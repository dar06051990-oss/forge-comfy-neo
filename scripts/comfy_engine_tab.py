"""Comfy Neo: native Forge UI and engine-scoped UI Preset choices.

DEV11 layout and original Generate/Stop are preserved unchanged. The single
Forge `UI Preset` Dropdown is updated by top-level Gradio TabItem.select
events. Values are remembered separately per browser session; native Forge
callbacks still own all model/preset switching. No DOM option manipulation.
"""
from __future__ import annotations

from functools import wraps

from modules import script_callbacks


# Register a Comfy-specific ANIMA preset, keeping native Forge `anima` intact.
# This gives the two engines truly separate settings and model selections;
# Forge's original preset callback still controls all the native sliders.

def _register_comfy_anima_preset():
    from modules import shared
    from modules_forge import presets
    from forge_h3.native.presets import _add_enum_member
    name = 'comfy_anima'
    if name in getattr(shared.cmd_opts, 'disable_preset', ()) or 'anima' in getattr(shared.cmd_opts, 'disable_preset', ()):
        return
    arch = _add_enum_member(presets.PresetArch, name)
    source = presets.PresetArch.anima
    for registry in (presets.SAMPLERS, presets.SCHEDULERS, presets.STEPS,
                     presets.CFG, presets.SHIFT):
        registry[arch] = registry[source]
    # Use Comfy's native lower-case sampler names for this preset. The vanilla
    # Forge ANIMA settings remain unchanged and continue using Forge labels.
    presets.SAMPLERS[arch] = 'er_sde'
    presets.SCHEDULERS[arch] = 'beta'
    templates = {}
    presets.register(templates)
    from forge_h3.comfy_sampler_ui import available_choices
    try:
        available_samplers, available_schedulers = available_choices()
    except (OSError, SyntaxError, ValueError):
        available_samplers, available_schedulers = ['er_sde', 'euler', 'res_multistep'], ['simple', 'beta']
    for key, info in templates.items():
        if (key.startswith(name + '_') or key.endswith('_' + name)) and key not in shared.opts.data_labels:
            if getattr(info, 'section', (None,))[0] == 'ui_' + name:
                info.section = ('ui_' + name, 'ANIMA Preset')
                info.category_id = 'comfy_engine'
                if key.endswith('_sampler'):
                    info.component_args = lambda entries=available_samplers: {
                        'choices': entries, 'allow_custom_value': False}
                elif key.endswith('_scheduler'):
                    info.component_args = lambda entries=available_schedulers: {
                        'choices': entries, 'allow_custom_value': False}
            shared.opts.add_option(key, info)

_register_comfy_anima_preset()

COMFY_PRESETS = {'h3': 'MiniMax H3', 'comfy_anima': 'ANIMA'}


def _choices(shared, preset_names=None):
    """Read the actual Forge preset registry, respecting Forge CLI filters."""
    if preset_names is None:
        from modules_forge.presets import PresetArch
        preset_names = PresetArch.choices()
    disabled = set(getattr(shared.cmd_opts, "disable_preset", ()) or ())
    forge = [name for name in preset_names if name not in {'h3', 'comfy_anima'} and name not in disabled]
    if getattr(shared.cmd_opts, "sort_preset", False):
        forge.sort()
    comfy = [(label, name) for name, label in COMFY_PRESETS.items()
             if name in preset_names and name not in disabled and not (name == 'comfy_anima' and 'anima' in disabled)]
    return forge, comfy


def _select_engine(target, current, state, shared, preset_names=None):
    """Pure event logic: swap choices, remember selected value per engine.

    `state` is a per-session Gradio State. We never alter Forge's presets
    registry, global callbacks, or workflow runner.
    """
    import gradio as gr
    forge_choices, comfy_choices = _choices(shared, preset_names)
    comfy_values = [v for _, v in comfy_choices]
    if not forge_choices or not comfy_values:
        # Refuse to show an empty dropdown or select a nonexistent preset.
        raise gr.Error("Comfy Neo: one of the preset lists is empty. Check MiniMax H3 extension and Forge presets.")

    state = dict(state or {})
    previous = state.get("engine", "forge")
    if previous == "forge" and current in forge_choices:
        state["forge"] = current
    elif previous == "comfy" and current in comfy_values:
        state["comfy"] = current

    choices = forge_choices if target == "forge" else comfy_choices
    allowed = forge_choices if target == "forge" else comfy_values
    last = state.get(target)
    if last not in allowed:
        last = current if previous == target and current in allowed else allowed[0]
    state[target] = last
    state["engine"] = target
    return gr.update(choices=choices, value=last), state


def _native_preset_components(main_entry):
    """Same components/order as Forge's forge_main_entry output_targets.

    Unlike the old DEV21 lookup in ui.COMPONENTS, these are obtained from the
    source of truth used by native Forge's own preset callback.
    """
    component = main_entry.get_a1111_ui_component
    return [
        main_entry.ui_checkpoint,
        main_entry.ui_vae,
        main_entry.ui_forge_unet_dtype,
        component('txt2img', 'Steps'),
        component('txt2img', 'Hires steps'),
        component('img2img', 'Steps'),
        component('txt2img', 'sampler_name'),
        component('img2img', 'sampler_name'),
        component('txt2img', 'scheduler'),
        component('img2img', 'scheduler'),
        component('txt2img', 'Size-1'),
        component('img2img', 'Size-1'),
        component('txt2img', 'Size-2'),
        component('img2img', 'Size-2'),
        component('txt2img', 'CFG scale'),
        component('txt2img', 'Hires CFG Scale'),
        component('img2img', 'CFG scale'),
        component('txt2img', 'Distilled CFG Scale'),
        component('txt2img', 'Hires Distilled CFG Scale'),
        component('img2img', 'Distilled CFG Scale'),
        component('txt2img', 'Batch size'),
        component('img2img', 'Batch size'),
    ]


def _sampler_choices(controls):
    """Create engine-scoped updates with an explicit value AND allowed choices.

    Forge-only sampler labels must be restored whenever we leave Comfy ANIMA.
    Never mutate c.choices: each browser session gets its own gr.update.
    """
    return {i: list(getattr(controls[i], 'choices', []) or []) for i in (6, 7, 8, 9)}


def _apply_sampler_lists(preset_value, updates, originals, shared):
    """Enhance Forge's per-preset updates with the correct Dropdown choices."""
    import gradio as gr
    from forge_h3.comfy_sampler_ui import available_choices

    updates = list(updates)
    if len(updates) != 22:
        raise RuntimeError('Unexpected Forge preset control count; refusing partial update')
    if preset_value in ('comfy_anima', 'h3'):
        try:
            samplers, schedulers = available_choices()
        except (OSError, SyntaxError, ValueError) as exc:
            print('[Comfy Neo] Could not inspect Comfy sampler registry:', exc)
            samplers, schedulers = ['er_sde', 'euler', 'res_multistep'], ['simple', 'beta']
        # Both engines use the actual ComfyUI identifiers; H3 used to
        # display Forge's title-case labels even though they were ignored.
        from forge_h3.comfy_sampler_ui import normalized_sampler, normalized_scheduler
        key_prefix = preset_value
        choice_s = [(name, name) for name in samplers]
        choice_c = [(name, name) for name in schedulers]
        for idx, choices, suffix in (
            (6, choice_s, 't2i_sampler'),
            (7, choice_s, 'i2i_sampler'),
            (8, choice_c, 't2i_scheduler'),
            (9, choice_c, 'i2i_scheduler'),
        ):
            setting = f'{key_prefix}_{suffix}'
            wanted = getattr(shared.opts, setting, None)
            wanted = (normalized_sampler(wanted) if idx < 8 else normalized_scheduler(wanted))
            allowed = {value for _, value in choices}
            if wanted not in allowed:
                fallback = ('res_multistep' if idx < 8 else 'simple') if preset_value == 'h3' else ('er_sde' if idx < 8 else 'beta')
                wanted = fallback if fallback in allowed else choices[0][1]
                print('[Comfy Neo] Invalid preset %s; using %s' % (setting, wanted))
            updates[idx] = gr.update(choices=choices, value=wanted)
    else:
        for idx in (6, 7, 8, 9):
            # The native callback knows the correct value for the selected Forge
            # preset. It must be paired with original Forge choices in one event.
            original_value = updates[idx].get('value') if isinstance(updates[idx], dict) else None
            updates[idx] = gr.update(choices=originals[idx], value=original_value)
    return updates


def _bind_preset_navigation():
    """Apply engine lists AND native preset fields in the same top-tab event.

    DEV21 relied on a nested .change event after a programmatic Dropdown
    update; Gradio did not reliably dispatch it after tab moves. Avoid this
    implicit event dependency and update all Forge widgets explicitly.
    """
    import gradio as gr
    from gradio.context import Context
    from modules import shared
    from modules_forge import main_entry

    root = Context.root_block
    if root is None or getattr(root, '_comfy_engine_dev12_bound', False):
        return
    preset = getattr(main_entry, 'ui_forge_preset', None)
    if preset is None:
        raise RuntimeError('Comfy Neo: native UI Preset component unavailable')
    tabs = {getattr(c, 'elem_id', None): c for c in root.blocks.values()
            if str(getattr(c, 'elem_id', '') or '').startswith('tab_')}
    needed = ('tab_txt2img', 'tab_img2img', 'tab_comfy_engine')
    if not all(k in tabs for k in needed):
        raise RuntimeError('Comfy Neo: outer Forge tabs not found')

    controls = _native_preset_components(main_entry)
    if len(controls) != 22 or any(c is None for c in controls):
        raise RuntimeError('Comfy Neo: native Forge slider components not available')
    original_choices = _sampler_choices(controls)
    memory = gr.State(value={'engine': 'forge', 'forge': '', 'comfy': 'h3'})

    def activate(target):
        def callback(current, saved):
            preset_update, state = _select_engine(target, current, saved, shared)
            selected = state[target]
            # DEV33: selecting a tab never unloads or launches a CUDA worker.
            # Native Forge releases the Comfy worker only on its next Generate.
            # ComfyUI handles its own cached models when a Comfy job is submitted.
            # Call exactly the callback Forge uses for selecting its presets.
            # This reads values saved in Settings and restores Steps, CFG,
            # dimensions, sampler and scheduler, not only the top dropdown.
            values = main_entry.on_preset_change(selected)
            values = _apply_sampler_lists(selected, values, original_choices, shared)
            return [preset_update, state] + values
        return callback

    outputs = [preset, memory] + controls
    for tab, engine in (('tab_txt2img', 'forge'),
                        ('tab_img2img', 'forge'),
                        ('tab_comfy_engine', 'comfy')):
        tabs[tab].select(fn=activate(engine), inputs=[preset, memory],
                         outputs=outputs, queue=False, show_progress=False)
    root.load(fn=activate('forge'), inputs=[preset, memory],
              outputs=outputs, queue=False, show_progress=False)

    # Clicking a preset within the same Comfy Neo tab also needs to swap
    # Dropdown choices, not just the Forge-prescribed values. The existing
    # native on_preset_change callback retains ownership of other widgets.
    sampler_outputs = [controls[i] for i in (6, 7, 8, 9)]
    def on_manual_selection(value, saved):
        next_state = dict(saved or {})
        if next_state.get('engine') == 'comfy' and value in COMFY_PRESETS:
            next_state['comfy'] = value
        native = main_entry.on_preset_change(value)
        values = _apply_sampler_lists(value, native, original_choices, shared)
        return [values[i] for i in (6, 7, 8, 9)] + [next_state]

    preset.change(fn=on_manual_selection, inputs=[preset, memory],
                  outputs=sampler_outputs + [memory],
                  queue=False, show_progress=False)

    root._comfy_engine_dev12_bound = True
    print('[Comfy Neo] Lazy model lifecycle: no unload on tab/preset/model selection')

def _bind_dev27_send_image():
    """Link a hidden Comfy-only event to the native Forge img2img components.

    The transfer is performed by Gradio, not by changing raw Canvas DOM:
    LogicalImage.postprocess updates ForgeCanvas's background textarea and its
    existing polling listener redraws the image. Native Forge send actions are
    untouched; the browser intercepts them only while Comfy Neo is active.
    """
    import base64
    import binascii
    import io
    import json
    import gradio as gr
    from gradio.context import Context
    from PIL import Image, ImageOps, UnidentifiedImageError
    from modules.infotext_utils import paste_fields

    root = Context.root_block
    if root is None or getattr(root, '_comfy_dev27_send_bound', False):
        return
    blocks = {getattr(c, 'elem_id', None): c for c in root.blocks.values()}
    payload_widget = blocks.get('comfy_dev27_send_payload')
    trigger = blocks.get('comfy_dev27_send_go')
    forge_img2img = paste_fields.get('img2img', {})
    destination_image = forge_img2img.get('init_img')
    fields = forge_img2img.get('fields', [])

    def dest(api=None, label=None):
        for field in fields:
            if api and getattr(field, 'api', None) == api:
                return field.component
            if label and getattr(field, 'label', None) == label:
                return field.component
        return None

    mapping = [
        ('prompt', dest(label='Prompt')),
        ('negative_prompt', dest(label='Negative prompt')),
        ('steps', dest(api='steps', label='Steps')),
        ('cfg', dest(api='cfg_scale', label='CFG scale')),
        ('width', dest(api='width', label='Size-1')),
        ('height', dest(api='height', label='Size-2')),
        ('seed', dest(api='seed', label='Seed')),
        ('sampler', dest(api='sampler_name')),
        ('scheduler', dest(api='scheduler')),
    ]
    missing = [key for key, component in mapping if component is None]
    if payload_widget is None or trigger is None or destination_image is None or missing:
        raise RuntimeError(f'missing transfer controls {missing}; payload={bool(payload_widget)} trigger={bool(trigger)} image={bool(destination_image)}')

    # All returned targets are the original Forge widgets, including the
    # LogicalImage Gradio textbox backing the original ForgeCanvas.
    controls = [destination_image] + [component for _, component in mapping]

    def accept_image_from_comfy(serialized):
        if not isinstance(serialized, str) or len(serialized) > 115_000_000:
            raise gr.Error('Comfy Neo: invalid or too large transfer payload.')
        try:
            data = json.loads(serialized)
            encoded = data.get('image')
            if not isinstance(encoded, str) or not encoded.startswith('data:image/png;base64,'):
                raise ValueError('Expected a PNG data URL.')
            pixels = base64.b64decode(encoded.partition(',')[2], validate=True)
            if len(pixels) > 80 * 1024 * 1024:
                raise ValueError('PNG exceeds transfer limit.')
            with Image.open(io.BytesIO(pixels)) as source_image:
                if source_image.width * source_image.height > 40_000_000:
                    raise ValueError('Image exceeds transfer pixel limit.')
                image = ImageOps.exif_transpose(source_image).convert('RGBA')
        except (ValueError, TypeError, KeyError, json.JSONDecodeError,
                binascii.Error, UnidentifiedImageError, OSError) as exc:
            raise gr.Error(f'Comfy Neo: failed to transfer image: {exc}') from exc

        def safe_value(key):
            value = data.get(key)
            if value is None:
                return gr.skip()
            if key in ('width', 'height', 'steps', 'seed'):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    return gr.skip()
                if key == 'seed' and not (0 <= value <= 2**53-1):
                    return gr.skip()
                if key != 'seed' and value < 1:
                    return gr.skip()
            elif key == 'cfg':
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    return gr.skip()
            elif key in ('prompt', 'negative_prompt', 'sampler', 'scheduler'):
                if not isinstance(value, str):
                    return gr.skip()
            return value

        print('[Comfy Neo] Result transferred to native ForgeCanvas img2img')
        return [image] + [safe_value(key) for key, _ in mapping]

    trigger.click(fn=accept_image_from_comfy, inputs=[payload_widget],
                  outputs=controls, queue=False, show_progress=False)
    root._comfy_dev27_send_bound = True
    print('[Comfy Neo] Native ForgeCanvas image transfer bridge ready')


def _install_lazy_native_generate_hooks():
    """Release our owned private ComfyUI only when native Forge starts work.

    The native Forge Gradio buttons capture these module functions when the UI
    is built, so install before `create_ui` binds them. Never stop Comfy on
    top-tab selection, preset selection, checkpoint/encoder selection, or on
    return visits without submitting a job.
    """
    from modules import txt2img as native_t2i, img2img as native_i2i

    for module, name in ((native_t2i, 'txt2img'),
                         (native_i2i, 'img2img'),
                         (native_t2i, 'txt2img_upscale')):
        original = getattr(module, name, None)
        if original is None:
            raise RuntimeError(f'Forge native Generate handler missing: {name}')
        if getattr(original, '_comfy_dev33_lazy_native', False):
            continue

        @wraps(original)
        def native_with_comfy_release(*args, _original=original, _handler=name, **kwargs):
            # Called on a real native Forge Generate/Upcale event, not on UI
            # selection. Must finish before Forge starts loading its checkpoint.
            from forge_h3.comfy_full_backend import release_for_native_forge
            release_for_native_forge()
            return _original(*args, **kwargs)

        native_with_comfy_release._comfy_dev33_lazy_native = True
        setattr(module, name, native_with_comfy_release)
    print('[Comfy Neo] Native Forge Generate hooks installed (lazy VRAM hand-off)')


def _install_preset_hook():
    """Attach to Forge UI initialization without editing Forge's source."""
    from modules_forge import main_entry
    _install_lazy_native_generate_hooks()
    existing = main_entry.forge_main_entry
    if getattr(existing, "_comfy_dev12_wrapper", False):
        return

    @wraps(existing)
    def with_comfy_preset_scope(*args, **kwargs):
        result = existing(*args, **kwargs)
        # A failure here should not prevent the entire Forge UI from loading.
        try:
            _bind_preset_navigation()
        except Exception as exc:
            print(f"[Comfy Neo] Preset navigation unavailable: {exc}")
        try:
            from forge_h3.runtime_manager import runtime_state
            if runtime_state()[0]:
                _bind_dev27_send_image()
        except Exception as exc:
            # Sending images is optional; never stop Forge startup on failure.
            print(f"[Comfy Neo] Send-to-img2img bridge unavailable: {exc}")
        return result

    with_comfy_preset_scope._comfy_dev12_wrapper = True
    main_entry.forge_main_entry = with_comfy_preset_scope


# Forge calls on_before_ui before rebuilding the Gradio UI. This hook is idempotent.
script_callbacks.on_before_ui(_install_preset_hook)



def create_comfy_engine_tab():
    import gradio as gr

    from forge_h3.runtime_manager import runtime_state, create_install_screen
    installed, _reason = runtime_state()
    with gr.Blocks(analytics_enabled=False) as panel:
        if not installed:
            create_install_screen()
            return [(panel, "Comfy Neo", "comfy_engine")]
        # Host must be a REAL Gradio Column rather than an HTML content box.
        # In Gradio 4, native rows/sliders rely on the surrounding flex layout.
        with gr.Column(elem_id="comfy-native-workspace", scale=1, min_width=0):
            # This HTML component is only an absolutely positioned menu, never
            # the parent of txt2img/img2img controls or their DOM elements.
            gr.HTML(r"""
<style>
#comfy-native-workspace {
  position: relative !important;
  width: 100%; max-width: 100%; min-width: 0 !important;
  box-sizing: border-box; gap: 0 !important;
}
#comfy-native-t2i-slot, #comfy-native-i2i-slot {
  width: 100%; max-width: 100%; min-width: 0 !important;
  box-sizing: border-box; flex: 1 1 auto !important;
  margin: 0 !important; padding: 0 !important; gap: 0 !important;
}
/* The selector floats over the tab header; its HTML wrapper consumes no row. */
#comfy-native-mode-overlay {
  position: absolute !important; z-index: 40 !important;
  top: 3px; right: 8px; width: fit-content !important;
  min-width: 0 !important; max-width: 230px;
  height: 0 !important; min-height: 0 !important;
  margin: 0 !important; padding: 0 !important;
  border: 0 !important; background: transparent !important;
  overflow: visible !important;
}
#comfy-native-mode-overlay > * { overflow: visible !important; }
#comfy-native-mode-overlay .comfy-native-mode {
  display: flex; align-items: center; gap: 6px; margin: 0; padding: 0;
}
#comfy-native-mode-overlay .comfy-native-mode label {
  display: inline; margin: 0; font-size: 12px; line-height: 29px;
  color: var(--body-text-color, #ddd);
}
#comfy-native-mode-select {
  font: inherit; font-size: 12px; min-width: 134px; max-width: 168px;
  height: 29px; padding: 2px 20px 2px 8px;
  color: var(--body-text-color, #fff);
  background: var(--input-background-fill, #454545);
  border: 1px solid var(--border-color-primary, #666);
  border-radius: 6px; cursor: pointer;
}
#comfy-native-mode-select option {
  color: var(--body-text-color, #fff);
  background: var(--input-background-fill, #454545);
}
#comfy-native-hint {
  display: none; position: absolute; top: 36px; right: 0;
  max-width: 450px; width: max-content;
  background: var(--body-background-fill, #222);
  padding: 6px 9px; border: 1px solid #cc8e70;
  border-radius: 5px; color: #ffc5ab; font-size: 12px;
  pointer-events: none;
}
</style>
<div class="comfy-native-mode">
  <label for="comfy-native-mode-select">Mode</label>
  <select id="comfy-native-mode-select" aria-label="Comfy Neo generation mode">
    <option value="txt2img" selected>txt2img</option>
    <option value="img2img">img2img</option>
  </select>
</div>
<div id="comfy-native-hint" role="alert" aria-live="polite"></div>
            """, elem_id="comfy-native-mode-overlay")

            # These are native Gradio flex columns, NOT <div>s inside HTML.
            # JS moves original tab contents here; no new UI is instantiated.
            with gr.Column(elem_id="comfy-native-t2i-slot", scale=1, min_width=0):
                pass
            with gr.Column(elem_id="comfy-native-i2i-slot", scale=1, min_width=0):
                pass

            # DEV27: invisible Gradio bridge for the *actual* Comfy result.
            # Unlike Forge's Send-to buttons, this never reads an obsolete
            # image from the native gallery obscured by the Comfy preview.
            with gr.Row(visible=False):
                gr.Textbox(elem_id="comfy_dev27_send_payload", visible=False)
                gr.Button("Comfy send to img2img", elem_id="comfy_dev27_send_go", visible=False)

    return [(panel, "Comfy Neo", "comfy_engine")]


script_callbacks.on_ui_tabs(create_comfy_engine_tab)
