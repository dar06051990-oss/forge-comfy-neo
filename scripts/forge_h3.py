"""Forge Neo discovers this always-on script when the extension is installed."""

import sys
import traceback
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules import script_callbacks, scripts, shared

from forge_h3 import integration, ui
from forge_h3.native import compat

ENABLED = False

_problems = compat.check()

if _problems:
    compat.report(_problems)
else:
    try:
        from forge_h3.native import patches

        patches.apply()
        ENABLED = True
    except Exception:
        print("[MiniMax H3] failed to enable, Forge Neo is unchanged:")
        traceback.print_exc()


class Script(scripts.Script):
    sorting_priority = 10
    create_group = False

    def title(self):
        return "MiniMax H3"

    def show(self, is_img2img):
        return scripts.AlwaysVisible if ENABLED else False

    def ui(self, is_img2img):
        self.panel = ui.Panel(is_img2img)
        self.infotext_fields = [(self.panel.audio_shift, "H3 Audio shift")]
        return self.panel.inputs

    def before_process(self, p, output="Video", include_audio=True, audio_shift=3.0, *args):
        integration.script_before_process(p, output, include_audio, audio_shift)

    def process(self, p, *args):
        integration.process(p)

    def process_before_every_sampling(self, p, *args, **kwargs):
        integration.before_sampling(p, kwargs["noise"])

    def post_sample(self, p, ps, *args):
        integration.after_sampling(p)

    def postprocess(self, p, processed, *args):
        integration.postprocess(p, processed)


def settings():
    """Distinct per-model Comfy presets, plus shared image/video output."""
    from modules.options import categories
    import gradio as gr
    from forge_h3.comfy_output import register as register_output

    categories.register_category('comfy_engine', 'Comfy Neo')
    for key, info in shared.opts.data_labels.items():
        # Legacy H3 preset was registered as a normal Forge preset.
        if getattr(info, 'section', None) in (
            ('ui_h3', 'H3'), ('ui_h3', 'MiniMax H3 Preset')):
            info.section = ('ui_h3', 'MiniMax H3 Preset')
            info.category_id = 'comfy_engine'
        # Comfy-only ANIMA pseudo-preset created by the native preset registry.
        if key.startswith('comfy_anima_') and getattr(info, 'section', (None,))[0] == 'ui_comfy_anima':
            info.section = ('ui_comfy_anima', 'ANIMA Preset')
            info.category_id = 'comfy_engine'

    register_output(shared, gr)
    # Always available, even when the private ComfyUI runtime is not installed.
    from forge_h3.runtime_manager import settings_component
    runtime_option = shared.OptionInfo(
        None, 'Private Runtime', component=settings_component,
        section=('comfy_neo_runtime', 'Runtime'), category_id='comfy_engine')
    runtime_option.do_not_save = True
    shared.opts.add_option('comfy_neo_runtime_control', runtime_option)
    from forge_h3.comfy_sampler_ui import available_choices, normalized_sampler, normalized_scheduler
    try:
        samplers, schedulers = available_choices()
    except (OSError, SyntaxError, ValueError):
        samplers, schedulers = ['res_multistep', 'euler', 'er_sde'], ['simple', 'beta']
    for suffix, entries, convert in (
        ('sampler', samplers, normalized_sampler),
        ('scheduler', schedulers, normalized_scheduler),
    ):
        for mode in ('t2i', 'i2i'):
            key = f'h3_{mode}_{suffix}'
            info = shared.opts.data_labels.get(key)
            if info is not None:
                info.component_args = lambda values=entries: {'choices': list(values), 'allow_custom_value': False}
                original = shared.opts.data.get(key, info.default)
                normalized = convert(original)
                fallback = 'res_multistep' if suffix == 'sampler' else 'simple'
                if normalized not in entries:
                    normalized = fallback
                info.default = fallback
                if key in shared.opts.data:
                    shared.opts.data[key] = normalized

    shared.opts.add_option('h3_default_frames', shared.OptionInfo(
        90, 'Default video frames (MiniMax H3, 5–359)',
        section=('comfy_video_output', 'Video Output'), category_id='comfy_engine'))

    from forge_h3.workflow_native import (
        ATTENTION_MODES, ATTENTION_STABLE, ATTENTION_CK, ATTENTION_CK_LEGACY)
    if shared.opts.data.get('h3_attention_mode') == ATTENTION_CK_LEGACY:
        shared.opts.data['h3_attention_mode'] = ATTENTION_CK
    shared.opts.add_option('h3_attention_mode', shared.OptionInfo(
        ATTENTION_STABLE, 'Video attention backend (MiniMax H3)', gr.Dropdown,
        lambda: {'choices': list(ATTENTION_MODES)},
        section=('comfy_video_output', 'Video Output'), category_id='comfy_engine'))

    from forge_h3.anima_backend import ATTENTION_CHOICES, ATTENTION_STABLE as ANIMA_STABLE
    shared.opts.add_option('anima_attention', shared.OptionInfo(
        ANIMA_STABLE, 'Image attention backend (ANIMA)', gr.Dropdown,
        lambda: {'choices': list(ATTENTION_CHOICES)},
        section=('comfy_image_output', 'Image Output'), category_id='comfy_engine'))

script_callbacks.on_ui_settings(settings)
# a rebuilt UI starts with no panels: the ones of a previous build can never bind again
script_callbacks.on_before_ui(ui.reset)
script_callbacks.on_after_component(ui.capture)
script_callbacks.on_ui_tabs(ui.bind_all)
script_callbacks.on_app_started(ui.check_bindings)
script_callbacks.on_script_unloaded(ui.reset)
