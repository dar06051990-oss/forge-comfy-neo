"""Read supported Comfy KSampler choices without importing CUDA / torch into Forge.

The lists come from the embedded ComfyUI source itself, i.e. this installed
version, not a guessed translation of Forge-only sampler names.
"""
from __future__ import annotations
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'runtime' / 'core' / 'comfy' / 'samplers.py'

# A compatibility fallback when Comfy's source file cannot be inspected.
# These are supported by the pinned Comfy version, not unsupported Forge extras.
FALLBACK_SAMPLERS = ('euler', 'euler_ancestral', 'heun', 'dpmpp_2m', 'dpmpp_sde',
                     'res_multistep', 'er_sde', 'ddim', 'uni_pc')
PIKA_SCHEDULE = 'pika_h3_flex'
PIKA_LABEL = 'Pika H3 Flex'
PIKA_SIGMAS = '1.0, 0.972973, 0.923077, 0.8, 0.571429, 0.333333, 0.0'
PIKA_STEPS = 6
PH3_SCHEDULE = "p_h3_image"
PH3_LABEL = "P-H3-Image"
PH3_SIGMAS = "1.0, 0.984753, 0.967905, 0.951688, 0.925372, 0.881923, 0.810083, 0.528718, 0.0"


def h3_choices(samplers, schedulers):
    return list(dict.fromkeys([*samplers, 'lcm'])), list(dict.fromkeys([*schedulers, PIKA_SCHEDULE]))


def pika_lora_name(mode):
    if mode not in ('fl2va', 'ref2va'):
        raise ValueError(f'Unsupported Pika H3 mode: {mode}')
    return f'pika_h3_flex_{mode}_v1_bf16.safetensors'


FALLBACK_SCHEDULERS = ('simple', 'normal', 'karras', 'exponential', 'beta')


def _literal_assign(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    return None


def available_choices(path=SOURCE):
    """Extract KSampler exact options from AST; no Comfy or PyTorch import."""
    path = Path(path)
    if not path.is_file():
        return list(FALLBACK_SAMPLERS), list(FALLBACK_SCHEDULERS)
    tree = ast.parse(path.read_text(encoding='utf-8'))
    def names(expr):
        if isinstance(expr, (ast.List, ast.Tuple)):
            return [ast.literal_eval(x) for x in expr.elts]
        if isinstance(expr, ast.Name):
            other = _literal_assign(tree, expr.id)
            if other is not None and other is not expr: return names(other)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return names(expr.left) + names(expr.right)
        raise ValueError('Unsupported Comfy sampler list syntax')
    samplers = names(_literal_assign(tree, 'SAMPLER_NAMES'))
    scheduler_map = _literal_assign(tree, 'SCHEDULER_HANDLERS')
    if not isinstance(scheduler_map, ast.Dict):
        raise ValueError('Unsupported Comfy scheduler registry')
    schedulers = [ast.literal_eval(k) for k in scheduler_map.keys]
    if not samplers or not schedulers or not all(isinstance(x, str) for x in samplers+schedulers):
        raise ValueError('Comfy source reported invalid sampler choices')
    return samplers, schedulers


def normalized_sampler(value):
    """Translate known Forge display labels to Comfy names, never invent kernels."""
    value = str(value or '').strip().lower().replace(' ', '_')
    aliases = {
        'euler_a': 'euler_ancestral',
        'dpm++_2m': 'dpmpp_2m',
        'dpm++_sde': 'dpmpp_sde',
        'dpm++_2m_sde': 'dpmpp_2m_sde',
    }
    return aliases.get(value, value)


def normalized_scheduler(value):
    return str(value or '').strip().lower().replace(' ', '_')


def validate(sampler, scheduler):
    samplers, schedulers = available_choices()
    s, c = normalized_sampler(sampler), normalized_scheduler(scheduler)
    if s not in samplers:
        raise ValueError(f'Comfy Neo: sampler {sampler!r} is not available in embedded ComfyUI. Choose from the Comfy Neo sampler list.')
    if c not in schedulers:
        raise ValueError(f'Comfy Neo: scheduler {scheduler!r} is not available in embedded ComfyUI. Choose from the Comfy Neo scheduler list.')
    return s,c


def resolve_forge_sampling(sampler, scheduler, *, preset, mode, opts):
    """Resolve *only* Forge Hires 'Use same' sentinels before Comfy execution.

    Forge can send a Hires dropdown value even when Comfy Neo shows the
    primary sampler/scheduler. Never pass these sentinels to KSampler, and
    never silently rewrite a genuinely unsupported concrete choice.
    """
    if preset not in ('h3', 'comfy_anima', 'h3_image') or mode not in ('t2i', 'i2i'):
        raise ValueError('Unknown Comfy Neo preset or generation mode')
    supported_samplers, supported_schedulers = available_choices()
    if preset == 'h3':
        supported_samplers, supported_schedulers = h3_choices(supported_samplers, supported_schedulers)
    if preset == 'h3_image':
        supported_schedulers.append(PH3_SCHEDULE)
    default_sampler = 'euler' if preset == 'h3_image' else 'er_sde' if preset == 'comfy_anima' else 'res_multistep'
    default_scheduler = 'beta' if preset == 'comfy_anima' else 'simple'

    def preset_value(field, default, normalizer, supported):
        saved = getattr(opts, f'{preset}_{mode}_{field}', default)
        selected = normalizer(saved)
        if selected not in supported:
            selected = default if default in supported else supported[0]
        return selected

    if normalized_sampler(sampler) == 'use_same_sampler':
        sampler = preset_value('sampler', default_sampler, normalized_sampler, supported_samplers)
        print(f'[Comfy Neo] Forge Hires sampler sentinel resolved to {sampler!r} ({preset}/{mode})')
    if normalized_scheduler(scheduler) == 'use_same_scheduler':
        scheduler = preset_value('scheduler', default_scheduler, normalized_scheduler, supported_schedulers)
        print(f'[Comfy Neo] Forge Hires scheduler sentinel resolved to {scheduler!r} ({preset}/{mode})')
    if normalized_scheduler(scheduler) == PH3_SCHEDULE:
        if preset != "h3_image":
            raise ValueError("P-H3-Image is only available for MiniMax H3 Image.")
        return "er_sde", PH3_SCHEDULE
    if normalized_scheduler(scheduler) == PIKA_SCHEDULE:
        if preset != 'h3':
            raise ValueError('Pika H3 Flex is only available for MiniMax H3.')
        return 'lcm', PIKA_SCHEDULE
    return validate(sampler, scheduler)


def apply_pika_schedule(graph):
    """Replace the compiled BasicScheduler, retaining its existing output link."""
    if graph.get('105:9', {}).get('class_type') != 'BasicScheduler' or graph.get('105:17', {}).get('class_type') != 'KSamplerSelect':
        raise RuntimeError('Pika H3 Flex: expected sampler nodes missing')
    graph['105:9'] = {'class_type': 'ManualSigmas', 'inputs': {'sigmas': PIKA_SIGMAS}}
    graph['105:17']['inputs']['sampler_name'] = 'lcm'
    return graph
