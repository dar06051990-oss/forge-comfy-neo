"""Verify the real DEV03 runtime without mutating dependencies."""
from pathlib import Path
import json
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PY = HERE / 'venv' / 'Scripts' / 'python.exe'
READY = HERE / 'private_env_ready.json'
CORE = HERE / 'core'
print('Private environment:', HERE)
print('ComfyUI sources:', 'OK' if (CORE/'main.py').is_file() else 'MISSING')
print('Ready marker:', 'OK' if READY.is_file() else 'MISSING')
if not PY.is_file() or not READY.is_file() or not (CORE/'main.py').is_file():
    print('Run setup_private_runtime.bat before starting Forge.')
    sys.exit(1)
code = r'''
import sys,torch,importlib
print('Interpreter:',sys.executable)
print('Python:',sys.version.split()[0])
for name in ('torch','torchvision','comfy_kitchen','comfy_aimdo','aiohttp','av','yaml','safetensors','transformers','tokenizers','sentencepiece','einops','psutil','PIL','comfyui_frontend_package'):
 importlib.import_module(name)
 print('[OK]', name)
print('PyTorch:',torch.__version__)
print('CUDA runtime:',torch.version.cuda)
print('CUDA available:',torch.cuda.is_available())
if torch.cuda.is_available():print('GPU:',torch.cuda.get_device_name(0))
assert torch.__version__.startswith('2.11.0')
assert torch.version.cuda == '13.0' and torch.cuda.is_available()
'''
try:
    subprocess.run([str(PY),'-c',code],check=True)
except (OSError,subprocess.CalledProcessError) as error:
    print('[ERROR] Private runtime verification failed:',error)
    sys.exit(1)
