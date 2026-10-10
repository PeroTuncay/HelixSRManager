# HelixSR Manager (Decky plugin)

Installs [HelixSR](https://github.com/lonewolf0622/HelixSR) into Steam games on the Steam Deck, following the
HelixSR README, and checks that it actually runs.

Tested on a Steam Deck OLED with The Witcher 3 (DirectX 12) through OptiScaler.

> **HelixSR 1.5 changed how it works.** Up to 1.4, HelixSR replaced FSR 3.1 (`amd_fidelityfx_dx12.dll`). Since 1.5 it
> replaces DLSS (`nvngx.dll`) and works **only through OptiScaler (0.9.4)**, which needs a Steam launch option that
> makes the Deck's GPU look like an NVIDIA one. The plugin handles both: pick a 1.5 release for the DLSS route, or a
> 1.4 release to replace a game's own FSR 3.1 directly.

## Installation

### 1. Install the plugin

1. In Decky Loader, open **Settings → General** and turn on **Developer mode**.
2. Open **Settings → Developer → Install plugin from URL** and paste the link to `HelixSR-Manager.zip` from the
   [latest release](https://github.com/PeroTuncay/HelixSRManager/releases/latest) (on the release page: right-click
   or long-press the zip → copy link).
   Alternatively, download the zip, copy it to the Deck and use **Install plugin from ZIP file**.
3. Open the Quick Access menu (`···` button) → Decky → **HelixSR Manager**.

Updating works the same way: install the newer zip over the old one.

### 2. Download and build HelixSR (once per release)

1. Under **HelixSR**, pick a release in **Release to install** (the newest is marked *latest*).
2. Press **Download**.
3. Press **Run setup (build network)** and confirm. The setup downloads NVIDIA's DLSS 310.7.0 DLL, a portable Python
   with numpy and Microsoft's shader compiler, builds HelixSR's network into the DLL and deletes NVIDIA's DLL again.
4. Wait for it to finish: **about 7-10 minutes on a Steam Deck OLED** (the first run includes the one-time downloads;
   later releases reuse Python and the compiler). Keep the Deck plugged in and don't let it sleep, since sleep pauses
   the setup. You can close the menu meanwhile; the progress bar picks up where it is when you reopen it.
5. The release now shows **✓ ready**.

### 3. Install HelixSR into a game

Close the game first, then pick it under **Game** (the list shows games with an FSR 3.1 DLL or OptiScaler; turn off the
filter to see all).

- **HelixSR 1.5+ (game with OptiScaler 0.9.4):** in the **OptiScaler** section press **Use HelixSR in OptiScaler
  (as DLSS)**. This puts `nvngx.dll` into a `HelixSR` folder next to `OptiScaler.ini` and sets `Dx12Upscaler = dlss`
  and `NvngxPath` there (same files and marker as HelixSR's own `helixsr-install.sh`, so either tool can undo the
  other). Then press **Add launch option**: it adds `PROTON_FORCE_NVAPI=1 DXVK_NVAPI_GPU_ARCH=AD100` in front of the
  game's existing launch options (e.g. Decky Framegen's stay as they are). In the game, select **DLSS**. Your FSR setup
  stays selectable in OptiScaler's menu for comparison.
- **HelixSR 1.4 (game with OptiScaler):** in the **OptiScaler** section press **Use HelixSR in OptiScaler**. Leave
  **Keep current FSR selectable** on to keep your FSR 4 available for comparison. In the game, open OptiScaler's menu
  and pick **FSR HelixSR (3.1.5)** under Upscalers → FFX Upscaler.
- **Game that ships FSR 3.1 itself (HelixSR 1.4 only)**: in **Replace the game's FSR 3.1** press **Install HelixSR
  here**, then select AMD FSR as the upscaler in the game's settings.

Switching a game between a 1.4 and a 1.5 release is one button; the plugin undoes the other setup first. If OptiScaler
logs "Not running on Nvidia, disabling DLSS", the plugin points out that the launch option wasn't active.

### 4. Check that it runs

After playing for a moment, press **Check again** in the plugin. Green **HelixSR network running** means it works. A
red frame around the picture, or a red status, means the network isn't running; the plugin shows what HelixSR logged.

The check also shows what HelixSR last upscaled, e.g. "853x533 → 1280x800 (Quality, 1.5x), Network: Model E main".

**To prove HelixSR (and not FSR) is doing the upscaling**, turn off **DLSS network (Model E)** for that game and
restart it. HelixSR then falls back to a simple, blurry placeholder upscale: if the picture changes, HelixSR is the
active upscaler; if it looks the same, a different upscaler is selected. Turn the network back on afterwards. The switch
only takes effect when the game starts. For a live side-by-side comparison, switch between "FSR HelixSR (3.1.5)" and
FSR 3.1.5 / FSR 4 in OptiScaler's menu (Upscalers → FFX Upscaler); an entry named just "FSR 3.1.5" is AMD's FSR 3.1,
not HelixSR.

For HelixSR 1.5+ the network switch is experimental: 1.5 documents no settings but still contains this switch.

To undo: **Revert OptiScaler to previous upscaler** (plus **Remove HelixSR launch option** for 1.5+) or **Restore the
game's FSR**.

## What it does

1. **HelixSR release**: a dropdown of every release on
   [HelixSR's release page](https://github.com/lonewolf0622/HelixSR/releases), read live from GitHub's API (the last
   list is cached for offline use), so new releases show up without a plugin update. The selected release is the one
   the install buttons use. Each release is downloaded to its own folder (`~/HelixSR/v1.4.1`, …; the base folder can
   be changed) and needs its one-time setup (`helixsr-setup.sh --yes`, about 7-10 minutes on a Steam Deck OLED; downloads NVIDIA's DLSS
   310.7.0 DLL after you confirm, builds the network, deletes NVIDIA's DLL). Several releases can sit side by side;
   switching a game to another one is one button. Releases before 1.4.0 keep the network in
   `helixsr_weights.bin`/`helixsr_kernels.pak`, which are copied along with the DLL.
2. **Game list**: every installed Steam game from all libraries (SD card too), optionally filtered to games that ship
   an FSR 3.1 DLL or have OptiScaler.
3. **Replace FSR 3.1 directly**: renames the game's `amd_fidelityfx_upscaler_dx12.dll` (or `amd_fidelityfx_dx12.dll`)
   to `*.original.dll` and puts HelixSR in its place, plus `helixsr.ini`. Same files and `helixsr-install.json` marker
   as the official `helixsr-install.sh`, so either tool can undo the other's install.
4. **OptiScaler**: creates a `HelixSR` folder next to `OptiScaler.ini` with HelixSR as both
   `amd_fidelityfx_dx12.dll` and `amd_fidelityfx_upscaler_dx12.dll`, and sets in `OptiScaler.ini`:
   `Dx12Upscaler` (`ffx` on OptiScaler 0.9+, `fsr31` on older versions), `FfxDx12Path`, `FfxDx12SRPath` (as `Z:\…`
   paths) and `[FSR] UpscalerIndex=0`. The old values are remembered and restored on revert; a full copy is kept as
   `OptiScaler.ini.helixsr-backup`. With **Keep current FSR selectable**, the FSR 4 DLL OptiScaler used so far is copied
   in as `amd_fidelityfx_upscaler_dx12.amd.dll` and set as HelixSR's `[Forwarding] UpscalerDll`, so FSR 4 stays in
   OptiScaler's FFX Upscaler list for comparison. The previous `amd_fidelityfx_dx12.dll` is copied in as
   `amd_fidelityfx_dx12.original.dll` so frame generation keeps working.
5. **Setup progress**: while the setup runs, a progress bar with the current step (in step 5 the compiled shaders,
   e.g. 41/72), the elapsed time, whether it is actually working (CPU use of its processes) and what runs right now.
   HelixSR's steps print nothing while they work, so the log alone can stay unchanged for minutes. The setup keeps
   running when the menu is closed or Decky restarts the plugin, and its exit code is recorded either way. If it
   fails: the error lines, **Show full log**, **Run diagnostics** (Proton, Python/numpy, free space, GitHub, …) and
   **Save debug report** (`~/HelixSR/helixsr-manager-debug.txt`).
6. **Clean**: removes every downloaded release, the setup's Python, shader compiler and Wine data
   (`~/.local/share/HelixSR`) and temporary files. Games keep their installed copy. Only files recognizably
   HelixSR's are removed from the HelixSR folder.
7. **Check**: reads `helixsr.log` (network running / not running / loaded but idle, wave size, network resolution) and,
   if enabled, OptiScaler.log lines about HelixSR/FFX.
8. **Settings**: the DLSS network on/off test switch, `NetworkResolution` (auto/full/QSSM/PRSM) and sharpening per
   install.

## Build

```
npm install
npm run package     # -> out/HelixSR-Manager.zip
```

## Notes

- Runs as the `deck` user (no root).
- Setup time: about 7-10 minutes on a Steam Deck OLED (HelixSR quotes 4-5 minutes for a desktop CPU). Don't let the
  Deck sleep during the setup (sleep pauses it).
- If the setup fails in Gaming Mode, run it once from Konsole in Desktop Mode, e.g.
  `~/HelixSR/v1.4.1/helixsr-setup.sh`; the plugin picks up the built DLL afterwards.
- Direct3D 12 only. Vulkan games aren't supported by HelixSR.
- Close the game before installing or reverting.

## License

Public domain ([The Unlicense](LICENSE)): copy, change and use it however you like. The install logic follows
HelixSR's Apache-2.0 installer script; see [LICENSE](LICENSE) for the notes on HelixSR and NVIDIA's files.
