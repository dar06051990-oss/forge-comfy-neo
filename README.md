# Forge Comfy Neo

**v1.0.3 — first stable source release of the embedded ComfyUI backend for Forge Neo.**  Windows, NVIDIA CUDA.

The project keeps the familiar Forge Neo txt2img/img2img interface and runs a **separate, private, headless ComfyUI** for the supported architectures. It does **not** replace your normal Forge pipeline, Python or PyTorch.

## Install from Git — inside Forge

1. Use **Forge Neo → Extensions → Install from URL**, enter your published Git repository URL, then apply/restart Forge. Alternatively clone the `forge-comfy-neo` repository into `Forge Neo/extensions/`.
2. Open the new **Comfy Neo** tab. On a machine with no private runtime, it shows **Install** rather than the generator.
3. Click **Install**. The installer downloads the pinned ComfyUI source and an isolated Python/PyTorch environment into this extension's `runtime` folder. Follow the progress indicator; technical logs are available under **Show installation log**.
4. After installation is reported successful, **restart Forge Neo** and reopen Comfy Neo. The native generation interface is now available.

No BAT files or manual paths are needed for a fresh installation. Models are **not** downloaded by Install; use your Forge model folders as before. Large dependency downloads and free disk space are required. The installation action is allowed only from a local (localhost) Forge session.

**Important:** do not install/rerun this extension simultaneously with the old `comfymax-h3-neo-embedded-dev01` extension. Both hook the same Forge UI. See [MIGRATION.md](MIGRATION.md) to transition safely. Do not simply move an existing Windows `venv` folder: its Python marker and environment paths may become invalid.

## Using Comfy Neo

- Preset **MiniMax H3**: video generation using the private ComfyUI worker.
- Preset **ANIMA**: txt2img, img2img / Edit, native `<lora:filename:weight>` tags through your existing Forge prompt tools, and experimental Sketch/Inpaint modes.
- Existing Forge Sampler, Scheduler, Seed, prompt, Gallery and output workflows are reused. Preset settings are separate from ordinary Forge.
- Runtime model unloading is **lazy**: merely browsing tabs/presets does not evict models; generating with the other engine handles VRAM hand-off.
- Experimental Inpaint/Sketch have not been fully validated on all ANIMA checkpoints or Edit LoRAs. ControlNet is **not** a v1 feature.

## Updates via Settings

Open **Settings → Comfy Neo → Runtime → Update**. When the extension was installed
from a Git repository (with a tracked upstream branch), this button safely runs
`git pull --ff-only` in the extension folder. It refuses to overwrite tracked
local modifications and does **not** reset your branch. If new extension code is
received, **restart Forge** before using it.

The separate private ComfyUI/PyTorch environment is **not** replaced by this
Git operation. This is intentional: runtime upgrades need explicit compatible
pins and migration logic, not a blind `pip install -U`. If a future extension
release changes its required runtime, follow that release's migration guide
rather than attempting to transplant or overwrite a live Windows `venv`.

See [PUBLISHING.md](PUBLISHING.md) for a Git publishing and smoke-test checklist.

### Pinned runtime in v1.0.3

- ComfyUI source: `bdb0816a292d221efeb6ec4ae41bcdff3f91fc86`
- Private Python: **3.13.18** (managed by `uv`)
- Private PyTorch: **2.11.0 + CUDA 13.0**, torchvision **0.26.0**
- `uv`: **0.13.7**, official binary with SHA-256 validation
- Additional dependencies are installed from the pinned ComfyUI source requirements; torch and torchvision are constrained to the versions above.

The exact ComfyUI source is pinned by commit, and the package installer prints its steps in `runtime/forge_comfy_neo_install.log`. A GitHub-provided source tarball is downloaded over HTTPS; unlike `uv`, it is not currently verified against an independently pinned tarball checksum.

## Recovery and known limitations

- If the UI still shows Install after successful setup, **restart Forge**: Gradio tabs are built at process startup.
- If Install fails, check its log in the tab or `runtime/forge_comfy_neo_install.log`. A partial environment is not overwritten automatically. Back up `runtime` before deleting/repairing it. If you deleted the entire `runtime/venv` folder but left the ready marker, Install now backs up that stale marker and restores the private environment from the pinned recipe.
- For a clean reinstall, with Forge fully closed, back up and remove this extension's `runtime/core`, `runtime/venv`, `runtime/python`, `runtime/python-bin` and `runtime/private_env_ready.json` (and any broken download cache). Then use Install again. **Do not delete Forge's own `venv`.**
- This source distribution contains **no model weights** and **no embedded Python or CUDA executables**.
- If another Forge copy uses the same private ComfyUI port, edit `comfy_bridge.json`'s `embedded_port` (1024–65535) while Forge is closed. The default is 8191. If it is already in use by a different ComfyUI/Forge instance, Comfy Neo selects a free port in 8191–8255 without terminating the other process. An explicitly configured `embedded_port` is always respected.
- First-release installation needs a Windows NVIDIA GPU with a compatible CUDA driver and internet access; CUDA/Python dependencies are not expected to run on macOS/CPU.
- Inpaint modes require a correct mask and a compatible ANIMA/Edit workflow; they remain experimental.

## Project and license

Forge Comfy Neo is a community extension, not an official ComfyUI or Forge release. This repository is built from the previous ComfyMax H3 Embedded / Comfy Engine development tree. See [CHANGELOG.md](CHANGELOG.md).

The base extension includes code derived from Forge Neo and its upstream components; the bundled `LICENSE` contains the **GNU AGPL v3** license. The separate ComfyUI source is downloaded from the upstream project at installation; its license is included in `runtime/core/LICENSE`. Review upstream model licenses separately.

## Validation and known limitations

This stable source release is the **same generation, worker-management and installer implementation as v1.0.3 RC2**. It promotes the version after reported real-world validation on Windows: a clean installation, generation, no extra console window and owned process cleanup on Forge exit. The independent offline source test suite also passes; see [SOURCE_AUDIT.md](SOURCE_AUDIT.md).

This does not guarantee every model, extension combination or experimental img2img/Sketch/Inpaint mode. No runtime pins or model files were changed for the stable release. To avoid conflicting Forge callbacks, do not enable this extension alongside the former Comfy Engine/ComfyMax extension.
