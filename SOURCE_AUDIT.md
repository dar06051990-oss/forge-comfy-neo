# Source and compatibility audit — v1.0.3 stable

Source baseline: user-provided `comfymax-h3-neo-embedded-dev01.zip`, with the final
DEV33 patch applied (`forge_h3/ui.py` and `scripts/comfy_engine_tab.py` match
`Comfy-Engine-DEV33-LAZY-UNLOAD-ON-GENERATE.zip`).

The initial v1.0.0/v1.0.1 transition did not require rewriting Comfy's engine.
The shared changes were predominantly Comfy Engine → Comfy Neo branding,
the new Install/Update UI, ANIMA LoRA alias handling and the fix for Forge
sending the Hires-only `Use same scheduler` sentinel into Comfy's scheduler.
The v1.0.2 candidate added per-process worker logs, loopback port fallback,
and hidden-process launcher options.

The working archive includes ComfyUI v0.44.0 under `runtime/core`; the candidate
Git package uses the same pinned upstream commit
`bdb0816a292d221efeb6ec4ae41bcdff3f91fc86`, downloading that source at
first install. The earlier audit matched main.py, comfy/samplers.py,
comfy/model_management.py, comfyui_version.py and requirements.txt to that commit.

New in v1.0.3 RC1: when the **entire** `runtime/venv` folder is absent, an obsolete
`private_env_ready.json` marker is saved aside so the user can reinstall from the
pinned recipe using Install. A partly present private venv is **never** silently
replaced. Model files and Forge's Python environment are not changed.

## Validation summary for v1.0.3 Stable

- In this build environment: **147 offline unit tests**, **93 passed**, **54 skipped** (GPU/Forge dependencies unavailable). Python source compile checks passed.
- Separately, the user confirmed working v1.0.3 RC2 generation, no extra window, and cleanup of owned subprocesses on Windows; then confirmed a fresh installation of the full RC2 package and successful operation.
- These Windows observations were reported by the user, not independently reproduced inside this build environment. Concurrent operation of two Forge instances is **not** claimed tested.
- Stable v1.0.3 uses the same runtime/inference/process code as RC2. Only version labels, changelog, publishing and audit documentation changed.


## Exclusions from the Git package

The archive does not include `runtime/core`, `runtime/venv`, uv binaries,
models, user presets, locally generated data, `__pycache__`, caches or any
absolute runtime paths. See `.gitignore` and `SOURCE_SHA256.json`.

## Windows process lifecycle — v1.0.3 RC2

Replaced the default watcher subprocess with a kill-on-close Windows Job Object.
The Comfy worker is added to the job at launch, and after checking process
ancestry, the real Python listener is also enrolled to cover the Windows
virtual-environment redirector race. The websocket relay belongs to this job
as well. If job assignment is unavailable, the fallback watchdog now uses
`CREATE_NO_WINDOW` without `DETACHED_PROCESS`, as required by Windows.
This is source-level validation, not an observed Windows/GPU end-to-end test.

## Stable package provenance

Original source: `Forge-Comfy-Neo-v1.0.3-RC2-Git.zip`. All non-versioned runtime and generation Python files retain the original RC2 SHA-256 checksums; see `SOURCE_SHA256.json` for the stable archive.
