"""Compile legacy MiniMax H3 API workflows into builtin-only Comfy graphs.

This does not alter prompt contents or the intended 4-step Turbo default.
"""
import copy

# DEV14: Optional attention patch nodes; disabled by default.
ATTENTION_STABLE = "Stable (current)"
ATTENTION_SOL = "Comfy Kitchen SOL sparse (experimental)"
ATTENTION_CK = "Comfy Kitchen attention (experimental)"
ATTENTION_CK_LEGACY = "Comfy Kitchen INT8 dense (BF16 only)"
ATTENTION_MODES = (ATTENTION_STABLE, ATTENTION_SOL, ATTENTION_CK)
ATTENTION_DENSE_NODE = "h3_attention_dense"
ATTENTION_SOL_NODE = "h3_attention_sol"


def normalize_attention_mode(mode):
    """Accept the old DEV14 saved dropdown value without breaking user settings."""
    return ATTENTION_CK if mode == ATTENTION_CK_LEGACY else mode

LEGACY_HELPERS = frozenset({
    '127', '105:111', '105:107', '105:122', '105:123',
    '105:124', '105:125', '105:126',
})


def compile_h3_graph(graph, steps, turbo=None, width=None, height=None, frames=None, attention_mode=ATTENTION_STABLE):
    """Resolve nonstandard helper nodes and leave only native Comfy node types.

    The original Forge bridge already writes width, height and frames directly
    into MiniMaxH3ImageToVideo/MiniMaxH3ReferenceToVideo. The helper nodes are
    therefore redundant once the graph is assembled.
    """
    graph = copy.deepcopy(graph)
    if turbo is None:
        turbo = bool(graph.get('105:126', {}).get('inputs', {}).get('value', True))
    if '105:6' not in graph or '105:9' not in graph or '105:16' not in graph:
        raise ValueError('Unsupported H3 workflow: model, guider or scheduler is missing')
    if '105:104' not in graph and '105:128' not in graph:
        raise ValueError('Unsupported H3 workflow: no MiniMax H3 conditioning node')
    model_id = '105:6'
    if turbo:
        if '105:121' not in graph:
            raise ValueError('Turbo selected but LoraLoaderModelOnly is missing')
        graph['105:121']['inputs']['model'] = ['105:6', 0]
        model_id = '105:121'
    else:
        graph.pop('105:121', None)
    # DEV14: Keep the old H3 graph byte-for-byte equivalent in Stable mode.
    # The optional nodes are kept in the JSON templates only as editable
    # examples; remove the inactive ones before submitting to ComfyUI.
    attention_mode = normalize_attention_mode(attention_mode)
    if attention_mode not in ATTENTION_MODES:
        raise ValueError(f"Unsupported H3 Attention setting: {attention_mode!r}")
    if attention_mode == ATTENTION_SOL:
        sparse = graph.get(ATTENTION_SOL_NODE)
        if not sparse or sparse.get('class_type') != 'BlockSparseAttention':
            raise ValueError('H3 sparse-attention node missing from workflow template')
        sparse['inputs']['model'] = [model_id, 0]
        model_id = ATTENTION_SOL_NODE
    else:
        graph.pop(ATTENTION_SOL_NODE, None)
    if attention_mode == ATTENTION_CK:
        # DEV15: No name-based BF16/INT8 restriction. ConvRot is a *weight*
        # representation and does not prove that INT8 attention is unsupported.
        # The ModelAttentionBackend node performs its own runtime dispatch.
        # Some checkpoint / device / shape combinations can still fail; this
        # remains an experimental, opt-in mode (Stable is always available).
        dense = graph.get(ATTENTION_DENSE_NODE)
        if not dense or dense.get('class_type') != 'ModelAttentionBackend':
            raise ValueError('H3 ModelAttentionBackend node missing from template')
        dense['inputs']['model'] = [model_id, 0]
        dense['inputs']['attention'] = 'comfy kitchen attention'
        model_id = ATTENTION_DENSE_NODE
    else:
        graph.pop(ATTENTION_DENSE_NODE, None)

    graph['105:9']['inputs']['model'] = [model_id, 0]
    graph['105:9']['inputs']['steps'] = int(steps)
    graph['105:16']['inputs']['model'] = [model_id, 0]

    # Bind the H3-native conditioning inputs directly; the old selectors and
    # math node only existed to feed these three scalar values.
    legacy_dims = graph.get('127', {}).get('inputs', {})
    for node_id in ('105:104', '105:128'):
        if node_id not in graph:
            continue
        inputs = graph[node_id]['inputs']
        for name, direct, fallback in (
            ('width', width, legacy_dims.get('Width', 512)),
            ('height', height, legacy_dims.get('Height', 512)),
            ('length', frames, 90),
        ):
            existing = inputs.get(name)
            if isinstance(existing, list) and len(existing) == 2:
                inputs[name] = int(direct if direct is not None else fallback)
            elif direct is not None:
                inputs[name] = int(direct)

    for node_id in LEGACY_HELPERS:
        graph.pop(node_id, None)

    # A very small validation of references that also catches mistakes in future edits.
    for node_id, spec in graph.items():
        for name, value in spec.get('inputs', {}).items():
            if (isinstance(value, list) and len(value) == 2 and
                    isinstance(value[0], str) and isinstance(value[1], int) and
                    value[0] not in graph):
                raise ValueError(f'{node_id}.{name}: missing upstream node {value[0]}')
    return graph
