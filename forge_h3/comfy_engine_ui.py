"""Separate Comfy Neo tab. Designed for Forge Neo's Gradio on_ui_tabs API.

Reuses the unchanged DEV07 H3 backend; adds generic API JSON workflow support.
"""
from __future__ import annotations

import html
import queue
import threading
import time

import gradio as gr

from . import comfy_engine as engine


def _model_choices():
    try:
        from modules import sd_models, shared
        candidates = []
        for title, info in (getattr(sd_models, "checkpoints_list", {}) or {}).items():
            label = str(title)
            path = str(getattr(info, "filename", ""))
            if any(x in (label + " " + path).lower() for x in ("minimax", "h3", "pinkcherry")):
                candidates.append(label)
        # A selected model may be known under an alias not in checkpoints_list.
        selected = str(getattr(shared.opts, "sd_model_checkpoint", "") or "")
        if selected and selected not in candidates:
            candidates.insert(0, selected)
        return list(dict.fromkeys(candidates)), selected
    except Exception:
        return [], ""


def _module_choices():
    try:
        from modules_forge import main_entry
        from modules import shared
        registry = getattr(main_entry, "module_list", {}) or {}
        candidates = [str(name) for name, path in registry.items() if any(
            key in (str(name) + " " + str(path)).lower()
            for key in ("minimax", "h3", "qwen3vl", "audio_vae", "video_vae"))]
        selected = [str(name) for name in (getattr(shared.opts, "forge_additional_modules", None) or [])]
        for value in selected:
            if value not in candidates:
                candidates.append(value)
        # Do not guess model components: two VAEs + text encoder must be selected.
        return list(dict.fromkeys(candidates)), selected
    except Exception:
        return [], []


def _refresh(selected_workflow, selected_checkpoint, selected_modules):
    wf = engine.list_workflows()
    values = {v for _, v in wf}
    choice = selected_workflow if selected_workflow in values else wf[0][1]
    checkpoints, default = _model_choices()
    models = _module_choices()[0]
    return (gr.update(choices=wf, value=choice),
            gr.update(choices=checkpoints, value=selected_checkpoint if selected_checkpoint in checkpoints else default if default in checkpoints else None),
            gr.update(choices=models, value=[v for v in (selected_modules or []) if v in models]),
            "Workflow and model lists refreshed. Use API-format JSON for additional workflows.")


def _workflow_changed(value):
    h3 = engine.is_h3(value)
    ref = value == 'h3:ref2va'
    return (gr.update(visible=h3), gr.update(visible=h3),
            gr.update(visible=h3),
            "**MiniMax H3:** uses the existing verified DEV07 backend. "
            + ("First image + optional additional reference images." if ref else "First and last frame (FL2VA); Ref2VA detected from checkpoint in Auto mode.")
            if h3 else
            "**API workflow:** put its JSON into the `workflows` folder. Use `{{prompt}}`, `{{steps}}`, `{{seed}}` etc. "
            "for editable fields. Other input values are preserved exactly as saved.")


def _run(*args):
    """Stream an elapsed timer while the shared backend performs the job in a worker thread."""
    outcome = queue.Queue(maxsize=1)
    def work():
        try:
            outcome.put((True, engine.run_workflow(*args)))
        except Exception as exc:
            outcome.put((False, exc))

    thread = threading.Thread(target=work, name="ComfyEngine-generate", daemon=True)
    thread.start()
    started = time.monotonic()
    yield "**Comfy Neo:** starting…", gr.update(value=[], visible=False), gr.update(value=None, visible=False), gr.update(value=None, visible=False)
    while True:
        try:
            ok, result = outcome.get(timeout=0.5)
            break
        except queue.Empty:
            elapsed = int(time.monotonic() - started)
            yield f"**Comfy Neo:** running · {elapsed // 60:02d}:{elapsed % 60:02d} · progress details in Forge console", gr.update(), gr.update(), gr.update()
    if not ok:
        message = html.escape(str(result))
        print(f"[Comfy Neo] {result}")
        yield f"**Error:** {message}", gr.update(), gr.update(), gr.update()
        return
    images, video, files, info = result
    elapsed = time.monotonic() - started
    yield (f"**{html.escape(info)}** · {elapsed:.1f} s",
           gr.update(value=images, visible=bool(images)),
           gr.update(value=video, visible=bool(video)),
           gr.update(value=files or None, visible=bool(files)))


def create_tab():
    workflows = engine.list_workflows()
    ckpts, selected_ckpt = _model_choices()
    mods, selected_mods = _module_choices()
    with gr.Blocks(analytics_enabled=False) as panel:
        gr.Markdown("## Comfy Neo\nIndependent workflow workspace using Forge Neo's embedded ComfyUI. The existing MiniMax tab is unchanged.")
        with gr.Row():
            with gr.Column(scale=5):
                workflow = gr.Dropdown(choices=workflows, value=workflows[0][1], label="Workflow", elem_id="comfy_engine_workflow")
                refresh = gr.Button("Refresh workflows and models", size="sm")
                help_text = gr.Markdown("MiniMax H3 workflows use the verified DEV07 backend. New API-format JSON workflows can be added to the `workflows` folder.")
                prompt = gr.Textbox(lines=5, label="Prompt", placeholder="Describe your image or video…", elem_id="comfy_engine_prompt")
                negative = gr.Textbox(lines=2, label="Negative prompt (API workflows with {{negative_prompt}})")
                with gr.Row():
                    width = gr.Number(label="Width", value=512, precision=0, minimum=64)
                    height = gr.Number(label="Height", value=512, precision=0, minimum=64)
                    frames = gr.Number(label="Frames (video)", value=124, precision=0, minimum=1)
                with gr.Row():
                    steps = gr.Number(label="Steps", value=4, precision=0, minimum=1)
                    seed = gr.Number(label="Seed (-1 = random)", value=-1, precision=0)
                    cfg = gr.Number(label="CFG (API workflows)", value=3.5, minimum=0)
                checkpoint = gr.Dropdown(choices=ckpts, value=selected_ckpt if selected_ckpt in ckpts else None,
                                         label="MiniMax checkpoint", allow_custom_value=True)
                modules = gr.Dropdown(choices=mods, value=selected_mods, multiselect=True,
                                      label="MiniMax modules (text encoder, video VAE, audio VAE)",
                                      allow_custom_value=True)
                with gr.Row():
                    first_image = gr.Image(type="filepath", sources=["upload", "clipboard"], label="First frame / reference / {{image}}", height=185)
                    last_image = gr.Image(type="filepath", sources=["upload", "clipboard"], label="Last frame / {{last_image}}", height=185)
                refs = gr.File(label="Extra reference images (MiniMax Ref2VA)", file_count="multiple", file_types=["image"])
                with gr.Row():
                    start = gr.Button("Generate", variant="primary", elem_id="comfy_engine_generate")
                    stop = gr.Button("Stop", variant="stop", elem_id="comfy_engine_stop")
                    release = gr.Button("Release GPU memory", variant="secondary")
            with gr.Column(scale=4):
                status = gr.Markdown("Ready. Select a workflow and check its model inputs.")
                output_images = gr.Gallery(label="Generated images", visible=False, columns=2)
                output_video = gr.Video(label="Generated video", visible=False, autoplay=False)
                downloads = gr.File(label="Saved output files", file_count="multiple", visible=False)
                gr.Markdown("**Compatibility:** MiniMax H3 is ready. Other workflows must be ComfyUI **API-format JSON**, use installed built-in node types, and reference visible model files. An ordinary editor workflow is not executable without conversion.")

        workflow.change(_workflow_changed, inputs=workflow, outputs=[checkpoint, modules, refs, help_text], queue=False, show_progress=False)
        refresh.click(_refresh, inputs=[workflow, checkpoint, modules], outputs=[workflow, checkpoint, modules, status], queue=False, show_progress=False)
        start.click(_run,
                    inputs=[workflow, prompt, negative, width, height, steps, seed, cfg, frames,
                            checkpoint, modules, first_image, last_image, refs],
                    outputs=[status, output_images, output_video, downloads], show_progress=False)
        stop.click(engine.request_stop, outputs=status, queue=False, show_progress=False)
        release.click(engine.release_worker, outputs=status, queue=False, show_progress=False)
    return [(panel, "Comfy Neo", "comfy_engine")]
