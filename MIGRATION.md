# Migrating from Comfy Engine DEV33 to Forge Comfy Neo v1.0.3

**Do not run the two extensions in the same Forge instance.** Both bind native Forge UI buttons and callbacks, and can conflict with each other.

1. Back up `extensions/comfymax-h3-neo-embedded-dev01` and the Forge `config.json` containing your presets.
2. Fully close Forge, including any worker it owns.
3. Disable/remove the old extension through Forge. Keep a backup outside `extensions/` so that Forge does not load its scripts.
4. Install `forge-comfy-neo` from Git in Forge, then restart Forge.
5. In the new **Comfy Neo** tab, use **Install** to prepare an independent private runtime. Do **not** move a Windows `runtime/venv` between extension folders; absolute paths can become invalid. Existing Forge model files do not need to be downloaded again.
6. Restart Forge after installation and check ANIMA and MiniMax H3. Then verify whichever additional img2img/LoRA workflows you use.

Existing internal preset keys (`h3_*`, `comfy_anima_*`, `comfy_image_*`) are retained for compatibility with saved Forge settings, but check preset values once after migration.

### Repairing the private environment

If you intentionally removed the **entire** `runtime/venv` folder but left `runtime/private_env_ready.json`, Install archives that stale marker and recreates the environment using the pinned recipe. A partially present or moved `venv` is not overwritten automatically. Follow the README recovery instructions after backing up your files. **Never remove Forge's own `venv`.**

### Upgrading from v1.0.3 RC2

Stable v1.0.3 does not change the runtime recipe or inference implementation. With Forge closed, update the extension's source files from Git. Keep `runtime/core`, `runtime/venv`, models and settings untouched. Restart Forge to load the new version labels.
