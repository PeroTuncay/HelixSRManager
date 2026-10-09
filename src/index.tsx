import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  showModal,
  staticClasses,
} from "@decky/ui";
import { addEventListener, callable, definePlugin, removeEventListener, toaster } from "@decky/api";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FaDna } from "react-icons/fa";

// --- backend types and calls -------------------------------------------------------------------------------------------

type Result = { ok: boolean; message?: string; error?: string };

type LocalVersion = { tag: string; dir: string; version: string | null; built: boolean; setup_script: boolean };

type Release = {
  tag: string;
  name: string;
  url: string;
  size: number | null;
  published_at: string | null;
  prerelease: boolean;
};

type HelixState = {
  base: string;
  active: string | null;
  versions: LocalVersion[];
  dir: string | null;
  downloaded: boolean;
  version: string | null;
  built: boolean;
  setup_script: boolean;
  setup_running: boolean;
  setup_tag: string | null;
  setup_rc: number | null;
  setup_log: string[];
};

type Check = {
  exists: boolean;
  status: "running" | "error" | "loaded" | "none";
  mtime?: number;
  highlights: string[];
  tail: string[];
};

type IniValues = { exists: boolean; network_resolution: string; sharpening: string; upscaler_dll: string };

type DirectTarget = {
  path: string;
  rel: string;
  state: "original" | "helixsr" | "helixsr_no_backup" | "conflict";
  helix_version: string | null;
  near_optiscaler: boolean;
  size: number;
  check: Check;
  ini: IniValues;
};

type OptiTarget = {
  dir: string;
  rel: string;
  configured: boolean;
  helix_folder_present: boolean;
  helix_version: string | null;
  dx12_upscaler: string;
  upscaler_index: string;
  ffx_path: string;
  ffx_sr_path: string;
  current_sr_dll: { path: string; size: number } | null;
  kept_fsr4: boolean;
  log_to_file: boolean;
  check: Check;
  opti_log: { exists: boolean; path: string; mtime?: number; lines: string[] };
  ini: IniValues;
  helix_folder: string;
};

type Active = { ready: boolean; tag: string | null; version: string | null };

type GameSummary = { appid: string; name: string; path: string; fsr: boolean; optiscaler: boolean; helixsr: boolean };
type GameDetail = Result & {
  appid: string;
  name: string;
  path: string;
  direct: DirectTarget[];
  optiscaler: OptiTarget[];
};

const getHelixState = callable<[], HelixState>("get_helix_state");
const setHelixDir = callable<[path: string], HelixState>("set_helix_dir");
const listReleases = callable<[], Result & { offline: boolean; releases: Release[] }>("list_releases");
const setActive = callable<[tag: string], Result>("set_active");
const downloadRelease = callable<[tag: string], Result>("download_release");
const deleteRelease = callable<[tag: string], Result>("delete_release");
const startSetup = callable<[tag: string], Result>("start_setup");
const cancelSetup = callable<[], boolean>("cancel_setup");
const listGames = callable<[], GameSummary[]>("list_games");
const getGame = callable<[appid: string], GameDetail>("get_game");
const installDirect = callable<[path: string], Result>("install_direct");
const uninstallDirect = callable<[path: string], Result>("uninstall_direct");
const installOptiscaler = callable<[path: string, keepFsr4: boolean], Result>("install_optiscaler");
const uninstallOptiscaler = callable<[path: string], Result>("uninstall_optiscaler");
const setOptiLogging = callable<[path: string, on: boolean], Result>("set_opti_logging");
const setHelixOption = callable<[folder: string, option: string, value: string], Result>("set_helix_option");

const NETWORK_RESOLUTIONS = [
  "auto", "full",
  "QSSM Min", "QSSM Eco", "QSSM Light", "QSSM Balanced", "QSSM High", "QSSM Ultra",
  "PRSM Light", "PRSM Quality", "PRSM Balanced", "PRSM Performance", "PRSM Ultra", "PRSM Extreme",
];
const SHARPENING = ["off", "game", "override"];

// --- small UI helpers --------------------------------------------------------------------------------------------------

const small: React.CSSProperties = { fontSize: 12, lineHeight: 1.35, color: "#b8bcbf", wordBreak: "break-word" };
const mono: React.CSSProperties = {
  fontFamily: "monospace", fontSize: 10, lineHeight: 1.35, color: "#c9ced1", wordBreak: "break-all",
  whiteSpace: "pre-wrap", background: "rgba(0,0,0,0.25)", padding: 6, borderRadius: 3,
};
const STATUS: Record<Check["status"], { color: string; text: string }> = {
  running: { color: "#5ba32b", text: "HelixSR network running" },
  error: { color: "#d94126", text: "HelixSR loaded, but the network is NOT running" },
  loaded: { color: "#e5a50a", text: "HelixSR loaded, no upscaling yet: select FSR in the game / FFX in OptiScaler" },
  none: { color: "#8b929a", text: "Not loaded yet: start the game and select FSR" },
};

function toast(r: Result, title = "HelixSR") {
  toaster.toast({ title, body: r.ok ? r.message ?? "Done" : r.error ?? "Failed" });
}

function storage(key: string, fallback: string) {
  try {
    return localStorage.getItem(`helixsr-manager:${key}`) ?? fallback;
  } catch {
    return fallback;
  }
}
function store(key: string, value: string) {
  try {
    localStorage.setItem(`helixsr-manager:${key}`, value);
  } catch {
    /* ignore */
  }
}

function notReady(a: Active) {
  if (!a.tag) return "Select a HelixSR release first";
  return a.version ? `Run the setup for ${a.tag} first` : `Download ${a.tag} first`;
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <PanelSectionRow>
      <div style={small}>{children}</div>
    </PanelSectionRow>
  );
}

// --- HelixSR download + setup ------------------------------------------------------------------------------------------

function FolderModal({ initial, closeModal, onSave }: { initial: string; closeModal?: () => void; onSave: (v: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <ConfirmModal
      strTitle="HelixSR folder"
      strDescription="Each downloaded release gets its own subfolder here (v1.4.1, ...), built by its setup. Leave empty for ~/HelixSR."
      strOKButtonText="Save"
      closeModal={closeModal}
      onOK={() => onSave(value)}
    >
      <TextField value={value} onChange={(e) => setValue(e.target.value)} />
    </ConfirmModal>
  );
}

function HelixSection({ state, refresh }: { state: HelixState | null; refresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const [releases, setReleases] = useState<Release[] | null>(null);
  const [offline, setOffline] = useState(false);

  const loadReleases = useCallback(async () => {
    const r = await listReleases();
    setReleases(r.releases);
    setOffline(r.offline);
  }, []);

  useEffect(() => {
    loadReleases();
  }, [loadReleases]);

  useEffect(() => {
    if (!state?.setup_running) return;
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [state?.setup_running, refresh]);

  if (!state) return <PanelSection title="HelixSR"><Note>Loading…</Note></PanelSection>;

  const latest = releases?.find((r) => !r.prerelease)?.tag;
  const local = new Map(state.versions.map((v) => [v.tag, v]));
  // every release on GitHub, plus downloaded ones GitHub no longer lists (or a hand-extracted one)
  const tags = [...(releases ?? []).map((r) => r.tag), ...state.versions.map((v) => v.tag)].filter(
    (t, i, all) => all.indexOf(t) === i,
  );
  if (state.active && !tags.includes(state.active)) tags.unshift(state.active);
  const label = (tag: string) => {
    const v = local.get(tag);
    const name = tag === "local" ? `${v?.version ?? "?"} (in ${state.base})` : tag;
    const parts = [name];
    if (tag === latest) parts.push("latest");
    if (releases?.find((r) => r.tag === tag)?.prerelease) parts.push("pre-release");
    parts.push(v ? (v.built ? "✓ ready" : "setup needed") : "not downloaded");
    return parts.join(" · ");
  };

  const tag = state.active;
  const rel = releases?.find((r) => r.tag === tag);
  const setupHere = state.setup_running && state.setup_tag === tag;

  const choose = async (t: string) => {
    await setActive(t);
    refresh();
  };

  const download = async () => {
    if (!tag) return;
    setBusy(true);
    toast(await downloadRelease(tag));
    setBusy(false);
    refresh();
  };

  const remove = () =>
    tag &&
    showModal(
      <ConfirmModal
        strTitle={`Delete HelixSR ${tag}?`}
        strDescription={`Removes ${state.dir}, including its built network. Games that have this version installed keep working.`}
        strOKButtonText="Delete"
        onOK={async () => {
          toast(await deleteRelease(tag));
          refresh();
        }}
      />,
    );

  const setup = () =>
    tag &&
    showModal(
      <ConfirmModal
        strTitle={`Build HelixSR ${tag}`}
        strDescription={
          "HelixSR's setup downloads NVIDIA's DLSS 310.7.0 DLL from NVIDIA's GitHub (NVIDIA's license applies), " +
          "plus a portable Python with numpy and Microsoft's shader compiler, builds the network into the HelixSR DLL " +
          "and deletes NVIDIA's DLL again. Takes about 4-5 minutes; keep the Deck awake and plugged in. " +
          "The resulting DLL contains NVIDIA's network: it is for this device only, don't share it."
        }
        strOKButtonText="Download and build"
        onOK={async () => {
          const r = await startSetup(tag);
          if (!r.ok) toast(r);
          refresh();
        }}
      />,
    );

  return (
    <PanelSection title="HelixSR">
      <PanelSectionRow>
        {releases === null && tags.length === 0 ? (
          <Field label="Loading releases from GitHub…" />
        ) : (
          <DropdownItem
            label="Release to install"
            rgOptions={tags.map((t) => ({ data: t, label: label(t) }))}
            selectedOption={tag ?? undefined}
            strDefaultLabel="Select a release"
            disabled={state.setup_running}
            onChange={(o) => choose(o.data)}
          />
        )}
      </PanelSectionRow>
      <Note>
        {tag && (
          <div style={{ color: state.built ? "#5ba32b" : "#e5a50a", fontWeight: 600 }}>
            {state.built
              ? `HelixSR ${state.version}: ready to install`
              : state.downloaded
                ? `HelixSR ${state.version}: run its setup before installing`
                : `HelixSR ${tag}: not downloaded yet`}
          </div>
        )}
        {rel?.published_at && <div>Published {new Date(rel.published_at).toLocaleDateString()}</div>}
        {latest && tag && tag !== latest && <div style={{ color: "#e5a50a" }}>Newer release available: {latest}</div>}
        {offline && <div>GitHub not reachable: showing the last known releases.</div>}
        <div>{state.base}</div>
      </Note>
      {tag && rel && !state.setup_running && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={download}>
            {busy ? "Downloading…" : state.downloaded ? `Download ${tag} again` : `Download ${tag}`}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {state.setup_script && !state.setup_running && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={setup}>
            {state.built ? "Run setup again" : "Run setup (build network)"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {state.setup_running && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={async () => { await cancelSetup(); refresh(); }}>
            Cancel setup of {state.setup_tag}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {(setupHere || (state.setup_rc !== null && state.setup_rc !== 0 && state.setup_tag === tag)) &&
        state.setup_log.length > 0 && (
          <PanelSectionRow>
            <div style={mono}>
              {state.setup_running ? "Setup running…\n" : `Setup failed (exit ${state.setup_rc})\n`}
              {state.setup_log.join("\n")}
            </div>
          </PanelSectionRow>
        )}
      {state.downloaded && tag !== "local" && !setupHere && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={remove}>
            Delete {tag} from the Deck
          </ButtonItem>
        </PanelSectionRow>
      )}
      {!state.setup_running && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={loadReleases}>
            Check GitHub for releases
          </ButtonItem>
        </PanelSectionRow>
      )}
      {!state.setup_running && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() =>
              showModal(
                <FolderModal
                  initial={state.base}
                  onSave={async (v) => {
                    await setHelixDir(v);
                    refresh();
                  }}
                />,
              )
            }
          >
            Change HelixSR folder
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

// --- per-install pieces ------------------------------------------------------------------------------------------------

function CheckBlock({ check }: { check: Check }) {
  const s = STATUS[check.status];
  return (
    <>
      <PanelSectionRow>
        <div style={small}>
          <span style={{ color: s.color, fontWeight: 600 }}>● {s.text}</span>
          {check.mtime && <div>helixsr.log written {new Date(check.mtime * 1000).toLocaleString()}</div>}
        </div>
      </PanelSectionRow>
      {check.highlights.length > 0 && (
        <PanelSectionRow>
          <div style={mono}>{check.highlights.join("\n")}</div>
        </PanelSectionRow>
      )}
    </>
  );
}

function IniControls({ folder, ini, onChange }: { folder: string; ini: IniValues; onChange: () => void }) {
  const save = async (option: string, value: string) => {
    toast(await setHelixOption(folder, option, value));
    onChange();
  };
  return (
    <>
      <PanelSectionRow>
        <DropdownItem
          label="Network resolution"
          description="QSSM: sharper, slower. PRSM: faster, softer."
          rgOptions={NETWORK_RESOLUTIONS.map((v) => ({ data: v, label: v }))}
          selectedOption={ini.network_resolution}
          onChange={(o) => save("network_resolution", o.data)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label="Sharpening"
          rgOptions={SHARPENING.map((v) => ({ data: v, label: v }))}
          selectedOption={ini.sharpening}
          onChange={(o) => save("sharpening", o.data)}
        />
      </PanelSectionRow>
    </>
  );
}

function DirectSection({ items, active, reload }: { items: DirectTarget[]; active: Active; reload: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  if (items.length === 0) return null;

  const run = async (path: string, fn: () => Promise<Result>) => {
    setBusy(path);
    toast(await fn());
    setBusy(null);
    reload();
  };

  return (
    <PanelSection title="Replace the game's FSR 3.1">
      {items.map((t) => {
        const installed = t.state === "helixsr";
        const differs = installed && !!active.version && t.helix_version !== active.version;
        return (
          <div key={t.path}>
            <Note>
              <div style={{ fontWeight: 600 }}>{t.rel}</div>
              <div>
                {t.state === "original" && "Game's FSR DLL"}
                {installed && `HelixSR ${t.helix_version ?? ""} installed`}
                {t.state === "helixsr_no_backup" && "HelixSR without a backup of the game's file"}
                {t.state === "conflict" && "A backup exists but this file is not HelixSR (game updated?)"}
              </div>
              {t.near_optiscaler && !installed && (
                <div style={{ color: "#e5a50a" }}>
                  This DLL sits next to OptiScaler and is probably OptiScaler's own FSR DLL. Use the OptiScaler section below instead.
                </div>
              )}
            </Note>
            {t.state === "original" && (
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={!active.ready || busy !== null} onClick={() => run(t.path, () => installDirect(t.path))}>
                  {active.ready ? `Install HelixSR ${active.version} here` : notReady(active)}
                </ButtonItem>
              </PanelSectionRow>
            )}
            {differs && active.ready && (
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={busy !== null} onClick={() => run(t.path, () => installDirect(t.path))}>
                  Switch to HelixSR {active.version}
                </ButtonItem>
              </PanelSectionRow>
            )}
            {(installed || t.state === "conflict") && (
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={busy !== null} onClick={() => run(t.path, () => uninstallDirect(t.path))}>
                  Restore the game's FSR
                </ButtonItem>
              </PanelSectionRow>
            )}
            {installed && (
              <>
                <CheckBlock check={t.check} />
                <IniControls folder={t.path.replace(/\/[^/]+$/, "")} ini={t.ini} onChange={reload} />
              </>
            )}
          </div>
        );
      })}
    </PanelSection>
  );
}

function OptiItem({ o, active, reload }: { o: OptiTarget; active: Active; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [keepFsr4, setKeepFsr4] = useState(true);

  const run = async (fn: () => Promise<Result>) => {
    setBusy(true);
    toast(await fn(), "OptiScaler");
    setBusy(false);
    reload();
  };

  const sizeMb = o.current_sr_dll ? (o.current_sr_dll.size / 1048576).toFixed(1) : null;

  return (
    <>
      <Note>
        <div style={{ fontWeight: 600 }}>{o.rel === "." ? "Game folder" : o.rel}</div>
        <div>
          {o.configured
            ? `Uses HelixSR ${o.helix_version ?? ""}${o.kept_fsr4 ? " (FSR 4 selectable next to it)" : ""}`
            : o.helix_folder_present
              ? "HelixSR folder present, but OptiScaler.ini no longer points to it (OptiScaler reinstalled?)"
              : `Dx12Upscaler=${o.dx12_upscaler}, FfxDx12SRPath=${o.ffx_sr_path}`}
        </div>
        {!o.configured && o.current_sr_dll && (
          <div>Current FSR upscaler DLL: {o.current_sr_dll.path.split("/").slice(-2).join("/")} ({sizeMb} MB)</div>
        )}
      </Note>
      {!o.configured && o.current_sr_dll && (
        <PanelSectionRow>
          <ToggleField
            label="Keep current FSR selectable"
            description="Your FSR 4 DLL stays in OptiScaler's FFX Upscaler list after HelixSR, for comparing."
            checked={keepFsr4}
            onChange={setKeepFsr4}
          />
        </PanelSectionRow>
      )}
      {!o.configured ? (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!active.ready || busy} onClick={() => run(() => installOptiscaler(o.dir, keepFsr4 && !!o.current_sr_dll))}>
            {active.ready
              ? o.helix_folder_present
                ? `Point OptiScaler at HelixSR ${active.version} again`
                : `Use HelixSR ${active.version} in OptiScaler`
              : notReady(active)}
          </ButtonItem>
        </PanelSectionRow>
      ) : (
        <>
          {active.ready && o.helix_version !== active.version && (
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy} onClick={() => run(() => installOptiscaler(o.dir, o.kept_fsr4))}>
                Switch to HelixSR {active.version}
              </ButtonItem>
            </PanelSectionRow>
          )}
        </>
      )}
      {o.helix_folder_present && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => run(() => uninstallOptiscaler(o.dir))}>
            Revert OptiScaler to previous upscaler
          </ButtonItem>
        </PanelSectionRow>
      )}
      {o.configured && (
        <>
          <CheckBlock check={o.check} />
          <Note>
            In game, open the OptiScaler menu: Upscalers → FFX Upscaler should read "FSR HelixSR (3.1.5)". A red frame
            around the picture means the network isn't built.
          </Note>
          <PanelSectionRow>
            <ToggleField
              label="OptiScaler log file"
              description="Writes OptiScaler.log so its lines about HelixSR show up below."
              checked={o.log_to_file}
              disabled={busy}
              onChange={(on) => run(() => setOptiLogging(o.dir, on))}
            />
          </PanelSectionRow>
          {o.opti_log.exists && (
            <PanelSectionRow>
              <div style={mono}>
                {o.opti_log.lines.length > 0 ? o.opti_log.lines.join("\n") : "OptiScaler.log has no lines about HelixSR / FFX yet."}
              </div>
            </PanelSectionRow>
          )}
          <IniControls folder={o.helix_folder} ini={o.ini} onChange={reload} />
        </>
      )}
    </>
  );
}

function GameSection({ appid, active }: { appid: string; active: Active }) {
  const [game, setGame] = useState<GameDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setGame(await getGame(appid));
    setLoading(false);
  }, [appid]);

  useEffect(() => {
    setGame(null);
    reload();
  }, [reload]);

  if (!game) return <PanelSection title="Game"><Note>Scanning…</Note></PanelSection>;
  if (!game.ok) return <PanelSection title="Game"><Note>{game.error}</Note></PanelSection>;

  return (
    <>
      {game.direct.length === 0 && game.optiscaler.length === 0 && (
        <PanelSection title={game.name}>
          <Note>
            No FSR 3.1 DLL (amd_fidelityfx_upscaler_dx12.dll / amd_fidelityfx_dx12.dll) and no OptiScaler.ini found in
            this game. Install OptiScaler for it first to use HelixSR in DLSS/XeSS/FSR 2 games (DirectX 12 only).
          </Note>
        </PanelSection>
      )}
      <DirectSection items={game.direct} active={active} reload={reload} />
      {game.optiscaler.length > 0 && (
        <PanelSection title="OptiScaler">
          {game.optiscaler.map((o) => (
            <OptiItem key={o.dir} o={o} active={active} reload={reload} />
          ))}
        </PanelSection>
      )}
      <PanelSection>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={loading} onClick={reload}>
            {loading ? "Checking…" : "Check again"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

// --- main panel --------------------------------------------------------------------------------------------------------

function Content() {
  const [helix, setHelix] = useState<HelixState | null>(null);
  const [games, setGames] = useState<GameSummary[] | null>(null);
  const [onlyCompatible, setOnlyCompatible] = useState(storage("onlyCompatible", "1") === "1");
  const [selected, setSelected] = useState<string>(storage("selected", ""));
  const [scanning, setScanning] = useState(false);

  const refreshHelix = useCallback(() => {
    getHelixState().then(setHelix);
  }, []);

  const scan = useCallback(async () => {
    setScanning(true);
    setGames(await listGames());
    setScanning(false);
  }, []);

  useEffect(() => {
    refreshHelix();
    scan();
    const l = addEventListener<[ok: boolean]>("setup_done", () => refreshHelix());
    return () => {
      removeEventListener("setup_done", l);
    };
  }, [refreshHelix, scan]);

  const shown = useMemo(
    () => (games ?? []).filter((g) => !onlyCompatible || g.fsr || g.optiscaler || g.helixsr),
    [games, onlyCompatible],
  );

  const pick = (appid: string) => {
    setSelected(appid);
    store("selected", appid);
  };

  const current = shown.find((g) => g.appid === selected);

  return (
    <>
      <HelixSection state={helix} refresh={refreshHelix} />
      <PanelSection title="Game">
        <PanelSectionRow>
          <ToggleField
            label="Only FSR 3.1 / OptiScaler games"
            checked={onlyCompatible}
            onChange={(v) => {
              setOnlyCompatible(v);
              store("onlyCompatible", v ? "1" : "0");
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          {games === null ? (
            <Field label="Scanning Steam libraries…" />
          ) : shown.length === 0 ? (
            <div style={small}>No matching games found.</div>
          ) : (
            <DropdownItem
              label="Game"
              rgOptions={shown.map((g) => ({
                data: g.appid,
                label: `${g.name}${g.helixsr ? "  ✓" : ""}`,
              }))}
              selectedOption={current?.appid}
              strDefaultLabel="Select a game"
              onChange={(o) => pick(o.data)}
            />
          )}
        </PanelSectionRow>
        {current && (
          <Note>
            {[current.fsr && "FSR 3.1", current.optiscaler && "OptiScaler", current.helixsr && "HelixSR installed"]
              .filter(Boolean)
              .join(" · ") || "No FSR 3.1 or OptiScaler found"}
          </Note>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={scanning} onClick={scan}>
            {scanning ? "Scanning…" : "Rescan games"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      {current && (
        <GameSection
          key={current.appid}
          appid={current.appid}
          active={{ ready: !!helix?.built, tag: helix?.active ?? null, version: helix?.version ?? null }}
        />
      )}
    </>
  );
}

export default definePlugin(() => {
  const listener = addEventListener<[ok: boolean]>("setup_done", (ok) => {
    toaster.toast({
      title: "HelixSR setup",
      body: ok ? "Network built. HelixSR is ready to install." : "Setup failed. Open the plugin to see the log.",
    });
  });
  return {
    name: "HelixSR Manager",
    titleView: <div className={staticClasses.Title}>HelixSR Manager</div>,
    content: <Content />,
    icon: <FaDna />,
    onDismount() {
      removeEventListener("setup_done", listener);
    },
  };
});
