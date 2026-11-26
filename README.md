# Forge Comfy Neo

**Version 1.1.0.** Windows, NVIDIA CUDA.

The project keeps the familiar Forge Neo txt2img/img2img interface and runs a **separate, private, headless ComfyUI** for the supported architectures. It does **not** replace your normal Forge pipeline, Python or PyTorch.

## Install from Git — inside Forge

1. Use **Forge Neo → Extensions → Install from URL**, enter your published Git repository URL, then apply/restart Forge. Alternatively clone the `forge-comfy-neo` repository into `Forge Neo/extensions/`.
2. Open the new **Comfy Neo** tab. On a machine with no private runtime, it shows **Install** rather than the generator.
3. Click **Install**. The installer downloads the pinned ComfyUI source and an isolated Python/PyTorch environment into this extension's `runtime` folder. Follow the progress indicator; technical logs are available under **Show installation log**.
4. After installation is reported successful, **restart Forge Neo** and reopen Comfy Neo. The native generation interface is now available.

No BAT files or manual paths are needed for a fresh installation. Models are **not** downloaded by Install; use your Forge model folders as before. Large dependency downloads and free disk space are required. The installation action is allowed only from a local (localhost) Forge session.

**Important:** do not install/rerun this extension simultaneously with the old `comfymax-h3-neo-embedded-dev01` extension. Both hook the same Forge UI. See [MIGRATION.md](MIGRATION.md) to transition safely. Do not simply move an existing Windows `venv` folder: its Python marker and environment paths may become invalid.

## Using Comfy Neo

- Preset **H3 Video** (MiniMax H3): video generation using the private ComfyUI worker. Ref2VA supports up to three MP4 video references (24 FPS, 2–15 s, up to 96 MiB per file), individual reference soundtrack switches and up to three standalone audio references. Local Windows Explorer clipboard paste is supported.
- **Pika H3 Flex** schedule: LCM, six steps, CFG 1, manual sigmas and the matching FL2VA/Ref2VA Pika H3 Flex LoRA from the Forge LoRA folder. The LoRAs are not bundled.
- Preset **H3 Image**: MiniMax H3 still-image generation and reference-based image editing, including the P-H3-Image (P-Image-OSS) schedule. Choose the diffusion model, text encoder, VAE and compatible LoRAs manually. Use native Forge `<lora:filename:weight>` prompt tags. Image dimensions must be multiples of 32 (minimum 64); the validation message suggests nearby valid sizes. The Denoising Strength slider does not affect H3 Image; reference-based editing uses full denoising or the P-H3-Image manual sigma schedule. H3 Image Edit currently uses the Image tab, not masked inpaint.
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
pins and migration logic, not a blind `pip install -U`. When upgrading from an older, incompatible runtime, close Forge, back up and remove this extension's old `runtime/core`, `runtime/venv` and `runtime/private_env_ready.json` before starting Forge and clicking **Install**. Restart Forge after installation. Do not change Forge's own environment. The existing runtime Python downloader, tools and cache can be retained. If the private runtime already matches the v0.46.0 recipe below, keep it; no reinstall is needed for this source update.

### Runtime in the current main branch

- ComfyUI v0.46.0 source: `a277b5bf213e8a44fa34817b7770b54f86ef4cea`
- Private Python: **3.13.18** (managed by `uv`)
- Private PyTorch: stable CUDA 13.0 (`cu130`) wheels from the official PyTorch index, including torchvision and torchaudio. The reported working installation resolved torch **2.14.1+cu130**, torchvision **0.29.1+cu130**, torchaudio **2.11.0+cu130**; subsequent installations may resolve newer compatible versions.
- `uv`: **0.13.7**, official binary with SHA-256 validation
- Additional dependencies are installed from the pinned ComfyUI requirements. Resolved CUDA package versions are constrained and checked to prevent their replacement during dependency installation.

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

The original v1.0.3 stable release was validated separately; its historical audit is in [SOURCE_AUDIT.md](SOURCE_AUDIT.md). Version 1.1.0 includes later user-tested video and image generation changes. The historical audit does not cover these new changes. Comfy Neo image previews open in a viewport-fitted viewer, and the worker exposes a VRAM hand-off for the updated Forge AI Prompt extension. Automatic download-cache cleanup is not yet implemented.

This does not guarantee every model, extension combination or experimental img2img/Sketch/Inpaint mode. To avoid conflicting Forge callbacks, do not enable this extension alongside the former Comfy Engine/ComfyMax extension.
