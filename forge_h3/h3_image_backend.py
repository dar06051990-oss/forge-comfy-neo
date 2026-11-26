"""MiniMax H3 still images, including the P-H3 distilled workflow."""
from pathlib import Path
import json
import secrets
import time
import uuid
import shutil
from .comfy_sampler_ui import PH3_SCHEDULE, PH3_SIGMAS, validate
from . import anima_backend as common


def _validate_dimensions(width, height):
    if min(width, height) >= 64 and width % 32 == 0 and height % 32 == 0:
        return

    def nearest(value):
        lower = max(64, (value // 32) * 32)
        upper = max(64, ((value + 31) // 32) * 32)
        return str(lower) if lower == upper else f'{lower} или {upper}'

    raise ValueError(
        'MiniMax H3 Image: width и height должны быть кратны 32 (минимум 64). '
        f'Получено: {width}x{height}. '
        f'Ближайшие допустимые: width {nearest(width)}; height {nearest(height)}. '
        'Размер автоматически не изменён.'
    )


def build_graph(unet, clip, vae, prompt, width, height, steps, cfg, seed,
                sampler='euler', scheduler='simple', loras=(), images=(), attention='Stable'):
    width, height, steps = int(width), int(height), int(steps)
    seed = int(seed)
    if seed < 0:
        seed = secrets.randbelow(2**53)
    _validate_dimensions(width, height)
    p_mode = scheduler == PH3_SCHEDULE
    if p_mode:
        sampler, steps, cfg = 'er_sde', 8, 1.0
    else:
        sampler, scheduler = validate(sampler, scheduler)
    if steps < 1 or not 0 <= float(cfg) <= 100:
        raise ValueError('Invalid steps or CFG.')
    if len(images) > 9:
        raise ValueError('At most nine reference pictures are supported.')
    g = {}
    def node(key, cls, **inputs):
        g[key] = {'class_type': cls, 'inputs': inputs}
        return [key, 0]
    model = node('1', 'UNETLoader', unet_name=unet, weight_dtype='default')
    encoder = node('2', 'CLIPLoader', clip_name=clip, type='minimax', device='default')
    decoder = node('3', 'VAELoader', vae_name=vae)
    for i, (name, model_weight, clip_weight) in enumerate(loras):
        key = f'lora_{i}'
        model = node(key, 'LoraLoader', model=model, clip=encoder, lora_name=name,
                     strength_model=float(model_weight), strength_clip=float(clip_weight))
        encoder = [key, 1]
    if p_mode:
        model = node('shift', 'MiniMaxH3SigmaShift', model=model, shift_video=1.0, shift_audio=1.0)
    if attention == common.ATTENTION_CK:
        model = node('attention', 'ModelAttentionBackend', model=model, attention='comfy kitchen attention')
    refs = {f'images.image_{i}': node(f'ref_{i}', 'LoadImage', image=name)
            for i, name in enumerate(images, 1)}
    positive = node('4', 'PH3ImageConditioning' if p_mode else 'MiniMaxH3ImageConditioning',
                    clip=encoder, vae=decoder, prompt=prompt, width=width, height=height, **refs)
    if p_mode:
        noise = node('noise', 'RandomNoise', noise_seed=seed)
        guider = node('guider', 'BasicGuider', model=model, conditioning=positive)
        select = node('sampler', 'KSamplerSelect', sampler_name=sampler)
        sigmas = node('sigmas', 'ManualSigmas', sigmas=PH3_SIGMAS)
        samples = node('7', 'SamplerCustomAdvanced', noise=noise, guider=guider,
                       sampler=select, sigmas=sigmas, latent_image=['4', 1])
    else:
        negative = node('5', 'ConditioningZeroOut', conditioning=positive)
        samples = node('7', 'KSampler', model=model, positive=positive, negative=negative,
                       latent_image=['4', 1], seed=seed, steps=steps, cfg=float(cfg),
                       sampler_name=sampler, scheduler=scheduler, denoise=1.0)
    image = node('8', 'VAEDecode', samples=samples, vae=decoder)
    node('9', 'PreviewImage', images=image)
    return g, seed, steps


def generate(prompt, negative, width, height, steps, cfg, seed, checkpoint, modules,
             sampler, scheduler, payload, is_img2img=False, attention=None):
    from . import comfy_full_backend as backend
    from .comfy_output import option
    from .integration import module_paths
    checkpoint = common._actual_path(checkpoint)
    mods = [Path(p) for p in module_paths(modules)]
    if len(mods) != 2:
        raise ValueError('Select one H3 text encoder and one image/video VAE (no audio VAE).')
    # Prefer Forge's actual folder membership, then recognizable role names.
    def vae_role(p):
        return any(x.lower() == 'vae' for x in p.parts[:-1]) or 'vae' in p.name.lower()
    vaes = [p for p in mods if vae_role(p)]
    if len(vaes) != 1:
        raise ValueError('Could not distinguish VAE from text encoder. Select one of each from their standard Forge folders.')
    vae = vaes[0]; clip = next(p for p in mods if p != vae)
    if is_img2img and not payload.get('init_image'):
        raise ValueError('H3 Image Edit requires a source image.')
    width, height = backend._resolve_h3_dimensions(width, height, payload)
    _validate_dimensions(width, height)
    prompt, tags = common.parse_lora_tags(prompt)
    _, negative_tags = common.parse_lora_tags(negative)
    tags += negative_tags
    refs = ([payload['init_image']] if payload.get('init_image') else []) + list(payload.get('stitch_images') or [])
    if len(refs) > 9:
        raise ValueError('At most nine reference pictures are supported.')
    backend._release_forge_models_for_h3()
    backend._prepare_embedded_model_paths(str(checkpoint), [str(clip), str(vae)])
    token = backend._session_token(auto_activate=True)
    backend.ensure_server()
    if backend._active_prompt_id is not None:
        raise RuntimeError('A Comfy Neo job is already running.')
    resolved = []
    if tags:
        choices = backend._choice_values('LoraLoader', 'lora_name')
        for name, a, b in tags:
            resolved.append((common.resolve_lora_choice(name, choices, common._lora_search_roots()), a, b))
    names = []; jobid = None; ws_proc = ws_queue = None
    try:
        for data in refs:
            name = str(backend._upload_browser_image(data, stem='h3_image_ref').get('name') or '')
            if not name.startswith('h3_image_ref_') or '/' in name or '\\' in name:
                raise RuntimeError('Unexpected reference upload filename.')
            names.append(name)
        graph, actual_seed, steps = build_graph(
            common._choice('UNETLoader', 'unet_name', str(checkpoint)),
            common._choice('CLIPLoader', 'clip_name', str(clip)),
            common._choice('VAELoader', 'vae_name', str(vae)),
            prompt, width, height, steps, cfg, seed, sampler, scheduler, resolved, names, attention)
        client = str(uuid.uuid4()); progress = {}; started = time.monotonic()
        try:
            ws_proc, ws_queue = backend._start_ws_relay(client)
        except Exception as exc:
            print('[Comfy Neo] websocket unavailable:', exc)
        jobid = backend._post('/prompt', {'prompt':graph, 'client_id':client})['prompt_id']
        with backend._lock:
            backend._active_prompt_id = jobid
        print(f'[Comfy Neo] {width}x{height} {steps} steps seed={actual_seed} references={len(names)} scheduler={scheduler}', flush=True)
        while time.monotonic() - started < 1800:
            if not backend._session_current(token): return None
            common._consume_progress(ws_queue, jobid, steps, progress)
            entry = backend._get('/history/' + str(jobid), timeout=25).get(str(jobid))
            if entry:
                status = entry.get('status') or {}; state = status.get('status_str')
                if state in ('interrupted', 'cancelled'): return None
                if state == 'error': raise RuntimeError('H3 Image: ' + json.dumps(status, ensure_ascii=False)[:3000])
                source = common._preview_from_history(entry, Path(backend._cfg()['comfy_root']))
                if source:
                    if not backend._session_current(token): return None
                    preview = common._convert_preview(source, actual_seed, option('comfy_image_format', 'PNG'))
                    target = preview.with_name(preview.name.replace('ANIMA-', 'H3Image-', 1)); preview.rename(target)
                    if option('comfy_image_auto_save', False):
                        dest = common._forge_output_dir(is_img2img); dest.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, dest / target.name)
                    print(f'[Comfy Neo] done in {time.monotonic()-started:.1f}s', flush=True)
                    return str(target)
                if status.get('completed') or state == 'success': raise RuntimeError('H3 Image completed without preview.')
            time.sleep(.15)
        raise TimeoutError('H3 Image generation timed out.')
    finally:
        with backend._lock:
            if backend._active_prompt_id == jobid: backend._active_prompt_id = None
        backend._stop_ws_relay(ws_proc)
        for name in names:
            try: (Path(backend._cfg()['comfy_root']) / 'input' / name).unlink(missing_ok=True)
            except OSError: pass
