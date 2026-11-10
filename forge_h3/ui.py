"""Small H3 accordion and native-control events for Forge's Gradio interface."""

import html
import logging

import gradio as gr

from .contracts import AUDIO_SHIFT, DEFAULT_FRAMES, FPS, H3Error
from .integration import checkpoint_info
from .models import ROLE_LABELS, inspect_model
from .ui_state import frame_view, preset_frame_view

COMPONENTS = {}
NATIVE_CONTROLS = ("batch_size", "batch_count", "sampling", "scheduler", "cfg_scale")
PANELS = []
logger = logging.getLogger("forge_h3")


def _in_blocks():
    # Forge also creates components outside the UI build (on page load); events can only be bound inside it
    return gr.context.Context.root_block is not None


def capture(component, **kwargs):
    elem_id = getattr(component, "elem_id", None)
    if elem_id:
        COMPONENTS[elem_id] = component
        if elem_id in ("txt2img_batch_size", "img2img_batch_size"):
            key = elem_id.replace("batch_size", "h3_duration")
            if key not in COMPONENTS:
                COMPONENTS[key] = gr.Markdown(value="", visible=False, elem_id=key)
        if elem_id in ("setting_sd_model_checkpoint", "setting_sd_modules", "forge_ui_preset"):
            bind_all(elem_id)


class Panel:
    def __init__(self, is_img2img):
        self.tab = "img2img" if is_img2img else "txt2img"
        self.is_img2img = is_img2img
        self.saved = gr.State({"active": False})
        from modules import shared
        from .comfy_output import option
        autoplay_default = bool(option("comfy_video_autoplay", False))
        with gr.Accordion("MiniMax H3", open=False, visible=False, elem_id=f"{self.tab}_h3_panel") as self.accordion:
            self.output = gr.Radio(["Video", "Still image"], value="Video", label="Output",
                                   visible=not is_img2img, elem_id=f"{self.tab}_h3_output")
            self.audio = gr.Checkbox(value=True, label="Include generated audio", elem_id=f"{self.tab}_h3_audio")
            # the audio stream's own flow shift; Shift (the h3 preset slider) is the video one
            self.audio_shift = gr.Slider(minimum=1.0, maximum=20.0, step=0.5, value=AUDIO_SHIFT, label="Audio shift",
                                         elem_id=f"{self.tab}_h3_audio_shift")
            if is_img2img:
                gr.Markdown("With an FL2VA checkpoint the input image is the **first frame**; for a **last frame** too, add "
                            "one image to the **ImageStitch Integrated** gallery. With a **Ref2VA** checkpoint the input "
                            "image is `<Picture 1>` and the gallery holds the next reference pictures, up to 9 in all. "
                            "Denoising strength is not used.")
            else:
                gr.Markdown("With an FL2VA checkpoint, one image in the **ImageStitch Integrated** gallery is the **last "
                            "frame** (img2img gives the first). With a **Ref2VA** checkpoint the gallery holds up to 9 "
                            "reference pictures, `<Picture 1>`, `<Picture 2>`... in order.")
            self.status = gr.Markdown("Select the H3 text encoder, video VAE and audio VAE in VAE / Text Encoder.")
            self.comfy_generate = gr.Button("H3 Comfy backend trigger", visible=False, elem_id=f"{self.tab}_h3_comfy_generate")
            self.comfy_stop = gr.Button("H3 Comfy stop trigger", visible=False, elem_id=f"{self.tab}_h3_comfy_stop")
            self.comfy_video = gr.Video(label="H3 Comfy result", visible=False, elem_id=f"{self.tab}_h3_comfy_video")
            # DEV18: the same native Generate bridge can return an ANIMA PNG.
            self.comfy_image = gr.Image(label="ANIMA Comfy result", type="filepath", visible=False, elem_id=f"{self.tab}_h3_comfy_image")
            self.autoplay = gr.Checkbox(value=autoplay_default, visible=False, elem_id=f"{self.tab}_h3_autoplay")
            # Hidden media payload channel for the JS bridge. It carries the img2img input image
            # and ImageStitch Integrated references as data URLs for the Comfy backend.
            self.comfy_images = gr.Textbox(value="", visible=False, elem_id=f"{self.tab}_h3_comfy_images")
            # Hidden backend state channel for the JS bridge.  This lets validation/errors/stop
            # terminate the visible timer without throwing a Gradio traceback.
            self.comfy_state = gr.Textbox(value="", visible=False, elem_id=f"{self.tab}_h3_comfy_state")
            self.download = gr.DownloadButton("Download video", visible=True, elem_id=f"{self.tab}_h3_download")
            # Resolve the path at click time so the button always serves the newest H3 result.
            self.download.click(lambda: __import__("forge_h3.integration", fromlist=["last_video_path"]).last_video_path(),
                                inputs=None, outputs=self.download, queue=False, show_progress=False)
            with gr.Accordion("Components", open=False):
                self.summary = gr.Markdown("")
        self.bound = False
        self.attempts = []
        PANELS.append(self)

    @property
    def inputs(self):
        return [self.output, self.audio, self.audio_shift]

    @property
    def needed(self):
        ids = [f"{self.tab}_{name}" for name in NATIVE_CONTROLS]
        required = ids + ["setting_sd_model_checkpoint", "setting_sd_modules", f"{self.tab}_h3_duration", f"{self.tab}_prompt", f"{self.tab}_width", f"{self.tab}_height", f"{self.tab}_seed", f"{self.tab}_steps"]
        if self.is_img2img:
            required += ['resize_mode', 'img2img_denoising_strength']
        return required

    def bind(self, trigger="?"):
        if self.bound:
            return
        if not _in_blocks():
            self.attempts.append(f"{trigger}: outside Blocks")
            return
        ids = [f"{self.tab}_{name}" for name in NATIVE_CONTROLS]
        needed = self.needed
        if any(name not in COMPONENTS for name in needed):
            self.attempts.append(f"{trigger}: waiting for {', '.join(n for n in needed if n not in COMPONENTS)}")
            logger.debug("Waiting for H3 native controls for %s.", self.tab)
            return
        native = [COMPONENTS[name] for name in ids]
        # Use the same primary sampler/scheduler widgets as Forge's preset
        # callback. An elem_id captured by a third-party extension can point
        # to another field (notably the Hires "Use same scheduler" dropdown).
        from modules_forge import main_entry
        get_primary = getattr(main_entry, 'get_a1111_ui_component', None)
        if callable(get_primary):
            for position, field in ((2, 'sampler_name'), (3, 'scheduler')):
                primary = get_primary(self.tab, field)
                if primary is not None:
                    if primary is not native[position]:
                        logger.warning('Comfy Neo: rebinding %s %s to primary Forge control', self.tab, field)
                    native[position] = primary
        checkpoint = COMPONENTS["setting_sd_model_checkpoint"]
        modules = COMPONENTS["setting_sd_modules"]
        preset = COMPONENTS.get("forge_ui_preset")
        duration = COMPONENTS[f"{self.tab}_h3_duration"]
        prompt_c = COMPONENTS[f"{self.tab}_prompt"]
        width_c = COMPONENTS[f"{self.tab}_width"]
        height_c = COMPONENTS[f"{self.tab}_height"]
        seed_c = COMPONENTS[f"{self.tab}_seed"]
        steps_c = COMPONENTS[f"{self.tab}_steps"]
        resize_mode_c = COMPONENTS['resize_mode'] if self.is_img2img else gr.State(1)
        denoise_c = COMPONENTS['img2img_denoising_strength'] if self.is_img2img else gr.State(1.0)
        defaults = [{key: getattr(c, key) for key in ("minimum", "maximum", "step", "label", "visible", "choices", "interactive")
                     if hasattr(c, key)} for c in native]
        preset_input = [preset] if preset is not None else []
        inputs = [checkpoint, self.output, modules, self.saved] + native + preset_input
        outputs = [self.accordion, self.audio, self.audio_shift, duration, self.status, self.summary, self.saved] + native + preset_input

        def update(value, output, module_values, saved, *values):
            preset_value = values[-1] if preset is not None else None
            values = values[:len(native)]
            info = checkpoint_info(value)
            error = ""
            active = False
            if info:
                try:
                    item = inspect_model(info.filename)
                    active = item is not None and item.role == "dit"
                except H3Error as exc:
                    error = str(exc)
            saved = dict(saved or {"active": False})
            entering = active and not saved.get("active")
            leaving = not active and saved.get("active")
            if entering:
                # DEV33: do not unload native Forge or prewarm Comfy while the
                # user is just browsing models and presets.
                saved["native"] = [dict(config, value=value) for config, value in zip(defaults, values)]
                if preset_value is not None:
                    from modules_forge.presets import is_video
                    saved["native"][0].update(preset_frame_view(is_video(preset_value), values[0]))
            updates = [gr.update() for _ in native]
            frames = values[0]
            if active:
                frames = int(getattr(__import__("modules.shared", fromlist=["opts"]).opts, "h3_default_frames", DEFAULT_FRAMES)) if entering else frames
                updates[0] = gr.update(**frame_view(True, output, frames))
                frames = frame_view(True, output, frames)["value"]
                updates[1] = gr.update(value=1, visible=False)
                if entering:
                    # H3 preset values are now native Comfy KSampler/BasicScheduler
                    # identifiers; reflect user-saved preset choices where available.
                    from modules import shared
                    from .comfy_sampler_ui import available_choices, normalized_sampler, normalized_scheduler
                    samplers, schedulers = available_choices()
                    mode = 'i2i' if self.is_img2img else 't2i'
                    requested_sampler = normalized_sampler(getattr(shared.opts, f'h3_{mode}_sampler', 'res_multistep'))
                    requested_scheduler = normalized_scheduler(getattr(shared.opts, f'h3_{mode}_scheduler', 'simple'))
                    updates[2] = gr.update(value=requested_sampler if requested_sampler in samplers else 'res_multistep')
                    updates[3] = gr.update(value=requested_scheduler if requested_scheduler in schedulers else 'simple')
                    updates[4] = gr.update(value=1.0)
            elif leaving:
                # DEV33: leaving H3 does not stop its Comfy worker. It will be
                # reused if the user returns without starting another engine.
                # On a switch from H3 to Comfy ANIMA, Forge's own ANIMA
                # preset callback supplies native values. Restoring the old
                # pre-H3 controls here can overwrite them with stale H3 values.
                if preset_value == 'comfy_anima':
                    updates = [gr.update() for _ in native]
                else:
                    updates = [gr.update(**config) for config in saved.get("native", [])]
                saved.pop("native", None)
            saved["active"] = active
            summary = ""
            if active:
                try:
                    from .integration import module_paths
                    from .models import resolve_components
                    components = resolve_components(info.filename, module_paths(module_values))
                    mode = ("**Mode:** Ref2VA, reference pictures" if components.mode == "ref2va"
                            else "**Mode:** FL2VA, first and last frame")
                    summary = "  \n".join([mode] + [f"**{ROLE_LABELS[m.role].title()}:** {html.escape(m.path.name)} ({m.quantization})"
                                                    for m in components.models])
                    error = ""
                except H3Error as exc:
                    error = str(exc)
            status = html.escape(error) if error else ("H3 generates audio jointly. This checkbox controls audio in the exported video." if output == "Video" else "Still image uses the first frame of a 5-frame H3 generation.")
            return [gr.update(visible=active), gr.update(visible=active and output == "Video"),
                    gr.update(visible=active and output == "Video"),
                    gr.update(value=f"{frames} frames / {FPS} FPS = {frames / FPS:.2f} seconds" if active else "",
                              visible=active and output == "Video"), status, summary, saved] + updates + (
                                  [gr.update()] if preset is not None else [])

        for event in (checkpoint.change, self.output.change, modules.change):
            event(update, inputs=inputs, outputs=outputs, queue=False, show_progress=False)
        # Frame changes update duration without overwriting any other native controls.
        native[0].change(lambda n, output: gr.update(value=f"{int(n)} frames / {FPS} FPS = {int(n) / FPS:.2f} seconds"),
                         inputs=[native[0], self.output], outputs=[duration], queue=False, show_progress=False)
        gr.context.Context.root_block.load(update, inputs=inputs, outputs=outputs, queue=False, show_progress=False)
        def comfy_run(prompt, width, height, frames, seed, steps, ckpt, mods, media_json,
                      negative='', cfg=4.0, sampler='er_sde', scheduler='simple',
                      resize_mode=1, denoise=1.0):
            import time
            try:
                import json
                payload = json.loads(media_json or '{}')
                from .comfy_sampler_ui import resolve_forge_sampling
                from modules import shared
                engine = 'comfy_anima' if payload.get('engine') == 'anima' else 'h3'
                mode = 'i2i' if self.is_img2img else 't2i'
                sampler, scheduler = resolve_forge_sampling(
                    sampler, scheduler, preset=engine, mode=mode, opts=shared.opts)
                if payload.get('engine') == 'anima':
                    if int(frames) != 1:
                        raise ValueError('ANIMA currently supports Batch Size = 1. Set Batch Size to 1.')
                    image_data = payload.get('init_image') if self.is_img2img else None
                    if self.is_img2img and not image_data:
                        raise ValueError('ANIMA img2img needs a source image in the selected edit tab.')
                    from .anima_backend import generate as anima_generate, ATTENTION_STABLE
                    from modules import shared
                    img = anima_generate(prompt, negative, width, height, steps, cfg, seed,
                                         ckpt, mods, sampler, scheduler,
                                         getattr(shared.opts, 'anima_attention', ATTENTION_STABLE),
                                         init_image_data=image_data,
                                         resize_mode=resize_mode, resize_by=payload.get('resize_by'),
                                         denoise=denoise, edit_mode=payload.get('edit_mode', 'img2img'),
                                         sketch_layer_data=payload.get('sketch_layer'),
                                         mask_data=payload.get('mask_image'),
                                         invert_mask=payload.get('invert_mask', False),
                                         mask_blur=payload.get('mask_blur', 4))
                    if not img:
                        return (gr.update(value=None, visible=False),
                                gr.update(value=None, visible=False), None,
                                f"stopped|{time.time_ns()}")
                    return (gr.update(value=None, visible=False),
                            gr.update(value=img, visible=True), img,
                            f"done-image|{time.time_ns()}|{img}")
                from .comfy_full_backend import activate_session, generate
                # Lazy hand-off: unload any native Forge checkpoint only once
                # the user actually requests a MiniMax generation.
                activate_session()
                info = checkpoint_info(ckpt)
                ckpt_path = info.filename if info is not None else ckpt
                path = generate(prompt, width, height, frames, seed, steps, ckpt_path, mods, media_json,
                                sampler=sampler, scheduler=scheduler)
                if not path:
                    return (gr.update(value=None, visible=False),
                            gr.update(value=None, visible=False), None,
                            f"stopped|{time.time_ns()}")
                from . import integration
                path = integration.adopt_comfy_video(path)
                return (gr.update(value=path, visible=True),
                        gr.update(value=None, visible=False), path,
                        f"done|{time.time_ns()}|{path}")
            except Exception as exc:
                logger.exception("Comfy Neo generation failed")
                return (gr.update(value=None, visible=False),
                        gr.update(value=None, visible=False), None,
                        f"error|{time.time_ns()}|{exc}")

        self.comfy_stop.click(lambda: __import__("forge_h3.comfy_full_backend", fromlist=["interrupt"]).interrupt(), inputs=None, outputs=None, queue=False, show_progress=False)

        # Collect original Forge controls rather than scraping their values from HTML.
        negative_c = COMPONENTS.get(f"{self.tab}_neg_prompt")
        if negative_c is None:
            negative_c = gr.State('')
        self.comfy_generate.click(comfy_run,
            inputs=[prompt_c, width_c, height_c, native[0], seed_c, steps_c,
                    checkpoint, modules, self.comfy_images, negative_c,
                    native[4], native[2], native[3], resize_mode_c, denoise_c],
            outputs=[self.comfy_video, self.comfy_image, self.download, self.comfy_state],
            show_progress=True)
        self.bound = True
        logger.info("H3 native controls connected for %s.", self.tab)


def bind_all(trigger="ui_tabs"):
    for panel in PANELS:
        panel.bind(trigger)
    return []


def check_bindings(*_args):
    # Forge also builds panel instances it never renders; a tab is fine as long as one of its panels is bound
    for tab in dict.fromkeys(panel.tab for panel in PANELS):
        panels = [panel for panel in PANELS if panel.tab == tab]
        if any(panel.bound for panel in panels):
            continue
        missing = sorted({name for panel in panels for name in panel.needed if name not in COMPONENTS})
        attempts = [attempt for panel in panels for attempt in panel.attempts]
        logger.error("H3 native controls were not found for %s (missing: %s; attempts: %s); restart Forge after "
                     "updating it.", tab, ", ".join(missing) or "none", "; ".join(attempts) or "none")


def reset():
    try:
        from .comfy_full_backend import deactivate_session
        deactivate_session('Forge UI reset/unload')
        from .integration import clear_comfy_result
        clear_comfy_result(remove_temp=True)
    except Exception:
        pass
    COMPONENTS.clear()
    PANELS.clear()
