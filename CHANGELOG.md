## v1.0.3 (stable source release, 2026-11-10)

- Promoted the **same tested v1.0.3 RC2 implementation** to the first stable Git source package after confirmation of clean Windows installation, generation and correct shutdown of owned processes.
- No modifications to generation, ANIMA/MiniMax H3, process ownership, model hand-off, port fallback, scheduler handling, ComfyUI commit or private Python/PyTorch versions.
- Updated version labels, publication instructions and release documentation. This is **not** a new runtime or compatibility upgrade.

## v1.0.3-rc2 (Windows process-lifecycle candidate, 2026-11-10)

- Replaced the auxiliary Windows watchdog where possible with a native Windows Job Object (`KILL_ON_JOB_CLOSE`), attached only to the extension-owned Comfy worker and generation websocket relay. Closing Forge should cause Windows to clean up these owned children even after an abrupt exit.
- Retained the parent watchdog as a fallback if the current Windows environment does not allow Job Objects.
- Fixed console-popup flag conflict: previous watchdog combined `DETACHED_PROCESS` with `CREATE_NO_WINDOW`, which Windows explicitly ignores. Fallback now uses `CREATE_NO_WINDOW` without `DETACHED_PROCESS`.
- No changes to model generation, sampler presets, VRAM hand-off, installed runtime, or dependencies. Must still be tested on the user's Windows Forge installation before being declared stable.

## v1.0.3-rc1 (unified clean-install candidate, 2026-11-10)

- Consolidated the working DEV33 source with the v1.0.1 Forge/Comfy Scheduler bridge and the v1.0.2 runtime/process isolation improvements. No manual patch sequence is required.
- If `runtime/venv` was completely deleted and its ready marker remains, Install archives the obsolete marker and recreates the isolated Python environment. Partially present venv folders are still preserved instead of overwritten.
- Kept automatic port fallback for the default 8191 and private per-Forge worker ownership, so another ComfyUI process is never killed or used.
- Preserved lazy unload on Generate, ANIMA, MiniMax H3, independent presets, native image transfer, and separate Forge/Comfy Python environments.
- Clarified that an extra Windows console window has not been conclusively diagnosed; this candidate must be tested on Windows before a stable release.

## v1.0.2-rc2 (console-window test fix)

- Added explicit Windows STARTUPINFO SW_HIDE in addition to CREATE_NO_WINDOW for child ComfyUI Python processes.
- Applied this to the private Comfy backend, websocket relay, NVFP4 bridge helper and runtime Install/Update launcher.
- Worker logging, progress, process ownership, private venv and port selection are unchanged.
- The reported empty console has not been reproduced in a Windows session; verify behavior on the user's machine.

## v1.0.2-rc1 (test candidate, not yet a validated release)

- Reviewed the user-provided working DEV33 sources against v1.0.1; kept the lazy model lifecycle, presets, native image transfer and ANIMA/MiniMax integrations.
- Private ComfyUI now automatically chooses a free local loopback port (8191–8255) on a default-port conflict. Explicit `embedded_port` settings remain strict; foreign processes are never terminated or used.
- Worker log filenames are unique per Forge process.
- Installation UI now shows a separate completion/error summary and collapsible technical log, explicitly distinguishing structural checks from GPU generation tests.
- Installer and Update still keep Forge's `venv` untouched. GPU generation needs on-machine verification.

# Changelog

## 1.0.1 — Sampler/Scheduler bridge hotfix (2026-11-10)

- Fixed ANIMA generation abort when Forge submitted `Use same scheduler` instead of the Comfy Neo `beta` scheduler.
- Bound generation inputs to native Forge primary sampler/scheduler components (where available).
- Added strict handling for Forge Hires `Use same` sentinels with independent ANIMA and MiniMax H3 preset defaults.
- Added regression tests. Private runtime, model files, and Forge `venv` remain untouched.

**Test status:** offline tests passed; live GPU generation still needs verification on Windows.

## 1.0.0 — First release candidate (2026-11-09)

- Renamed **Comfy Engine** to **Forge Comfy Neo** (tab: **Comfy Neo**).
- Consolidated working development source through **DEV33** into one Git-installable extension.
- Added an in-Forge **Install** screen for the private, pinned runtime. Installation runs separately from Forge CUDA/Python and is started only by an explicit click.
- Added **Settings → Comfy Neo → Runtime → Update** for safe Git fast-forward source updates. It does not silently change the pinned private Python/PyTorch runtime.
- Preserved ANIMA txt2img, img2img, LoRA, MiniMax H3, native Forge UI, gallery, independent presets, shared outputs, and lazy memory release on Generate.
- Kept Sketch/Inpaint experimental. ControlNet is not included.

**Validation status:** source reconstruction, offline tests, and the Git updater completed. A clean installation and GPU workflows on a different Windows PC still require an end-to-end test before marking the release fully stable.
