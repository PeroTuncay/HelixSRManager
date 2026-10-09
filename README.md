# HelixSR Manager (Decky plugin)

Installs [HelixSR](https://github.com/lonewolf0622/HelixSR) into Steam games on the Steam Deck, following the
HelixSR README, and checks that it actually runs.

## What it does

1. **HelixSR**: downloads the latest release to `~/HelixSR` (folder can be changed) and runs its one-time setup
   (`helixsr-setup.sh --yes`, about 4-5 minutes; downloads NVIDIA's DLSS 310.7.0 DLL after you confirm, builds the
   network into `amd_fidelityfx_dx12.dll`, deletes NVIDIA's DLL).
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
5. **Check**: reads `helixsr.log` (network running / not running / loaded but idle, wave size, network resolution) and,
   if enabled, OptiScaler.log lines about HelixSR/FFX.
6. **Settings**: `NetworkResolution` (auto/full/QSSM/PRSM) and sharpening per install.

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
- If the setup fails in Gaming Mode, run `~/HelixSR/helixsr-setup.sh` once from Konsole in Desktop Mode; the plugin
  picks up the built DLL afterwards.
- Direct3D 12 only. Vulkan games aren't supported by HelixSR.
- Close the game before installing or reverting.
