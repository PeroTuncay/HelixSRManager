"""HelixSR Manager: Decky backend.

Installs HelixSR (https://github.com/lonewolf0622/HelixSR) into Steam games the way its README describes:

  * direct: the game's FSR 3.1 DLL is renamed to *.original.dll and HelixSR's DLL takes its name (same files and
    marker as the official helixsr-install, so either tool can undo the other's install);
  * OptiScaler: HelixSR goes into a HelixSR folder next to OptiScaler.ini (as amd_fidelityfx_dx12.dll and
    amd_fidelityfx_upscaler_dx12.dll), and OptiScaler.ini is pointed at it. The previous values are remembered and
    put back on revert. Optionally the FSR 4 DLL OptiScaler used so far stays selectable next to HelixSR.

It also downloads the HelixSR release and runs its one-time setup (which builds NVIDIA's DLSS network into the DLL on
this device), and reads helixsr.log / OptiScaler.log to show whether HelixSR actually runs.

Runs as the Deck user (no root flag): every file it touches lives in the user's home or Steam libraries.
"""
import asyncio
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import time
import zipfile
from pathlib import Path

import decky

HOME = Path(decky.DECKY_USER_HOME)
SETTINGS_FILE = Path(decky.DECKY_PLUGIN_SETTINGS_DIR) / "settings.json"
RUNTIME_DIR = Path(decky.DECKY_PLUGIN_RUNTIME_DIR)
SETUP_LOG = Path(decky.DECKY_PLUGIN_LOG_DIR) / "helixsr-setup.log"
SETUP_DIR = RUNTIME_DIR / "setup"
SETUP_STATUS = SETUP_DIR / "status.json"   # tag, pid, start time: survives plugin restarts
SETUP_RC = SETUP_DIR / "exit-code"         # written by the wrapper shell when the setup ends
SETUP_TMP = SETUP_DIR / "tmp"              # TMPDIR for the setup, so its work folder can be watched

RELEASES_API = "https://api.github.com/repos/lonewolf0622/HelixSR/releases?per_page=100"
RELEASES_CACHE = Path(decky.DECKY_PLUGIN_SETTINGS_DIR) / "releases.json"
LEGACY_TAG = "local"   # a release extracted straight into the base folder (plugin 0.1 layout)
SEPARATE_FILES = ("helixsr_weights.bin", "helixsr_kernels.pak")   # the network before 1.4.0

NAMES = ("amd_fidelityfx_upscaler_dx12.dll", "amd_fidelityfx_dx12.dll")
INSTALL_MARKER = "helixsr-install.json"        # the official installer's marker, kept compatible
OPTI_MARKER = "helixsr-decky-optiscaler.json"  # ours, inside the HelixSR folder made for OptiScaler
OPTI_SUBDIR = "HelixSR"
FSR4_COPY_NAME = "amd_fidelityfx_upscaler_dx12.amd.dll"
EMBED_MAGIC = b"HXSRNET1"
SCAN_DEPTH = 10

# Steam tools that show up as "games" in the library
TOOL_NAME = re.compile(r"^(Proton|Steam Linux Runtime|Steamworks Common|SteamVR|Steam Audio)", re.I)

NETWORK_RESOLUTIONS = ["auto", "full",
                       "QSSM Min", "QSSM Eco", "QSSM Light", "QSSM Balanced", "QSSM High", "QSSM Ultra",
                       "PRSM Light", "PRSM Quality", "PRSM Balanced", "PRSM Performance", "PRSM Ultra",
                       "PRSM Extreme"]
SHARPENING_MODES = ["off", "game", "override"]

# helixsr.log lines worth showing (taken from the strings HelixSR 1.4.1 logs)
LOG_HIGHLIGHTS = ("network running", "HelixSR ERROR", "NOT RUNNING", "summary:", "wave size:", "upscaler list:",
                  "create: max render", "forwarding", "output: network", "network files:")


# --- small helpers ---------------------------------------------------------------------------------------------------

def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(s):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, indent=1))


def base_dir():
    """Parent folder: every downloaded release gets its own subfolder named after its tag (v1.4.1, ...)."""
    return Path(load_settings().get("helix_dir") or HOME / "HelixSR")


def valid_tag(tag):
    return isinstance(tag, str) and (tag == LEGACY_TAG or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}", tag))


def version_dir(tag):
    if not valid_tag(tag):
        raise RuntimeError(f"Invalid release tag: {tag!r}")
    return base_dir() if tag == LEGACY_TAG else base_dir() / tag


def local_versions():
    """Downloaded releases: [{tag, dir, version, built, setup_script}], newest first."""
    out = []
    candidates = [(LEGACY_TAG, base_dir())]
    try:
        candidates += [(d.name, d) for d in base_dir().iterdir() if d.is_dir() and valid_tag(d.name)]
    except OSError:
        pass
    for tag, d in candidates:
        dll = d / "amd_fidelityfx_dx12.dll"
        if dll.exists() and is_helixsr(dll):
            out.append({"tag": tag, "dir": str(d), "version": helix_version(dll), "built": has_network(dll),
                        "setup_script": (d / "helixsr-setup.sh").exists()})
    out.sort(key=lambda v: version_key(v["version"] or v["tag"]), reverse=True)
    return out


def version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def active_tag():
    """The release games get. Chosen in the UI; defaults to the newest built (else newest downloaded) release."""
    tag = load_settings().get("active")
    if tag and valid_tag(tag):
        return tag
    local = local_versions()
    built = [v for v in local if v["built"]]
    return (built or local or [{"tag": None}])[0]["tag"]


def active_dir():
    tag = active_tag()
    if not tag:
        raise RuntimeError("No HelixSR release selected. Pick one and download it first.")
    return version_dir(tag)


def clean_env():
    """Environment for child processes: Decky's bundled Python sets LD_LIBRARY_PATH to its own libraries, which breaks
    system tools such as curl and Proton's wine."""
    env = dict(os.environ)
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    # PyInstaller's own variables (PYTHONHOME, PYTHONPATH, anything pointing into its _MEI folder) would make the
    # setup's Python load Decky's bundled standard library instead of its own
    for k in list(env):
        if k in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH") or k.startswith(("_MEI", "_PYI")) or "_MEI" in env[k]:
            env.pop(k)
    if orig:
        env["LD_LIBRARY_PATH"] = orig
    env["HOME"] = str(HOME)
    env["USER"] = decky.DECKY_USER
    env["XDG_DATA_HOME"] = str(HOME / ".local/share")
    env["PATH"] = env.get("PATH", "") + ":/usr/local/bin:/usr/bin:/bin"
    return env


def is_helixsr(path):
    try:
        with open(path, "rb") as f:
            return re.search(rb"HelixSR \d+\.\d+\.\d+", f.read(4 << 20)) is not None
    except OSError:
        return False


def helix_version(path):
    try:
        with open(path, "rb") as f:
            m = re.search(rb"\x00HelixSR (\d+\.\d+\.\d+)\x00", f.read(4 << 20))
        return m.group(1).decode() if m else None
    except OSError:
        return None


def has_network(path):
    """True when the setup appended the network to the DLL (or left it as separate files next to it)."""
    if has_embedded_network(path):
        return True
    return all((Path(path).parent / n).exists() for n in SEPARATE_FILES)


def original_of(target):
    return target.with_name(target.stem + ".original.dll")


def win_path(p):
    """Linux path -> the Windows path a Proton game sees (Z: is the Linux root)."""
    return "Z:" + os.path.realpath(p).replace("/", "\\")


def linux_path(value, base):
    """OptiScaler.ini path value -> Linux path (Z:\\... absolute, otherwise relative to the game folder)."""
    v = value.strip().strip('"')
    if not v or v.lower() == "auto":
        return None
    if re.match(r"^[zZ]:[\\/]", v):
        return Path("/" + v[3:].replace("\\", "/"))
    if re.match(r"^[a-zA-Z]:", v):
        return None   # another Wine drive: can't map it reliably
    return Path(base) / v.replace("\\", "/")


def file_info(p):
    try:
        st = p.stat()
        return {"path": str(p), "size": st.st_size, "mtime": st.st_mtime}
    except OSError:
        return None


# --- ini editing that keeps the file's own formatting -----------------------------------------------------------------

def ini_get(text, section, key):
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\s*\[(.+?)\]\s*$", line)
        if m:
            cur = m.group(1).strip().lower()
            continue
        if cur == section.lower():
            m = re.match(r"^\s*([^;#=\s][^=]*?)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1).lower() == key.lower():
                return m.group(2)
    return None


def ini_set(text, section, key, value):
    """Sets section/key to value (value None removes the key). Returns the new text."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    cur, sec_start, sec_end, found = None, None, None, False
    out = []
    for i, line in enumerate(lines):
        m = re.match(r"^\s*\[(.+?)\]\s*$", line)
        if m:
            if cur == section.lower():
                sec_end = len(out)
            cur = m.group(1).strip().lower()
            if cur == section.lower():
                sec_start = len(out) + 1
            out.append(line)
            continue
        if cur == section.lower():
            m = re.match(r"^(\s*)([^;#=\s][^=]*?)(\s*=\s*)(.*?)(\s*)$", line)
            if m and m.group(2).lower() == key.lower():
                found = True
                if value is not None:
                    out.append(f"{m.group(1)}{m.group(2)}{m.group(3)}{value}")
                continue
        out.append(line)
    if cur == section.lower():
        sec_end = len(out)
    if not found and value is not None:
        if sec_start is None:
            out += ["", f"[{section}]", f"{key}={value}"]
        else:
            # after the section's last non-empty line
            pos = sec_end
            while pos > sec_start and not out[pos - 1].strip():
                pos -= 1
            out.insert(pos, f"{key}={value}")
    return nl.join(out) + nl


def read_text(p):
    with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def write_text(p, text):
    tmp = Path(str(p) + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, p)


# --- Steam libraries and games ----------------------------------------------------------------------------------------

def steam_roots():
    return [HOME / ".local/share/Steam", HOME / ".steam/steam", HOME / ".steam/root",
            HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]


def steamapps_dirs():
    out, seen = [], set()
    for root in steam_roots():
        paths = [root]
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            text = vdf.read_text(errors="replace")
            paths += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
        for p in paths:
            sa = p / "steamapps"
            try:
                key = sa.resolve()
            except OSError:
                continue
            if sa.is_dir() and key not in seen:
                seen.add(key)
                out.append(sa)
    return out


def acf_field(text, name):
    m = re.search(rf'"{name}"\s+"([^"]*)"', text)
    return m.group(1) if m else None


def installed_games():
    games = {}
    for sa in steamapps_dirs():
        for acf in sa.glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(errors="replace")
            except OSError:
                continue
            appid, name, installdir = acf_field(text, "appid"), acf_field(text, "name"), acf_field(text, "installdir")
            if not (appid and name and installdir) or TOOL_NAME.match(name):
                continue
            folder = sa / "common" / installdir
            if folder.is_dir():
                games[appid] = {"appid": appid, "name": name, "path": str(folder)}
    return games


def game_roots():
    return [str((sa / "common").resolve()) for sa in steamapps_dirs()]


def inside_library(p):
    rp = os.path.realpath(p)
    return any(rp.startswith(r + os.sep) for r in game_roots())


def scan_folder(folder):
    """FSR DLLs and OptiScaler.ini folders under a game folder. Our own HelixSR folders for OptiScaler are skipped."""
    dlls, opti = [], []

    def walk(d, depth):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        names = {e.name.lower() for e in entries}
        if OPTI_MARKER.lower() in names:
            return
        if "optiscaler.ini" in names:
            opti.append(Path(d))
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                if depth > 0:
                    walk(e.path, depth - 1)
            elif e.name.lower() in NAMES:
                dlls.append(Path(e.path))

    walk(folder, SCAN_DEPTH)
    return dlls, opti


def direct_targets(dlls):
    """The official installer's choice: the upscaler DLL, or amd_fidelityfx_dx12.dll in games without one."""
    up = [d for d in dlls if d.name.lower() == NAMES[0]]
    return up or [d for d in dlls if d.name.lower() == NAMES[1]]


def near_optiscaler(target, opti_dirs):
    return any(target.parent == o or target.parent.parent == o for o in opti_dirs)


# --- the HelixSR download and setup -----------------------------------------------------------------------------------

def helix_state():
    tag = active_tag()
    local = local_versions()
    cur = next((v for v in local if v["tag"] == tag), None)
    return {"base": str(base_dir()), "active": tag, "versions": local,
            "dir": cur["dir"] if cur else (str(version_dir(tag)) if tag else None),
            "downloaded": cur is not None, "version": cur["version"] if cur else None,
            "built": bool(cur and cur["built"]), "setup_script": bool(cur and cur["setup_script"])}


def built_dll():
    d = active_dir()
    dll = d / "amd_fidelityfx_dx12.dll"
    if not dll.exists() or not is_helixsr(dll):
        raise RuntimeError(f"HelixSR {active_tag()} isn't downloaded yet ({d}).")
    if not has_network(dll):
        raise RuntimeError(f"HelixSR {active_tag()} has no network yet. Run its setup first "
                           f"(otherwise games show a red frame).")
    return dll


def copy_helix(dll, dest):
    """Copies HelixSR's DLL to dest. Releases before 1.4.0 keep the network in two files next to the DLL: those go
    along; for a release with the network inside the DLL, stale copies of them are removed."""
    shutil.copyfile(dll, dest)
    for name in SEPARATE_FILES:
        src, dst = dll.parent / name, dest.parent / name
        if src.exists() and not has_embedded_network(dll):
            shutil.copyfile(src, dst)
        elif dst.exists():
            dst.unlink()


def has_embedded_network(path):
    try:
        with open(path, "rb") as f:
            f.seek(-32, os.SEEK_END)
            return f.read(8) == EMBED_MAGIC
    except OSError:
        return False


def run_curl(args, timeout=300):
    p = subprocess.run(["curl", "-fsSL", "--retry", "3", *args], env=clean_env(), capture_output=True,
                       timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"Download failed: {p.stderr.decode(errors='replace')[-300:]}")
    return p.stdout


def fetch_releases():
    """Every published HelixSR release with a zip, newest first, from GitHub's release API (cached for offline use)."""
    data = json.loads(run_curl(["-H", "Accept: application/vnd.github+json", RELEASES_API], timeout=30))
    out = []
    for r in data:
        asset = next((a for a in r.get("assets", []) if a["name"].lower().endswith(".zip")), None)
        if r.get("draft") or not asset or not valid_tag(r.get("tag_name")):
            continue
        out.append({"tag": r["tag_name"], "name": asset["name"], "url": asset["browser_download_url"],
                    "size": asset.get("size"), "published_at": r.get("published_at"),
                    "prerelease": bool(r.get("prerelease"))})
    out.sort(key=lambda r: r["published_at"] or "", reverse=True)
    RELEASES_CACHE.parent.mkdir(parents=True, exist_ok=True)
    RELEASES_CACHE.write_text(json.dumps(out, indent=1))
    return out


def cached_releases():
    try:
        return json.loads(RELEASES_CACHE.read_text())
    except (OSError, ValueError):
        return []


def extract_release(zip_path, target):
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    with zipfile.ZipFile(zip_path) as z:
        members = z.infolist()
        tops = {m.filename.split("/", 1)[0] for m in members}
        strip = len(tops) == 1 and all("/" in m.filename for m in members)
        for m in members:
            name = m.filename.split("/", 1)[1] if strip else m.filename
            if not name or name.endswith("/"):
                continue
            dest = (target / name).resolve()
            if root not in dest.parents:
                raise RuntimeError(f"Unsafe path in the zip: {m.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            mode = (m.external_attr >> 16) & 0o777
            if mode:
                os.chmod(dest, mode)
            elif dest.suffix == ".sh" or dest.name == "launch_synth":
                os.chmod(dest, 0o755)


# --- helixsr.log / OptiScaler.log -------------------------------------------------------------------------------------

def tail_lines(p, n=400):
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            return f.read().decode("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def helix_log_check(folder):
    log = Path(folder) / "helixsr.log"
    info = file_info(log)
    if not info:
        return {"exists": False, "status": "none", "highlights": [], "tail": []}
    lines = tail_lines(log)
    text = "\n".join(lines)
    if "network running" in text and "HelixSR ERROR" not in text:
        status = "running"
    elif "HelixSR ERROR" in text or "NOT RUNNING" in text:
        status = "error"
    else:
        status = "loaded"
    hl = [l.strip() for l in lines if any(k in l for k in LOG_HIGHLIGHTS)]
    seen, uniq = set(), []
    for l in reversed(hl):
        k = re.sub(r"^\S*\s*\S*\s*", "", l) if l[:1].isdigit() else l
        if k not in seen:
            seen.add(k)
            uniq.append(l)
    return {"exists": True, "mtime": info["mtime"], "status": status, "highlights": list(reversed(uniq[:12])),
            "tail": lines[-15:]}


def opti_log_check(opti_dir):
    ini = opti_dir / "OptiScaler.ini"
    name = "OptiScaler.log"
    try:
        v = ini_get(read_text(ini), "Log", "LogFileName")
        if v and v.lower() != "auto":
            name = v.strip('"')
    except OSError:
        pass
    log = linux_path(name, opti_dir) or (opti_dir / name)
    info = file_info(log)
    if not info:
        return {"exists": False, "path": str(log), "lines": []}
    lines = [l.strip() for l in tail_lines(log, 4000)
             if re.search(r"helixsr|amd_fidelityfx|ffx.*upscaler|upscaler.*ffx", l, re.I)]
    return {"exists": True, "path": str(log), "mtime": info["mtime"], "lines": lines[-15:]}


def helix_ini_values(folder):
    ini = Path(folder) / "helixsr.ini"
    if not ini.exists():
        return {"exists": False, "network_resolution": "auto", "sharpening": "off", "upscaler_dll": ""}
    t = read_text(ini)
    return {"exists": True,
            "network_resolution": ini_get(t, "Upscaling", "NetworkResolution") or "auto",
            "sharpening": ini_get(t, "Sharpening", "Mode") or "off",
            "upscaler_dll": ini_get(t, "Forwarding", "UpscalerDll") or ""}


# --- OptiScaler -------------------------------------------------------------------------------------------------------

def opti_upscaler_value(text):
    """OptiScaler 0.9+ calls FSR 3.1/4 'ffx' for Dx12Upscaler, older versions 'fsr31'. Read it from the ini's comment."""
    m = re.search(r"\[Upscalers\](.*?)Dx12Upscaler\s*=", text, re.S | re.I)
    block = m.group(1) if m else ""
    return "fsr31" if ("fsr31" in block and not re.search(r"\bffx\b", block)) else "ffx"


def resolve_ffx(opti_dir, text, key, filename):
    """Which DLL OptiScaler uses for key now (FfxDx12Path / FfxDx12SRPath), if it can be found."""
    v = ini_get(text, "Libraries", key)
    p = linux_path(v, opti_dir) if v else None
    if p and p.exists():
        return p
    base = linux_path(ini_get(text, "Libraries", "OptiDllPath") or "", opti_dir)
    for c in [b / filename for b in (base, opti_dir / "OptiScaler", opti_dir) if b]:
        if c.exists():
            return c
    return None


def opti_state(opti_dir, game_folder):
    ini = opti_dir / "OptiScaler.ini"
    text = read_text(ini)
    sub = opti_dir / OPTI_SUBDIR
    marker = sub / OPTI_MARKER
    ffx = ini_get(text, "Libraries", "FfxDx12Path") or "auto"
    sr = ini_get(text, "Libraries", "FfxDx12SRPath") or "auto"
    sr_path = linux_path(sr, opti_dir)
    configured = marker.exists() and sr_path is not None and \
        os.path.realpath(sr_path) == os.path.realpath(sub / NAMES[0])
    current_sr = None if configured else resolve_ffx(opti_dir, text, "FfxDx12SRPath", NAMES[0])
    if current_sr and is_helixsr(current_sr):
        current_sr = None
    helix_files = sub / NAMES[1]
    return {
        "dir": str(opti_dir),
        "rel": os.path.relpath(opti_dir, game_folder),
        "configured": configured,
        "helix_folder_present": marker.exists(),
        "helix_version": helix_version(helix_files) if helix_files.exists() else None,
        "dx12_upscaler": ini_get(text, "Upscalers", "Dx12Upscaler") or "auto",
        "upscaler_index": ini_get(text, "FSR", "UpscalerIndex") or "auto",
        "ffx_path": ffx,
        "ffx_sr_path": sr,
        "current_sr_dll": file_info(current_sr) if current_sr else None,
        "kept_fsr4": (sub / FSR4_COPY_NAME).exists(),
        "log_to_file": (ini_get(text, "Log", "LogToFile") or "auto").lower() == "true",
    }


# --- install / uninstall ----------------------------------------------------------------------------------------------

def install_direct(target, dll, ini_src):
    orig = original_of(target)
    if is_helixsr(target):
        if not orig.exists():
            raise RuntimeError(f"{target.name} is HelixSR but the game's file is missing ({orig.name}); left as it is.")
        copy_helix(dll, target)   # update or switch to another release
        msg = f"Updated HelixSR in {target.parent.name}"
    else:
        if orig.exists():
            raise RuntimeError(f"{orig.name} already exists and {target.name} is not HelixSR (game updated?). "
                               f"Left as it is.")
        os.replace(target, orig)
        try:
            copy_helix(dll, target)
        except OSError:
            os.replace(orig, target)
            raise
        msg = f"Installed HelixSR (game's file kept as {orig.name})"
    if ini_src.exists() and not (target.parent / "helixsr.ini").exists():
        shutil.copyfile(ini_src, target.parent / "helixsr.ini")
    (target.parent / INSTALL_MARKER).write_text(
        json.dumps({"helixsr": helix_version(dll) or "?", "files": [target.name]}, indent=1) + "\n")
    return msg


def uninstall_direct(target, ini_src):
    orig = original_of(target)
    if not orig.exists():
        raise RuntimeError(f"No backup ({orig.name}) next to {target.name}: nothing to restore.")
    if target.exists() and not is_helixsr(target):
        raise RuntimeError(f"{target.name} is not HelixSR (the game may have been updated); {orig.name} left in place.")
    if target.exists():
        target.unlink()
    os.replace(orig, target)
    for extra in (INSTALL_MARKER, "helixsr.log", *SEPARATE_FILES):
        p = target.parent / extra
        if p.exists():
            p.unlink()
    user_ini = target.parent / "helixsr.ini"
    if user_ini.exists() and ini_src.exists() and user_ini.read_bytes() == ini_src.read_bytes():
        user_ini.unlink()   # an unchanged copy of ours; an edited one is kept
    return f"Restored the game's {target.name}"


def install_optiscaler(opti_dir, dll, ini_src, keep_fsr4):
    ini = opti_dir / "OptiScaler.ini"
    text = read_text(ini)
    sub = opti_dir / OPTI_SUBDIR
    marker = sub / OPTI_MARKER
    prev = json.loads(marker.read_text()).get("previous", {}) if marker.exists() else None

    # what OptiScaler used before we change anything (for FSR 4 next to HelixSR and frame generation)
    cur_sr = resolve_ffx(opti_dir, text, "FfxDx12SRPath", NAMES[0])
    cur_ffx = resolve_ffx(opti_dir, text, "FfxDx12Path", NAMES[1])
    if sub.exists() and not marker.exists():
        raise RuntimeError(f"{sub} exists but wasn't made by this plugin; rename or remove it first.")
    sub.mkdir(exist_ok=True)

    copy_helix(dll, sub / NAMES[1])
    copy_helix(dll, sub / NAMES[0])
    # frame generation: HelixSR forwards non-upscaling effects to amd_fidelityfx_dx12.original.dll next to it
    if cur_ffx and not is_helixsr(cur_ffx) and not (sub / "amd_fidelityfx_dx12.original.dll").exists():
        shutil.copyfile(cur_ffx, sub / "amd_fidelityfx_dx12.original.dll")
    if not (sub / "helixsr.ini").exists() and ini_src.exists():
        shutil.copyfile(ini_src, sub / "helixsr.ini")
    hini = sub / "helixsr.ini"
    htext = read_text(hini) if hini.exists() else ""
    if keep_fsr4 and cur_sr and not is_helixsr(cur_sr):
        shutil.copyfile(cur_sr, sub / FSR4_COPY_NAME)
        htext = ini_set(htext, "Forwarding", "UpscalerDll", FSR4_COPY_NAME)
    elif not keep_fsr4:
        if (sub / FSR4_COPY_NAME).exists():
            (sub / FSR4_COPY_NAME).unlink()
        htext = ini_set(htext, "Forwarding", "UpscalerDll", "")
    write_text(hini, htext)

    wanted = {
        ("Upscalers", "Dx12Upscaler"): opti_upscaler_value(text),
        ("Libraries", "FfxDx12Path"): win_path(sub / NAMES[1]),
        ("Libraries", "FfxDx12SRPath"): win_path(sub / NAMES[0]),
    }
    if ini_get(text, "FSR", "UpscalerIndex") is not None:
        wanted[("FSR", "UpscalerIndex")] = "0"   # HelixSR is the first upscaler its DLL lists
    if prev is None:
        prev = {f"{s}/{k}": ini_get(text, s, k) for (s, k) in wanted}
        backup = opti_dir / "OptiScaler.ini.helixsr-backup"
        if not backup.exists():
            shutil.copyfile(ini, backup)
    for (s, k), v in wanted.items():
        text = ini_set(text, s, k, v)
    write_text(ini, text)
    marker.write_text(json.dumps({"previous": prev, "set": {f"{s}/{k}": v for (s, k), v in wanted.items()},
                                  "helixsr": helix_version(dll)}, indent=1) + "\n")
    return "OptiScaler now uses HelixSR" + (" (FSR 4 stays selectable)" if (sub / FSR4_COPY_NAME).exists() else "")


def uninstall_optiscaler(opti_dir):
    ini = opti_dir / "OptiScaler.ini"
    sub = opti_dir / OPTI_SUBDIR
    marker = sub / OPTI_MARKER
    if not marker.exists():
        raise RuntimeError("HelixSR wasn't set up for OptiScaler here by this plugin.")
    data = json.loads(marker.read_text())
    text = read_text(ini)
    for sk, old in data.get("previous", {}).items():
        s, k = sk.split("/", 1)
        # only put back what still has our value (the user or a reinstall may have changed it since)
        if (ini_get(text, s, k) or "") == (data.get("set", {}).get(sk) or ""):
            text = ini_set(text, s, k, old)
    write_text(ini, text)
    shutil.rmtree(sub)
    return "OptiScaler is back to its previous upscaler"


# --- setup tracking ---------------------------------------------------------------------------------------------------
# HelixSR's setup prints one header line per step and keeps each step's tools quiet, so the log alone can't tell
# "working" from "stuck". Progress comes from the step headers, the shaders compiled so far in its work folder and the
# CPU time of its process tree (read from /proc).

PHASES = [  # (log pattern, progress %, label)
    (r"\[helixsr-setup\] system:", 1, "Checking the system"),
    (r"portable Python|python\.tar\.gz", 2, "Downloading Python + numpy (one time)"),
    (r"\[helixsr-setup\] python:", 5, "Preparing the shader compiler"),
    (r"dxc-win\.zip", 5, "Downloading the shader compiler (one time)"),
    (r"shader compiler:", 7, "Starting the build"),
    (r"downloading nvngx_dlss", 8, "Downloading NVIDIA's DLSS DLL"),
    (r"1/5 weights", 10, "Step 1/5: extracting weights"),
    (r"2/5 PTX", 14, "Step 2/5: extracting the network kernels"),
    (r"3/5 fragment tables", 18, "Step 3/5: fragment tables"),
    (r"4/5 kernel sources", 40, "Step 4/5: generating shader sources"),
    (r"5/5 shaders", 50, "Step 5/5: compiling shaders"),
    (r"HelixSR is ready|now carries the network", 100, "Finished"),
]
ERROR_PATTERN = re.compile(r"error|failed|traceback|no such file|not found|denied|mismatch|cannot|killed", re.I)


def proc_table():
    """{pid: (ppid, comm, cmdline, cpu_seconds incl. reaped children)} from /proc; empty where /proc doesn't exist."""
    table = {}
    try:
        tick = os.sysconf("SC_CLK_TCK")
        pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
    except (OSError, ValueError, AttributeError):
        return table
    for pid in pids:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            comm = stat[stat.index("(") + 1:stat.rindex(")")]
            f = stat[stat.rindex(")") + 2:].split()
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            table[pid] = (int(f[1]), comm, cmd, sum(int(x) for x in f[11:15]) / tick)
        except (OSError, ValueError, IndexError):
            continue
    return table


def setup_processes(table, root=None):
    """The setup's process tree: below root if given, else any process running HelixSR's setup (also one started by
    an older plugin version or by hand)."""
    if root is None:
        roots = [p for p, (_, _, cmd, _) in table.items()
                 if ("helixsr-setup.sh" in cmd or "helixsr_setup.py" in cmd) and
                 "helixsr" not in table.get(table[p][0], (0, "", "", 0))[2]]
    else:
        roots = [root] if root in table else []
    tree, todo = [], list(roots)
    while todo:
        p = todo.pop()
        tree.append(p)
        todo += [c for c, v in table.items() if v[0] == p]
    return tree


def short_cmd(cmd, comm):
    """'python3 trace_tables.py' instead of the full command line."""
    parts = cmd.split()
    if not parts:
        return comm
    names = [os.path.basename(x) for x in parts[:6] if not x.startswith("-")]
    script = next((n for n in names[1:] if re.search(r"\.(py|sh|exe)$", n)), "")
    return f"{names[0] if names else comm} {script}".strip()


def phase_of(lines):
    pct, label = 0, "Starting"
    for line in lines:
        for pat, p, l in PHASES:
            if re.search(pat, line) and p >= pct:
                pct, label = p, l
    return pct, label


def shader_progress():
    """(compiled, expected) shaders in the newest setup work folder, or None before step 5."""
    try:
        works = sorted(SETUP_TMP.glob("helixsr-setup-*"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    if not works:
        return None
    w = works[-1]
    sources = [p for p in (w / "kernels").rglob("*.hlsl") if not p.name.startswith("w64_")]
    done = len(list(w.glob("*.cso")))
    return (done, len(sources) * 2) if sources else None


def read_json(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def setup_status():
    st = read_json(SETUP_STATUS) or {}
    table = proc_table()
    tree = setup_processes(table, st.get("pid")) if st.get("pid") else []
    if not table and st.get("pid") and not SETUP_RC.exists():   # no /proc: just check the wrapper is alive
        try:
            os.kill(st["pid"], 0)
            tree = [st["pid"]]
        except OSError:
            pass
    adopted = False
    if not tree:
        stray = setup_processes(table)
        if stray and not SETUP_RC.exists():
            tree, adopted = stray, True   # e.g. started by plugin 0.2 or from Konsole
    try:
        rc = int(SETUP_RC.read_text().strip()) if SETUP_RC.exists() else None
    except ValueError:
        rc = None
    lines = tail_lines(SETUP_LOG, 2000) if SETUP_LOG.exists() else []
    pct, label = phase_of(lines)
    shaders = shader_progress() if tree else None
    if shaders and label.startswith("Step 5") and shaders[1]:
        pct = 50 + int(49 * min(1.0, shaders[0] / shaders[1]))
        label = f"Step 5/5: compiling shaders ({shaders[0]}/{shaders[1]})"
    tag = st.get("tag")
    built = bool(tag) and valid_tag(tag) and has_network(version_dir(tag) / "amd_fidelityfx_dx12.dll")
    if tree:
        state = "running"
    elif rc is not None:
        state = "succeeded" if rc == 0 and built else "failed"
    elif st.get("cancelled"):
        state = "cancelled"
    elif st:
        state = "stopped"
    else:
        state = "idle"
    log_info = file_info(SETUP_LOG)
    now = time.time()
    finished = SETUP_RC.stat().st_mtime if SETUP_RC.exists() else None
    return {
        "state": state, "tag": tag, "adopted": adopted, "rc": rc, "built": built,
        "started": st.get("started"), "finished": finished, "now": now,
        "progress": 100 if state == "succeeded" else pct, "phase": label,
        "log_age": (now - log_info["mtime"]) if log_info else None,
        "cpu_seconds": round(sum(table[p][3] for p in tree if p in table), 1),
        "processes": [short_cmd(table[p][2], table[p][1]) for p in tree if p in table
                      and not re.match(r"(bash|sh|wineserver|tee)\b", table[p][1])][:4],
        "errors": [l.strip() for l in lines if ERROR_PATTERN.search(l)][-6:],
        "tail": [l for l in lines if l.strip()][-8:],
        "log_path": str(SETUP_LOG),
    }


def which(name):
    return shutil.which(name, path=clean_env()["PATH"])


def setup_diagnostics():
    """What the setup depends on, checked the way helixsr-setup.sh looks for it."""
    data = HOME / ".local/share/HelixSR"
    out = []

    def add(name, ok, detail):
        out.append({"name": name, "ok": ok, "detail": detail})

    try:
        osr = dict(re.findall(r'^(\w+)="?([^"\n]*)"?', Path("/etc/os-release").read_text(), re.M))
        add("System", True, f"{osr.get('PRETTY_NAME', '?')} {osr.get('VERSION_ID', '')}".strip())
    except OSError:
        add("System", True, "unknown (no /etc/os-release)")
    for label, p in (("Free space (HelixSR folder)", base_dir()), ("Free space (home)", HOME)):
        try:
            q = p if p.exists() else p.parent
            free = shutil.disk_usage(q).free / 1e9
            add(label, free > 2, f"{free:.1f} GB free in {q}")
        except OSError as e:
            add(label, False, str(e))
    add("curl", bool(which("curl")), which("curl") or "missing: needed for downloads")
    try:
        p = subprocess.run(["curl", "-fsSI", "--max-time", "10", "https://github.com"], env=clean_env(),
                           capture_output=True, timeout=15)
        add("GitHub reachable", p.returncode == 0, "yes" if p.returncode == 0 else
            p.stderr.decode(errors="replace").strip()[-200:] or f"curl exit {p.returncode}")
    except (OSError, subprocess.TimeoutExpired) as e:
        add("GitHub reachable", False, str(e))
    wines = []
    for pat in (".local/share/Steam/steamapps/common/Proton*/files/bin/wine",
                ".steam/steam/steamapps/common/Proton*/files/bin/wine",
                ".local/share/Steam/compatibilitytools.d/*/files/bin/wine",
                ".steam/root/compatibilitytools.d/*/files/bin/wine"):
        wines += [str(w) for w in HOME.glob(pat) if os.access(w, os.X_OK)]
    add("Proton (runs the shader compiler)", bool(wines), wines[0] if wines else
        "no Proton found: install any Proton version in Steam (e.g. run a Windows game once)")
    py = data / "python/bin/python3"
    for label, exe in (("Portable Python", py), ("System Python", which("python3"))):
        if not exe or not Path(exe).exists():
            add(label, label == "System Python", "not installed" + (" (downloaded by the setup)" if
                                                                     label == "Portable Python" else ""))
            continue
        try:
            p = subprocess.run([str(exe), "-c", "import sys, numpy; print(sys.version.split()[0], numpy.__version__)"],
                               env=clean_env(), capture_output=True, timeout=30)
            ok = p.returncode == 0
            add(label, ok or label == "System Python",
                f"{exe}: Python/numpy {p.stdout.decode().strip()}" if ok else f"{exe}: no numpy")
        except (OSError, subprocess.TimeoutExpired) as e:
            add(label, False, f"{exe}: {e}")
    dxc = data / "dxc-win/bin/x64/dxc.exe"
    add("Shader compiler", True, f"{dxc}" if dxc.exists() else "not downloaded yet (the setup downloads it)")
    leaked = [k for k in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH") if k in os.environ]
    add("Decky environment", True, ("cleaned for the setup: " + ", ".join(leaked)) if leaked else "clean")
    st = setup_status()
    add("Setup", st["state"] in ("idle", "running", "succeeded"),
        f"{st['state']}" + (f", exit code {st['rc']}" if st["rc"] is not None else "") +
        (f", release {st['tag']}" if st["tag"] else ""))
    return out


def write_debug_report():
    st = setup_status()
    lines = [f"HelixSR Manager {getattr(decky, "DECKY_PLUGIN_VERSION", "?")} debug report, {time.ctime()}", "",
             "== Setup status", json.dumps(st, indent=1, default=str), "", "== Diagnostics"]
    lines += [f"[{'ok' if d['ok'] else 'PROBLEM'}] {d['name']}: {d['detail']}" for d in setup_diagnostics()]
    lines += ["", "== Downloaded releases", json.dumps(local_versions(), indent=1), "", f"== {SETUP_LOG}"]
    lines += tail_lines(SETUP_LOG, 3000) if SETUP_LOG.exists() else ["(no setup log)"]
    out = base_dir() / "helixsr-manager-debug.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return out



# --- clean ------------------------------------------------------------------------------------------------------------

# what a HelixSR release zip (1.2.0 - 1.4.1) and its setup put into a release folder
RELEASE_FILES = {"amd_fidelityfx_dx12.dll", "helixsr.ini", "helixsr_weights.bin", "helixsr_kernels.pak",
                 "helixsr_setup.json", "LICENSE", "LICENSE-APACHE-2.0", "README.md", "SOURCE.md",
                 "THIRD_PARTY_NOTICES.md", "helixsr-setup.sh", "helixsr-setup.bat", "helixsr-setup.ps1",
                 "helixsr-install.sh", "helixsr-install.bat", "helixsr.log"}


def tree_size(p):
    p = Path(p)
    if p.is_file() or p.is_symlink():
        return p.lstat().st_size
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def is_release_folder(d):
    return (d / "helixsr-setup.sh").exists() or (d / "setup" / "helixsr_setup.py").exists() or \
        ((d / "amd_fidelityfx_dx12.dll").exists() and is_helixsr(d / "amd_fidelityfx_dx12.dll"))


def clean_targets():
    """Everything the plugin and HelixSR's setup downloaded or built. Only things recognizably HelixSR's: the base
    folder may be one the user picked, so it is never removed as a whole while it holds anything else."""
    base = base_dir()
    out = []
    if base.is_dir():
        for d in base.iterdir():
            if d.is_dir() and valid_tag(d.name) and is_release_folder(d):
                out.append(d)
        if is_release_folder(base):   # the plugin 0.1 layout: a release extracted straight into the base folder
            out += [base / n for n in RELEASE_FILES if (base / n).exists()]
            if (base / "setup" / "helixsr_setup.py").exists():
                out.append(base / "setup")
        if (base / "helixsr-manager-debug.txt").exists():
            out.append(base / "helixsr-manager-debug.txt")
    data = HOME / ".local/share/HelixSR"   # the setup's portable Python, shader compiler and Wine prefix
    if data.is_dir():
        out.append(data)
    for p in (SETUP_TMP, *RUNTIME_DIR.glob("*.zip")):
        if Path(p).exists():
            out.append(Path(p))
    return out


def clean_all():
    targets = clean_targets()
    freed = sum(tree_size(p) for p in targets)
    for p in targets:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    base = base_dir()
    try:
        if base.is_dir() and not any(base.iterdir()):
            base.rmdir()
    except OSError:
        pass
    for f in (SETUP_STATUS, SETUP_RC):
        f.unlink(missing_ok=True)
    s = load_settings()
    s.pop("active", None)
    save_settings(s)
    return len(targets), freed



# --- plugin -----------------------------------------------------------------------------------------------------------

class Plugin:
    games_cache = {}

    async def _main(self):
        decky.logger.info("HelixSR Manager loaded")

    async def _unload(self):
        pass   # a running setup keeps going (it records its own result); the panel picks it up again

    # settings / HelixSR itself

    async def get_helix_state(self):
        s = helix_state()
        s["setup"] = await asyncio.to_thread(setup_status)
        return s

    async def set_helix_dir(self, path):
        p = Path(os.path.expanduser(path.strip())) if path.strip() else HOME / "HelixSR"
        s = load_settings()
        s["helix_dir"] = str(p)
        s.pop("active", None)
        save_settings(s)
        return await self.get_helix_state()

    async def list_releases(self):
        """Releases from GitHub; on failure the last list fetched, flagged offline."""
        try:
            return {"ok": True, "offline": False, "releases": await asyncio.to_thread(fetch_releases)}
        except Exception as e:
            return {"ok": True, "offline": True, "error": str(e), "releases": cached_releases()}

    async def set_active(self, tag):
        if not valid_tag(tag):
            return {"ok": False, "error": "Invalid release."}
        s = load_settings()
        s["active"] = tag
        save_settings(s)
        return {"ok": True}

    async def download_release(self, tag):
        try:
            def work():
                rel = next((r for r in cached_releases() if r["tag"] == tag), None) or \
                    next((r for r in fetch_releases() if r["tag"] == tag), None)
                if not rel:
                    raise RuntimeError(f"Release {tag} not found on GitHub.")
                d = version_dir(tag)
                RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
                zp = RUNTIME_DIR / rel["name"]
                run_curl(["-o", str(zp), rel["url"]], timeout=600)
                if d.exists() and d != base_dir():
                    shutil.rmtree(d)   # a clean folder per release (a re-download means a fresh setup anyway)
                extract_release(zp, d)
                zp.unlink()
                return d
            d = await asyncio.to_thread(work)
            return {"ok": True, "message": f"HelixSR {tag} downloaded to {d}. Now run its setup."}
        except Exception as e:
            decky.logger.exception("download")
            return {"ok": False, "error": str(e)}

    async def delete_release(self, tag):
        try:
            d = version_dir(tag)
            st = setup_status()
            if st["state"] == "running" and st["tag"] in (tag, None):
                raise RuntimeError("Its setup is running.")
            if tag == LEGACY_TAG or not d.is_dir() or base_dir().resolve() not in d.resolve().parents:
                raise RuntimeError("Only release folders this plugin downloaded can be deleted here.")
            await asyncio.to_thread(shutil.rmtree, d)
            return {"ok": True, "message": f"Deleted HelixSR {tag}. Games that have it installed keep working."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def start_setup(self, tag):
        if (await asyncio.to_thread(setup_status))["state"] == "running":
            return {"ok": False, "error": "A setup is already running."}
        try:
            d = version_dir(tag)
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}
        script = d / "helixsr-setup.sh"
        if not script.exists():
            return {"ok": False, "error": f"helixsr-setup.sh not found. Download HelixSR {tag} first."}
        SETUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        SETUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(SETUP_TMP, ignore_errors=True)
        SETUP_TMP.mkdir()
        if SETUP_RC.exists():
            SETUP_RC.unlink()
        env = clean_env()
        env["TMPDIR"] = str(SETUP_TMP)
        with open(SETUP_LOG, "wb") as log:
            log.write(f"[decky] {time.ctime()}: running {script} {d} --yes\n".encode())
            log.flush()
            # A wrapper shell records the exit code in a file, so the result is known even if Decky restarts the
            # plugin meanwhile. --yes: the user agreed to NVIDIA's DLSS download in the confirmation dialog.
            proc = subprocess.Popen(
                ["bash", "-c", 'bash "$0" "$1" --yes; echo $? > "$2"', str(script), str(d), str(SETUP_RC)],
                cwd=str(d), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
        SETUP_STATUS.write_text(json.dumps({"tag": tag, "pid": proc.pid, "started": time.time()}))
        asyncio.get_event_loop().create_task(self._wait_setup(proc))
        return {"ok": True}

    async def _wait_setup(self, proc):
        await asyncio.to_thread(proc.wait)
        st = await asyncio.to_thread(setup_status)
        await decky.emit("setup_done", st["state"] == "succeeded")

    async def cancel_setup(self):
        st = read_json(SETUP_STATUS) or {}
        table = proc_table()
        tree = setup_processes(table, st.get("pid")) if st.get("pid") else []
        tree = tree or setup_processes(table)
        if st:
            st["cancelled"] = True
            SETUP_STATUS.write_text(json.dumps(st))
        for pid in reversed(tree):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        if st.get("pid"):
            try:
                os.killpg(st["pid"], signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        return True

    async def clean_preview(self):
        def work():
            t = clean_targets()
            return {"items": [str(p) for p in t], "bytes": sum(tree_size(p) for p in t)}
        return await asyncio.to_thread(work)

    async def clean(self):
        if (await asyncio.to_thread(setup_status))["state"] == "running":
            return {"ok": False, "error": "A setup is running. Cancel it first."}
        try:
            n, freed = await asyncio.to_thread(clean_all)
            return {"ok": True, "message": f"Removed {n} item(s), freed {freed / 1048576:.0f} MB. "
                                           f"Games with HelixSR installed keep working."}
        except Exception as e:
            decky.logger.exception("clean")
            return {"ok": False, "error": str(e)}

    async def get_setup_log(self):
        return {"path": str(SETUP_LOG), "lines": tail_lines(SETUP_LOG, 400) if SETUP_LOG.exists() else []}

    async def run_diagnostics(self):
        return await asyncio.to_thread(setup_diagnostics)

    async def save_debug_report(self):
        try:
            return {"ok": True, "message": f"Saved to {await asyncio.to_thread(write_debug_report)}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # games

    async def list_games(self):
        def work():
            out = []
            for g in installed_games().values():
                dlls, opti = scan_folder(g["path"])
                targets = direct_targets(dlls)
                installed = any(is_helixsr(t) and original_of(t).exists() for t in targets) or \
                    any((o / OPTI_SUBDIR / OPTI_MARKER).exists() for o in opti)
                out.append({**g, "fsr": bool(targets), "optiscaler": bool(opti), "helixsr": installed})
            out.sort(key=lambda x: x["name"].lower())
            return out
        games = await asyncio.to_thread(work)
        self.games_cache = {g["appid"]: g for g in games}
        return games

    def _game(self, appid):
        g = self.games_cache.get(appid) or installed_games().get(appid)
        if not g:
            raise RuntimeError("Game not found. Refresh the list.")
        return g

    async def get_game(self, appid):
        try:
            def work():
                g = self._game(appid)
                folder = Path(g["path"])
                dlls, opti = scan_folder(folder)
                direct = []
                for t in direct_targets(dlls):
                    orig = original_of(t)
                    helix = is_helixsr(t)
                    state = ("helixsr" if orig.exists() else "helixsr_no_backup") if helix else \
                        ("conflict" if orig.exists() else "original")
                    v = helix_version(t) if helix else None
                    direct.append({
                        "path": str(t), "rel": os.path.relpath(t, folder), "state": state, "helix_version": v,
                        "near_optiscaler": near_optiscaler(t, opti), "size": t.stat().st_size,
                        "check": helix_log_check(t.parent), "ini": helix_ini_values(t.parent),
                    })
                optis = []
                for o in opti:
                    st = opti_state(o, folder)
                    sub = o / OPTI_SUBDIR
                    st["check"] = helix_log_check(sub)
                    st["opti_log"] = opti_log_check(o)
                    st["ini"] = helix_ini_values(sub)
                    st["helix_folder"] = str(sub)
                    optis.append(st)
                return {"ok": True, **g, "direct": direct, "optiscaler": optis}
            return await asyncio.to_thread(work)
        except Exception as e:
            decky.logger.exception("get_game")
            return {"ok": False, "error": str(e)}

    def _checked_target(self, path):
        t = Path(path)
        if t.name.lower() not in NAMES or not inside_library(t):
            raise RuntimeError("Not an FSR DLL inside a Steam library.")
        return t

    def _checked_opti(self, path):
        o = Path(path)
        if not (o / "OptiScaler.ini").exists() or not inside_library(o / "OptiScaler.ini"):
            raise RuntimeError("No OptiScaler.ini in that folder.")
        return o

    async def _do(self, fn):
        try:
            return {"ok": True, "message": await asyncio.to_thread(fn)}
        except Exception as e:
            decky.logger.exception("action")
            return {"ok": False, "error": str(e)}

    async def install_direct(self, path):
        return await self._do(lambda: install_direct(self._checked_target(path), built_dll(),
                                                     active_dir() / "helixsr.ini"))

    async def uninstall_direct(self, path):
        return await self._do(lambda: uninstall_direct(self._checked_target(path), active_dir() / "helixsr.ini"))

    async def install_optiscaler(self, path, keep_fsr4):
        return await self._do(lambda: install_optiscaler(self._checked_opti(path), built_dll(),
                                                         active_dir() / "helixsr.ini", bool(keep_fsr4)))

    async def uninstall_optiscaler(self, path):
        return await self._do(lambda: uninstall_optiscaler(self._checked_opti(path)))

    async def set_opti_logging(self, path, on):
        def work():
            o = self._checked_opti(path)
            ini = o / "OptiScaler.ini"
            write_text(ini, ini_set(read_text(ini), "Log", "LogToFile", "true" if on else "auto"))
            return "OptiScaler log file " + ("on (OptiScaler.log next to the game)" if on else "back to default")
        return await self._do(work)

    async def set_helix_option(self, folder, option, value):
        def work():
            f = Path(folder)
            if not inside_library(f / "helixsr.ini") or not any(is_helixsr(f / n) for n in NAMES):
                raise RuntimeError("No HelixSR install in that folder.")
            ini = f / "helixsr.ini"
            if not ini.exists():
                src = active_dir() / "helixsr.ini"
                if src.exists():
                    shutil.copyfile(src, ini)
                else:
                    ini.write_text("")
            text = read_text(ini)
            if option == "network_resolution" and value in NETWORK_RESOLUTIONS:
                text = ini_set(text, "Upscaling", "NetworkResolution", value)
            elif option == "sharpening" and value in SHARPENING_MODES:
                text = ini_set(text, "Sharpening", "Mode", value)
            else:
                raise RuntimeError("Unknown option.")
            write_text(ini, text)
            return "Saved. Restart the game to apply."
        return await self._do(work)
