# HelixSR Manager (Decky plugin)

Installs [HelixSR](https://github.com/lonewolf0622/HelixSR) into Steam games on the Steam Deck, following the
HelixSR README, and checks that it actually runs.

## What it does

1. **HelixSR release**: a dropdown of every release on
   [HelixSR's release page](https://github.com/lonewolf0622/HelixSR/releases), read live from GitHub's API (the last
   list is cached for offline use), so new releases show up without a plugin update. The selected release is the one
   the install buttons use. Each release is downloaded to its own folder (`~/HelixSR/v1.4.1`, …; the base folder can
   be changed) and needs its one-time setup (`helixsr-setup.sh --yes`, about 4-5 minutes; downloads NVIDIA's DLSS
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
8. **Settings**: `NetworkResolution` (auto/full/QSSM/PRSM) and sharpening per install.

## Install on the Deck

Decky Loader → Settings → General → enable **Developer mode**, then Developer → **Install plugin from ZIP file** and
pick `HelixSR-Manager.zip` (copy it to the Deck first, e.g. to `~/Downloads`). Or serve the zip over the network and
use **Install plugin from URL**.

## Build

```
npm install
npm run package     # -> out/HelixSR-Manager.zip
```

## Notes

- Runs as the `deck` user (no root).
- Don't let the Deck sleep during the setup (sleep pauses it). Expect it to take longer than the 4-5 minutes HelixSR
  quotes for a desktop CPU.
- If the setup fails in Gaming Mode, run `~/HelixSR/helixsr-setup.sh` once from Konsole in Desktop Mode; the plugin
  picks up the built DLL afterwards.
- Direct3D 12 only. Vulkan games aren't supported by HelixSR.
- Close the game before installing or reverting.

## License

Public domain ([The Unlicense](LICENSE)): copy, change and use it however you like. The install logic follows
HelixSR's Apache-2.0 installer script; see [LICENSE](LICENSE) for the notes on HelixSR and NVIDIA's files.
