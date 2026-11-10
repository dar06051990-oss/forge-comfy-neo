"""Read-only check of the configured embedded runtime dependencies."""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'comfy_bridge.json').read_text(encoding='utf-8'))
python_path = CONFIG.get('runtime_python') or sys.executable
code = r'''
import importlib.util,sys
print('Python:',sys.executable)
print('Version:',sys.version.split()[0])
for pkg in ('torch','comfy_kitchen','comfy_aimdo','aiohttp','av','yaml','safetensors','transformers','tokenizers','sentencepiece','einops','psutil','PIL','comfyui_frontend_package'):
    try: ok=importlib.util.find_spec(pkg) is not None
    except (ImportError,AttributeError,ValueError):ok=False
    print(('[OK] ' if ok else '[MISSING] ') + pkg)
try:
 import torch
 print('PyTorch:', torch.__version__)
 print('CUDA:',torch.version.cuda)
 print('GPU available:',torch.cuda.is_available())
except Exception as exc:print('Torch check failed:',type(exc).__name__,str(exc))
'''
try:
    subprocess.run([python_path, '-c', code], check=True)
except (OSError,subprocess.CalledProcessError) as exc:
    print('Could not inspect runtime environment:', exc)
    sys.exit(1)
