"""Read-only report for the already installed headless Comfy source (DEV07).

Reports layout, required entrypoints, preserved ComfyUI license and the
class_type used by MiniMax workflow templates. Never deletes or updates files.
"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
CORE=ROOT/'runtime'/'core'
REQUIRED=(
 'main.py','nodes.py','execution.py','server.py','folder_paths.py',
 'comfy/model_management.py','comfy/sd.py', 'comfy/samplers.py',
 'comfy/ldm/minimax/model.py','comfy/text_encoders/minimax.py',
 'comfy_extras/nodes_minimax_h3.py',
 'comfy_extras/nodes_model_advanced.py',
 'comfy_api/latest/_io.py','comfy_execution/graph.py','LICENSE',
)
WORKFLOWS=('comfy_h3_api_workflow.json','comfy_h3_ref2va_api_workflow.json','comfy_h3_fl2va_api_workflow.json')

def summary():
    print('Forge Comfy Neo v1.0.0 — Comfy core audit (READ ONLY)')
    print('Extension:', ROOT)
    print('Core:', CORE)
    problems=[]
    if not (CORE/'main.py').exists():
        problems.append('Core is missing: run setup_embedded_runtime.bat')
    for rel in REQUIRED:
        ok=(CORE/rel).is_file()
        print(('[OK] ' if ok else '[MISSING] ')+rel)
        if not ok:problems.append('Missing '+rel)
    count=0
    sizes=Counter(); quantities=Counter()
    for f in CORE.rglob('*'):
        if f.is_file():
            count+=1
            category=f.relative_to(CORE).parts[0]
            sizes[category]+=f.stat().st_size
            quantities[category]+=1
    print('\nSOURCE FOLDERS (source files, not venv or model weights)')
    for name,size in sizes.most_common():
        print(f' {name:22s} {quantities[name]:5d} files  {size/(1024**2):7.2f} MiB')
    print(f' Total: {count} files; {sum(sizes.values())/(1024**2):.2f} MiB')
    for unnecessary in ('web', 'comfy_api_nodes', 'custom_nodes'):
        if (CORE/unnecessary).exists():
            print(f'[INFO] Additional folder present: {unnecessary} (not removed)')
        else:
            print(f'[OK] Unneeded distribution folder not bundled: {unnecessary}')
    print('\nWORKFLOW TYPES (as used by existing MiniMax presets)')
    workflow_types=set()
    for name in WORKFLOWS:
        try:
            payload=json.loads((ROOT/name).read_text(encoding='utf-8'))
            if not isinstance(payload,dict):raise ValueError('expected API workflow mapping')
            types=Counter(str(value['class_type']) for value in payload.values() if isinstance(value,dict) and 'class_type' in value)
            if not types:raise ValueError('no class_type nodes found')
            print(f'[OK] {name}: {len(payload)} nodes, {len(types)} types')
            workflow_types.update(types)
        except Exception as e:
            problems.append(f'{name}: {e}')
            print(f'[FAIL] {name}: {e}')
    print(' Node classes:', ', '.join(sorted(workflow_types)))
    print('\nNO FILES REMOVED. Dependency pruning needs runtime validation.')
    if problems:
        print('RESULT: FAIL')
        for issue in problems:print(' -',issue)
        return 1
    print('RESULT: PASS — structural checks only (not GPU-generation test)')
    return 0

if __name__=='__main__':sys.exit(summary())
