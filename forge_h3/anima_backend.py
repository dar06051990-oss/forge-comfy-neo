"""ANIMA txt2img + base img2img through Forge Neo's private ComfyUI worker.

DEV26 keeps standard LoraLoader for ordinary LoRA and edit LoRA files.
This image-to-image workflow uses VAE-encoded init latents; specialised
instruction-edit LoRAs may additionally require a reference conditioning
node/architecture-specific patch not provided here.
"""
from __future__ import annotations

import base64
import io
import json
import math
import re
import secrets
import shutil
import os
import queue
import tempfile
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps, ImageFilter, ImageChops

BASE_ENCODER = 'qwen_3_06b_base.safetensors'
BASE_VAE = 'qwen_image_vae.safetensors'
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / 'comfy_anima_txt2img_api_workflow.json'
ATTENTION_STABLE = 'PyTorch (stable)'
ATTENTION_CK = 'Comfy Kitchen (experimental)'
ATTENTION_CHOICES = (ATTENTION_STABLE, ATTENTION_CK)
LORA_PATTERN = re.compile(r'<lora\s*:\s*([^<>]+)>', re.IGNORECASE)


def parse_lora_tags(text):
    """Remove only LoRA directives; leave trigger words and prompt order intact.

    Forge/QuickPick syntax: <lora:name:weight> (one weight for both model and
    CLIP), optionally <lora:name:model_weight:clip_weight>. Names may include
    Comfy subfolders separated by forward or backslash. Empty weights are rejected.
    """
    selected = []
    def remove(match):
        raw = match.group(1).strip()
        fields = raw.split(':')
        weights = []
        while len(fields) > 1 and len(weights) < 2:
            try:
                strength = float(fields[-1].strip())
            except ValueError:
                break
            if not math.isfinite(strength) or not -100 <= strength <= 100:
                raise ValueError('ANIMA LoRA strength must be a finite number between -100 and 100.')
            weights.insert(0, strength)
            fields.pop()
        # Colons are not legal within Windows filenames. Leftover fields imply
        # an unsupported/malformed tag, not a LoRA that should be ignored.
        if len(fields) != 1 or not fields[0].strip():
            raise ValueError(f'Invalid ANIMA LoRA tag <lora:{raw}>. Expected <lora:name:weight>.')
        name = fields[0].strip().replace('\\', '/')
        if name.startswith('/') or name.startswith('~') or re.match(r'^[a-zA-Z]:', name) or '..' in name.split('/'):
            raise ValueError(f'ANIMA LoRA name must be relative to the LoRA folders: {name!r}.')
        if not weights:
            model_strength = clip_strength = 1.0
        elif len(weights) == 1:
            model_strength = clip_strength = weights[0]
        else:
            model_strength, clip_strength = weights
        selected.append((name, model_strength, clip_strength))
        return ''
    cleaned = LORA_PATTERN.sub(remove, str(text or ''))
    if re.search(r'<\s*lora\s*:', cleaned, flags=re.IGNORECASE):
        raise ValueError('ANIMA: incomplete <lora:...> tag in the prompt.')
    return cleaned, selected


def _lora_search_roots():
    return _comfy_search_roots('LoraLoader')


def resolve_lora_choice(name, choices, roots):
    """Resolve a Forge/QuickPick LoRA tag to a *real* Comfy-visible file.

    QuickPick can insert a display title (``AnimeEdit V2``) instead of the
    physical filename (``AnimeEditV2.safetensors``). Try exact matching first,
    then a separator-insensitive match only when it identifies ONE real file.
    No guessing between two different files, or between model directories.
    """
    import os

    requested = str(name).strip().replace('\\', '/').casefold()
    if not requested or requested.startswith('/') or '..' in requested.split('/'):
        raise ValueError(f'Invalid relative LoRA name: {name!r}')
    explicit_folder = '/' in requested

    def normalized(value):
        value = str(value).replace('\\', '/').casefold()
        if value.endswith(('.safetensors', '.ckpt', '.pt')):
            value = str(Path(value).with_suffix(''))
        segments = value.split('/') if explicit_folder else [value.rsplit('/', 1)[-1]]
        # Normalize display separators but retain the directory structure,
        # so a qualified tag can never silently select a different folder.
        return '/'.join(re.sub(r'[\s._-]+', '', segment) for segment in segments)

    def collect(*, alias=False):
        matches = {}
        for raw in choices:
            rel = Path(str(raw).replace('\\', '/'))
            if rel.is_absolute() or '..' in rel.parts:
                continue
            rel_name = str(raw).replace('\\', '/').casefold()
            noext = str(Path(rel_name).with_suffix(''))
            if alias:
                # Only compare aliases after an exact search failed. A tag
                # containing a directory must resolve inside that directory.
                is_match = normalized(requested) == normalized(rel_name)
            elif explicit_folder:
                is_match = requested in (rel_name, noext)
            else:
                is_match = requested in (Path(rel_name).name, Path(noext).name)
            if not is_match:
                continue
            # Match Comfy get_full_path: first existing root wins.
            for root in roots:
                physical = Path(root) / rel
                if physical.is_file():
                    key = os.path.normcase(os.path.realpath(physical))
                    matches.setdefault(key, []).append(str(raw))
                    break
        return matches

    matches = collect()
    used_alias = False
    if not matches:
        matches = collect(alias=True)
        used_alias = bool(matches)
    if len(matches) > 1:
        names = sorted({choice for aliases in matches.values() for choice in aliases})
        raise ValueError(
            f'ANIMA LoRA {name!r} is ambiguous: {", ".join(names[:10])}. '
            'Use the exact file name and its relative subfolder, e.g. '
            '<lora:ANIMA/AnimeEditV2:1.0>.')
    if not matches:
        raise ValueError(
            f'ANIMA LoRA {name!r} is not visible to embedded ComfyUI. '
            'Check the LoRA folder, use the exact filename in QuickPick, then '
            'restart Forge if model paths were changed.')
    aliases = next(iter(matches.values()))
    choice = sorted(aliases, key=lambda value: (-len(Path(value.replace('\\','/')).parts), value.casefold()))[0]
    if used_alias:
        print(f'[Comfy Neo ANIMA] LoRA alias: {name!r} -> {choice!r}', flush=True)
    return choice


def build_graph(unet, clip, vae, prompt, negative, width, height,
                steps, cfg, seed, sampler='er_sde', scheduler='simple',
                attention=ATTENTION_STABLE, loras=(), init_image_name=None, denoise=1.0,
                use_mask=False):
    """Pure ANIMA API workflow builder; LoRAs are standard Comfy LoraLoader nodes.

    The *caller* resolves prompt LoRA tags into Comfy-visible filenames before
    invoking this function. Both prompt CLIP encoders share the final LoRA CLIP.
    """
    width, height, steps = int(width), int(height), int(steps)
    cfg, seed = float(cfg), int(seed)
    if width < 64 or height < 64 or width % 16 or height % 16:
        raise ValueError('ANIMA width and height must be multiples of 16 (minimum 64).')
    if not 0.0 <= float(denoise) <= 1.0:
        raise ValueError('ANIMA img2img Denoising Strength must be 0.0 to 1.0.')
    if steps < 1 or not 0 <= cfg <= 100:
        raise ValueError('ANIMA: invalid steps or CFG.')
    if re.search(r'<lora\s*:', str(prompt) + str(negative), re.I):
        raise ValueError('ANIMA: LoRA tags must be parsed before building the workflow.')
    if attention not in ATTENTION_CHOICES:
        raise ValueError('Unsupported ANIMA attention mode')
    if seed < 0:
        seed = secrets.randbelow(2**53)  # JS/Gradio Number can reuse this seed exactly
    # Read the installable API workflow template (same arrangement as MiniMax
    # presets). Only parameter values and an optional attention link are
    # changed; standard node classes and graph connections stay in JSON.
    graph = json.loads(TEMPLATE_PATH.read_text(encoding='utf-8'))
    graph['1']['inputs']['unet_name'] = str(unet)
    graph['2']['inputs']['clip_name'] = str(clip)
    graph['3']['inputs']['vae_name'] = str(vae)
    graph['4']['inputs']['text'] = str(prompt)
    graph['5']['inputs']['text'] = str(negative)
    if init_image_name is None:
        graph['6']['inputs'].update(width=width, height=height, batch_size=1)
    else:
        # Standard ComfyUI img2img: uploaded image -> VAEEncode -> KSampler.
        # LoadImage is scoped to our private Comfy worker's input directory.
        name = str(init_image_name)
        if not name.startswith('anima_dev26_') or '/' in name or '\\' in name:
            raise ValueError('ANIMA: unsafe Comfy input-image filename.')
        graph['11'] = {'class_type': 'LoadImage', 'inputs': {'image': name}}
        graph['6'] = {'class_type': 'VAEEncode', 'inputs': {
            'pixels': ['11', 0], 'vae': ['3', 0]}}
        if use_mask:
            # LoadImage's second output is inverse alpha (white=edit).
            # This uses the standard ComfyUI inpainting noise mask, not LLLite.
            graph['12'] = {'class_type': 'SetLatentNoiseMask', 'inputs': {
                'samples': ['6', 0], 'mask': ['11', 1]}}
            graph['7']['inputs']['latent_image'] = ['12', 0]
    from .comfy_sampler_ui import validate
    sampler_name, scheduler_name = validate(sampler, scheduler)
    graph['7']['inputs'].update(seed=seed, steps=steps, cfg=cfg,
                               sampler_name=sampler_name,
                               scheduler=scheduler_name, denoise=float(denoise))
    # DEV20: preview-only, no automatic SaveImage in runtime/core/output.
    graph['9'] = {'class_type': 'PreviewImage', 'inputs': {'images': ['8', 0]}}
    # Apply each LoRA in original tag order. The connections mirror the user's
    # PromptLora workflow: model and CLIP both pass through LoRA, so text
    # triggers still affect conditioning in their ORIGINAL prompt position.
    model_input, clip_input = ['1', 0], ['2', 0]
    for index, (lora_name, strength_model, strength_clip) in enumerate(loras, 1):
        node_id = f'anima_lora_{index}'
        graph[node_id] = {
            'class_type': 'LoraLoader',
            'inputs': {
                'model': model_input,
                'clip': clip_input,
                'lora_name': str(lora_name),
                'strength_model': float(strength_model),
                'strength_clip': float(strength_clip),
            },
        }
        model_input, clip_input = [node_id, 0], [node_id, 1]
    graph['4']['inputs']['clip'] = clip_input
    graph['5']['inputs']['clip'] = clip_input
    if attention == ATTENTION_CK:
        graph['7']['inputs']['model'] = ['10', 0]
        graph['10']['inputs']['model'] = model_input
    else:
        graph['7']['inputs']['model'] = model_input
        graph.pop('10', None)
    return graph, seed


def _actual_path(selected):
    """Resolve only real files selected by Forge; never guess a checkpoint."""
    from .integration import checkpoint_info
    info = checkpoint_info(selected)
    if info is not None and Path(info.filename).is_file():
        return Path(info.filename)
    p = Path(str(selected or ''))
    if p.is_file():
        return p
    raise ValueError('ANIMA checkpoint not found. Select the actual ANIMA checkpoint in Forge.')


def _module_paths(selected):
    """Allow only the Qwen3/VAEs already available in Forge's model paths."""
    from modules_forge import main_entry
    resolved = []
    for v in selected or []:
        p = Path(str(main_entry.module_list.get(str(v), v)))
        if p.is_file():
            resolved.append(p)
    return resolved


def _comfy_search_roots(class_type):
    """Mirror the stock embedded Comfy folder_paths order without importing Comfy
    (which would load a second PyTorch into the Forge process).

    The extra_model_paths.yaml is generated by the verified embedded bridge.
    Comfy appends its paths after the stock core/models paths.
    """
    from . import comfy_full_backend as backend
    import yaml

    category = {
        'UNETLoader': 'diffusion_models',
        'CLIPLoader': 'text_encoders',
        'VAELoader': 'vae',
        'LoraLoader': 'loras',
        'LoraLoaderModelOnly': 'loras',
    }.get(class_type)
    if category is None:
        raise ValueError(f'ANIMA: unsupported loader type {class_type!r}')
    core = Path(backend._cfg()['comfy_root'])
    defaults = {
        'diffusion_models': ['unet', 'diffusion_models'],
        'text_encoders': ['text_encoders', 'clip'],
        'vae': ['vae'],
        'loras': ['loras'],
    }
    roots = [core / 'models' / name for name in defaults[category]]

    config_file = Path(__file__).resolve().parents[1] / 'runtime' / 'extra_model_paths.yaml'
    if config_file.is_file():
        config = yaml.safe_load(config_file.read_text(encoding='utf-8')) or {}
        for group in config.values():
            if not isinstance(group, dict):
                continue
            base = group.get('base_path')
            is_default = group.get('is_default', False)
            raw = group.get(category, '')
            if not isinstance(raw, str):
                continue
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                p = Path(line)
                if not p.is_absolute():
                    p = (Path(base) / p) if base else config_file.parent / p
                # Honor Comfy's is_default insertion order. Ordinary bridge
                # config has no is_default, and paths are appended.
                if p not in roots:
                    if is_default:
                        roots.insert(0, p)
                    else:
                        roots.append(p)
    return roots


def _resolve_choice_path(selected, choices, roots):
    """Find the exact Comfy dropdown entry resolving to selected physical file.

    Comfy may list the SAME file under several relative names because its
    model directories overlap (e.g. 'ANIMA/x.safetensors' and 'x.safetensors').
    Comfy resolves each entry by searching roots IN ORDER. We do likewise;
    if a different file shadows an entry, it is never selected by mistake.
    """
    import os
    target = Path(selected)
    if not target.is_file():
        raise FileNotFoundError(f'ANIMA: selected model file does not exist: {target}')
    target_key = os.path.normcase(os.path.realpath(target))
    matches = []
    for raw_choice in choices:
        relative = Path(str(raw_choice).replace('\\', '/'))
        if relative.is_absolute() or '..' in relative.parts:
            continue
        for root in roots:
            candidate = root / relative
            if not candidate.is_file():
                continue
            if os.path.normcase(os.path.realpath(candidate)) == target_key:
                matches.append(str(raw_choice))
            # Comfy get_full_path stops at the FIRST matching root.
            break
    if matches:
        # Prefer the most specific relative name if both aliases lead to
        # the same physical file, without relying on arbitrary list order.
        return sorted(matches, key=lambda name: (-len(Path(name.replace('\\','/')).parts), name.lower()))[0]
    return None


def _choice(class_type, field, selected, fallback=None):
    from . import comfy_full_backend as backend
    choices = backend._choice_values(class_type, field)
    if not choices:
        raise RuntimeError(f'ANIMA: Comfy did not report {class_type}.{field} choices.')
    roots = _comfy_search_roots(class_type)
    match = _resolve_choice_path(selected, choices, roots)
    if match is not None:
        print(f'[Comfy Neo ANIMA] {class_type}: {Path(selected).name} -> {match}')
        return match
    raise ValueError(
        'ANIMA: the exact selected file is not visible to the running private '
        f'Comfy worker: {selected}. The old model directory mapping may still '
        'be active. Restart Forge so the embedded worker reloads its paths; '
        'do not select a different model merely because its name matches.'
    )



# DEV26: prepare the Forge img2img input before VAEEncode; keep UI settings real.
def prepare_img2img_image(data_url, width, height, resize_mode=1, resize_by=None):
    """Return PNG data URL and actual dimensions, without touching Comfy or Torch.

    Forge resize modes are indexed: 0 Just Resize, 1 Crop and Resize,
    2 Resize and Fill, 3 Latent Upscale (unsupported in this VAE base),
    4 Preserve Aspect Ratio. Never silently substitute another mode.
    """
    from . import comfy_full_backend as backend
    blob, _ext = backend._decode_data_url(data_url)
    if len(blob) > 50 * 1024 * 1024:
        raise ValueError('ANIMA img2img: source image exceeds 50 MiB.')
    try:
        with Image.open(io.BytesIO(blob)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert('RGB')
    except (OSError, ValueError) as exc:
        raise ValueError('ANIMA img2img: invalid input image.') from exc
    if resize_by is not None:
        factor = float(resize_by)
        if not 0.5 <= factor <= 4.0 or not math.isfinite(factor):
            raise ValueError('ANIMA img2img: Resize by must be 0.5 to 4.0.')
        width, height = round(image.width * factor), round(image.height * factor)
    width, height = int(width), int(height)
    if min(width, height) < 64 or max(width, height) > 4096 or width % 16 or height % 16:
        raise ValueError(f'ANIMA img2img: output size {width}x{height} must be 64-4096 and divisible by 16.')
    resize_mode = int(resize_mode)
    resample = Image.Resampling.LANCZOS
    if resize_mode == 0:
        image = image.resize((width, height), resample)
    elif resize_mode == 1:
        image = ImageOps.fit(image, (width, height), method=resample, centering=(0.5, 0.5))
    elif resize_mode in (2, 4):
        # Both preserve image geometry. 'Resize and Fill' extends edge pixels
        # rather than adding black bars; Preserve Aspect Ratio uses a flat border.
        contained = ImageOps.contain(image, (width, height), method=resample)
        if resize_mode == 2:
            # stretched full-canvas background, original aspect-fit foreground
            background = image.resize((width, height), resample)
        else:
            background = Image.new('RGB', (width, height), image.getpixel((image.width//2, image.height//2)))
        background.paste(contained, ((width-contained.width)//2, (height-contained.height)//2))
        image = background
    elif resize_mode == 3:
        raise ValueError('ANIMA img2img: Latent Upscale resize mode is not implemented in v1.0.0. Select Just Resize, Crop and Resize, Resize and Fill or Preserve Aspect Ratio.')
    else:
        raise ValueError(f'ANIMA img2img: unsupported Resize Mode {resize_mode}.')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'), width, height


# DEV30: use the SAME source, overlay and mask for all Forge img2img submodes.
# Masked output is also composited over the ORIGINAL pixels after sampling so
# the unchanged area is protected from VAE reconstruction differences.
EDIT_MODES = {'img2img', 'sketch', 'inpaint', 'inpaint_sketch', 'inpaint_upload'}


def _read_browser_image(data_url, label, mode='RGBA'):
    from . import comfy_full_backend as backend
    if not isinstance(data_url, str) or not data_url.startswith('data:image/'):
        raise ValueError(f'ANIMA {label}: missing image from Forge.')
    blob, _ = backend._decode_data_url(data_url)
    if not blob or len(blob) > 50 * 1024 * 1024:
        raise ValueError(f'ANIMA {label}: empty image or greater than 50 MiB.')
    try:
        with Image.open(io.BytesIO(blob)) as inp:
            inp.load()
            if inp.width * inp.height > 40_000_000:
                raise ValueError(f'ANIMA {label}: image exceeds 40 megapixels.')
            return ImageOps.exif_transpose(inp).convert(mode)
    except (OSError, ValueError) as exc:
        raise ValueError(f'ANIMA {label}: cannot read the input image: {exc}') from exc


def _resize_anima_image(image, size, resize_mode, is_mask=False):
    width, height = size
    mode = int(resize_mode)
    method = Image.Resampling.BILINEAR if is_mask else Image.Resampling.LANCZOS
    if mode == 0:
        return image.resize(size, method)
    if mode == 1:
        return ImageOps.fit(image, size, method=method, centering=(0.5, 0.5))
    if mode in (2, 4):
        inside = ImageOps.contain(image, size, method=method)
        if is_mask:
            canvas = Image.new('L', size, 0)
        elif mode == 2:
            canvas = image.resize(size, method)
        else:
            canvas = Image.new(image.mode, size, image.getpixel((image.width//2, image.height//2)))
        canvas.paste(inside, ((width - inside.width)//2, (height - inside.height)//2))
        return canvas
    if mode == 3:
        raise ValueError('ANIMA img2img: Latent Upscale resize is not supported. Choose another Resize Mode.')
    raise ValueError(f'ANIMA img2img: unknown Resize Mode {resize_mode}.')


def prepare_anima_edit_image(init_image_data, width, height, resize_mode=1,
                             resize_by=None, edit_mode='img2img',
                             sketch_layer_data=None, mask_data=None,
                             invert_mask=False, mask_blur=4):
    """Return PNG data URL, actual dimensions and optional (RGB, MASK) restore pair.

    Inpaint PNG contains the original RGB pixels and inverted mask in alpha:
    Comfy LoadImage outputs MASK = 1 - alpha, suitable for SetLatentNoiseMask.
    """
    if edit_mode not in EDIT_MODES:
        raise ValueError(f'ANIMA img2img: unsupported mode {edit_mode!r}.')
    if edit_mode == 'img2img':
        png, w, h = prepare_img2img_image(init_image_data, width, height, resize_mode, resize_by)
        return png, w, h, None
    source = _read_browser_image(init_image_data, 'img2img background', 'RGBA')
    base_size = source.size
    layer = None
    if edit_mode in ('sketch', 'inpaint', 'inpaint_sketch'):
        if not sketch_layer_data:
            raise ValueError(f'ANIMA {edit_mode}: no drawing layer from Forge canvas.')
        layer = _read_browser_image(sketch_layer_data, 'drawing layer', 'RGBA')
        if layer.size != base_size:
            raise ValueError('ANIMA edit: canvas layer size does not match its background.')
    if edit_mode in ('sketch', 'inpaint_sketch'):
        source = Image.alpha_composite(source, layer)
    mask = None
    if edit_mode in ('inpaint', 'inpaint_sketch'):
        mask = layer.getchannel('A')
    elif edit_mode == 'inpaint_upload':
        raw_mask = _read_browser_image(mask_data, 'uploaded mask', 'RGBA')
        if raw_mask.size != base_size:
            raise ValueError('ANIMA Inpaint upload: image and mask must have the same dimensions.')
        # Uploaded masks are treated as luminosity (white=edit, black=keep).
        # Their alpha is not implicitly used as the mask: grayscale is explicit.
        mask = raw_mask.convert('RGB').convert('L')
    if mask is not None:
        if bool(invert_mask):
            mask = ImageOps.invert(mask)
        if mask.getextrema()[1] == 0:
            raise ValueError(f'ANIMA {edit_mode}: paint a mask first (white is the editable area).')
    if resize_by is not None:
        scale = float(resize_by)
        if not math.isfinite(scale) or not 0.5 <= scale <= 4:
            raise ValueError('ANIMA edit: Resize by must be 0.5 to 4.0.')
        width, height = round(base_size[0] * scale), round(base_size[1] * scale)
    width, height = int(width), int(height)
    if min(width, height) < 64 or max(width, height) > 4096 or width % 16 or height % 16:
        raise ValueError(f'ANIMA edit: size {width}x{height} must be 64-4096 and divisible by 16.')
    size = (width, height)
    source_rgb = _resize_anima_image(source.convert('RGB'), size, resize_mode)
    if mask is not None:
        mask = _resize_anima_image(mask, size, resize_mode, is_mask=True)
        try:
            amount = float(mask_blur)
        except (TypeError, ValueError):
            amount = 0.0
        if not math.isfinite(amount) or not 0 <= amount <= 64:
            raise ValueError('ANIMA Inpaint: Mask blur must be between 0 and 64.')
        if amount > 0:
            mask = mask.filter(ImageFilter.GaussianBlur(radius=amount / 2))
        if mask.getextrema()[1] == 0:
            raise ValueError('ANIMA Inpaint: the mask became empty after resizing.')
        png_image = source_rgb.convert('RGBA')
        png_image.putalpha(ImageOps.invert(mask))
        preserve = (source_rgb, mask)
    else:
        png_image = source_rgb
        preserve = None
    buffer = io.BytesIO()
    png_image.save(buffer, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'), width, height, preserve


def _protect_unmasked_pixels(preview_file, preserve, fmt):
    """Blend only the sampled area; avoid modifying the original outside mask."""
    source_rgb, mask = preserve
    with Image.open(preview_file) as generated:
        sampled = generated.convert('RGB')
    if sampled.size != source_rgb.size:
        raise RuntimeError('ANIMA Inpaint: result dimensions differ from the input mask.')
    composited = Image.composite(sampled, source_rgb, mask)
    fmt = _preview_format(fmt)
    if fmt == 'PNG':
        composited.save(preview_file, 'PNG')
    elif fmt == 'JPG':
        composited.save(preview_file, 'JPEG', quality=100, subsampling=0)
    else:
        composited.save(preview_file, 'WEBP', lossless=True, quality=100, method=6)


# DEV20 — private worker creates a temporary preview; only an explicit Forge
# setting can save a permanent copy. These helpers do not load Torch.
FORMATS = ('PNG', 'JPG', 'WEBP')

def _preview_format(value):
    value = str(value or 'PNG').strip().upper()
    if value == 'JPEG': value = 'JPG'
    if value not in FORMATS:
        raise ValueError(f'ANIMA output format must be PNG, JPG or WEBP, got {value!r}')
    return value


def _convert_preview(src, seed, fmt):
    """Copy lossless PNG or convert to full-quality JPG/lossless WEBP for preview.

    The OS temp directory holds only a preview for Gradio, not a saved image.
    The source is the temporary output of Comfy's PreviewImage node.
    """
    fmt = _preview_format(fmt)
    preview_dir = Path(tempfile.gettempdir()) / 'forge-anima-results'
    preview_dir.mkdir(parents=True, exist_ok=True)
    ext = {'PNG': '.png', 'JPG': '.jpg', 'WEBP': '.webp'}[fmt]
    dest = preview_dir / f'ANIMA-{seed}-{uuid.uuid4().hex[:12]}{ext}'
    try:
        if fmt == 'PNG':
            shutil.copy2(src, dest)
        else:
            with Image.open(src) as im:
                if fmt == 'JPG':
                    im.convert('RGB').save(dest, 'JPEG', quality=100, subsampling=0)
                else:
                    im.convert('RGB').save(dest, 'WEBP', lossless=True, quality=100, method=6)
        return dest
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def _forge_output_dir(is_img2img=False):
    """Use the configured sample directory of the active Forge mode."""
    from modules import shared
    configured = ((getattr(shared.opts, 'outdir_img2img_samples', '') if is_img2img else
                   getattr(shared.opts, 'outdir_txt2img_samples', '')) or
                  getattr(shared.opts, 'outdir_samples', '') or
                  'outputs/txt2img-images')
    result = Path(os.path.expandvars(os.path.expanduser(str(configured))))
    # Forge's data path is the root for relative output paths.
    if not result.is_absolute():
        from modules import paths_internal
        result = Path(paths_internal.data_path) / result
    return result.resolve()


def _autosave_preview(preview, seed, is_img2img=False):
    directory = _forge_output_dir(is_img2img)
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f'ANIMA_{seed}_{uuid.uuid4().hex[:8]}{preview.suffix}'
    shutil.copy2(preview, dest)
    print(f'[Comfy Neo ANIMA] auto-saved to Forge samples: {dest}', flush=True)
    return dest


def _consume_progress(ws_queue, jobid, steps, state):
    """Print only sampler progress for this ANIMA job; no output spam."""
    if ws_queue is None:
        return
    while True:
        try:
            raw = ws_queue.get_nowait()
        except queue.Empty:
            return
        if not raw or raw.startswith('__H3_WS_'):
            continue
        try:
            event = json.loads(raw)
            typ = event.get('type')
            data = event.get('data') or {}
            event_job = str(data.get('prompt_id') or '')
            if event_job and event_job != str(jobid):
                continue
            if typ == 'executing' and str(data.get('node')) == '7' and not state.get('started'):
                state['started'] = time.monotonic()
                state['last_time'] = state['started']
                print('[Comfy Neo ANIMA] sampling...', flush=True)
            if typ != 'progress':
                continue
            step, maximum = int(data.get('value')), int(data.get('max'))
            node = str(data.get('node') or '')
            if node not in ('7', '') or maximum != int(steps) or step < 1 or step > maximum:
                continue
            if state.get('last_step') == step:
                continue
            now = time.monotonic()
            started = state.setdefault('started', now)
            delta = now - state.get('last_time', started)
            elapsed = now - started
            eta = elapsed / step * (maximum - step)
            state['last_time'] = now
            state['last_step'] = step
            speed = f'{delta:.2f}s/it' if delta >= 1 else f'{1/max(delta, 1e-5):.2f}it/s'
            print(f'[Comfy Neo ANIMA] {step}/{maximum} '
                  f'[{elapsed:.1f}s elapsed, {eta:.1f}s remaining, {speed}]', flush=True)
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
            continue


def _preview_from_history(entry, root):
    """Accept ONLY PreviewImage temp output, never a saved file."""
    temp_root = (root / 'temp').resolve()
    outputs = entry.get('outputs') or {}
    for output in outputs.values():
        for item in output.get('images', []) or []:
            if item.get('type') != 'temp':
                continue
            if not str(item.get('filename', '')).lower().endswith('.png'):
                continue
            src = (temp_root / str(item.get('subfolder', '')) / str(item['filename'])).resolve()
            if src.is_relative_to(temp_root) and src.is_file():
                return src
    return None


def generate(prompt, negative, width, height, steps, cfg, seed, checkpoint, modules,
             sampler='er_sde', scheduler='simple', attention=ATTENTION_STABLE,
             output_format=None, auto_save=None, init_image_data=None,
             resize_mode=1, resize_by=None, denoise=1.0, edit_mode='img2img',
             sketch_layer_data=None, mask_data=None, invert_mask=False, mask_blur=4):
    """Run ANIMA txt2img or img2img, with existing LoRA and preview handling."""
    from . import comfy_full_backend as backend
    from .comfy_output import option
    fmt = _preview_format(output_format if output_format is not None else
                          option('comfy_image_format', 'PNG'))
    do_save = bool(auto_save if auto_save is not None else
                   option('comfy_image_auto_save', False))
    checkpoint = _actual_path(checkpoint)
    if 'anima' not in str(checkpoint).lower():
        raise ValueError('This first-stage Comfy workflow only supports ANIMA diffusion checkpoints.')
    mods = _module_paths(modules)
    clip = next((p for p in mods if 'qwen_3_06b_base' in p.name.lower()), None)
    vae = next((p for p in mods if 'qwen_image_vae' in p.name.lower()), None)
    if clip is None or vae is None:
        raise ValueError('ANIMA needs qwen_3_06b_base.safetensors and qwen_image_vae.safetensors selected in VAE / Text Encoder.')
    # Extract standard Forge/QuickPick directives, not their trigger words.
    # A LoRA can be tagged in either prompt. It changes the loaded model for
    # both branches (just like the normal Comfy LoraLoader).
    prompt, pos_loras = parse_lora_tags(prompt)
    negative, neg_loras = parse_lora_tags(negative)
    requested_loras = pos_loras + neg_loras
    # Unload Forge's in-process checkpoint before entering the private worker.
    backend._release_forge_models_for_h3()
    backend._prepare_embedded_model_paths(str(checkpoint), [str(clip), str(vae)])
    token = backend._session_token(auto_activate=True)
    backend.ensure_server()
    if backend._active_prompt_id is not None:
        raise RuntimeError('A MiniMax/ANIMA job is already running.')
    resolved_loras = []
    if requested_loras:
        choices = backend._choice_values('LoraLoader', 'lora_name')
        if not choices:
            raise ValueError('ANIMA: embedded ComfyUI reports no available LoRAs. '
                             'Check models/Lora and restart Forge if paths changed.')
        roots = _lora_search_roots()
        for name, model_strength, clip_strength in requested_loras:
            choice = resolve_lora_choice(name, choices, roots)
            resolved_loras.append((choice, model_strength, clip_strength))
            print(f'[Comfy Neo ANIMA] LoRA: {choice} '
                  f'(model={model_strength:g}, clip={clip_strength:g})', flush=True)
    # Upload our input into the PRIVATE worker (not native Forge, not another Comfy).
    # Each image filename is unique; no dangling Comfy cache entries are created.
    image_name = None
    preserve = None
    if init_image_data:
        png_url, width, height, preserve = prepare_anima_edit_image(
            init_image_data, width, height, resize_mode, resize_by, edit_mode,
            sketch_layer_data, mask_data, invert_mask, mask_blur)
        uploaded = backend._upload_browser_image(png_url, stem='anima_dev26')
        image_name = str(uploaded.get('name') or '')
        if not image_name.startswith('anima_dev26_') or Path(image_name).name != image_name:
            raise RuntimeError('ANIMA img2img: Comfy returned an unexpected upload filename.')
        print(f'[Comfy Neo ANIMA] img2img input uploaded: {image_name} '
              f'-> {width}x{height}; mode={edit_mode}, resize_mode={resize_mode}, '
              f'denoise={float(denoise):g}, mask={preserve is not None}', flush=True)
    graph, actual_seed = build_graph(
        _choice('UNETLoader', 'unet_name', str(checkpoint)),
        _choice('CLIPLoader', 'clip_name', str(clip), BASE_ENCODER),
        _choice('VAELoader', 'vae_name', str(vae), BASE_VAE),
        prompt, negative, width, height, steps, cfg, seed, sampler, scheduler,
        attention, loras=resolved_loras, init_image_name=image_name,
        denoise=denoise if image_name else 1.0, use_mask=preserve is not None)
    print(f'[Comfy Neo] ANIMA requested_seed={seed} actual_seed={actual_seed} '
          f'loras={len(resolved_loras)} mode={"img2img" if image_name else "txt2img"}', flush=True)
    jobid = None
    ws_proc = ws_queue = None
    progress = {}
    started = time.monotonic()
    client = str(uuid.uuid4())
    try:
        try:
            ws_proc, ws_queue = backend._start_ws_relay(client)
            print('[Comfy Neo ANIMA] websocket progress connected', flush=True)
        except Exception as exc:
            print(f'[Comfy Neo ANIMA] websocket unavailable; completion-only fallback: {exc}', flush=True)
        jobid = backend._post('/prompt', {'prompt': graph, 'client_id': client})['prompt_id']
        with backend._lock:
            backend._active_prompt_id = jobid
        print(f'[Comfy Neo ANIMA] submitted {jobid}: {width}x{height}, '
              f'{steps} steps, {sampler}/{scheduler}, {attention}, '
              f'format={fmt}, auto_save={do_save}', flush=True)
        while time.monotonic() - started < 1800:
            if not backend._session_current(token):
                print('[Comfy Neo ANIMA] job cancelled after worker session ended', flush=True)
                return None
            _consume_progress(ws_queue, jobid, steps, progress)
            history = backend._get('/history/' + str(jobid), timeout=25)
            if str(jobid) in history:
                entry = history[str(jobid)]
                status = entry.get('status') or {}
                state = status.get('status_str')
                if state in ('interrupted', 'cancelled'):
                    return None
                if state == 'error':
                    raise RuntimeError('ANIMA Comfy execution failed: ' + json.dumps(status, ensure_ascii=False)[:1700])
                root = Path(backend._cfg()['comfy_root'])
                source = _preview_from_history(entry, root)
                if source is not None:
                    if not backend._session_current(token):
                        return None
                    result = _convert_preview(source, actual_seed, fmt)
                    if preserve is not None:
                        try:
                            _protect_unmasked_pixels(result, preserve, fmt)
                        except BaseException:
                            result.unlink(missing_ok=True)
                            raise
                    # IMPORTANT: do NOT delete Comfy's temporary PreviewImage.
                    # Comfy caches output node results for identical workflows,
                    # including the filename returned by PreviewImage. When the
                    # user reuses a fixed seed, the next run may return that
                    # cached filename without executing PreviewImage again.
                    # Removing the source here would leave a dangling cached
                    # file path and falsely report "completed without preview".
                    # These are Comfy TEMP assets, not permanent auto-saves.
                    if do_save:
                        _autosave_preview(result, actual_seed, is_img2img=bool(image_name))
                    _consume_progress(ws_queue, jobid, steps, progress)
                    print(f'[Comfy Neo ANIMA] done in {time.monotonic()-started:.1f}s; '
                          f'preview (NOT auto-saved): {result}', flush=True)
                    return str(result)
                if status.get('completed') or state == 'success':
                    raise RuntimeError('ANIMA completed without a temporary PreviewImage. Check workflow node 9.')
            time.sleep(.15)
        raise TimeoutError('ANIMA generation timed out after 30 minutes')
    finally:
        with backend._lock:
            if backend._active_prompt_id == jobid:
                backend._active_prompt_id = None
        backend._stop_ws_relay(ws_proc)
        # Uploaded img2img inputs are one-use, unique per job. Do not accumulate
        # user images inside the Comfy input folder after completion/cancel.
        if image_name:
            try:
                input_dir = (Path(backend._cfg()['comfy_root']) / 'input').resolve()
                one_use_file = (input_dir / image_name).resolve()
                if one_use_file.parent == input_dir and one_use_file.name.startswith('anima_dev26_'):
                    one_use_file.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError) as exc:
                print(f'[Comfy Neo ANIMA] temporary input cleanup warning: {exc}', flush=True)
