"""Create a *private* Python/CUDA runtime inside the embedded Comfy extension.

Runs under Forge's Python as a DOWNLOAD/INSTALL COORDINATOR only. Never uses
Forge's interpreter for Comfy inference, and never installs packages into it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

RUNTIME = Path(__file__).resolve().parent
ROOT = RUNTIME.parent
CORE = RUNTIME / 'core'
ENV_DIR = RUNTIME / 'venv'
ENV_PYTHON = ENV_DIR / 'Scripts' / 'python.exe'
TOOLS = RUNTIME / 'tools'
UV_EXE = TOOLS / 'uv.exe'
MARKER = RUNTIME / 'private_env_ready.json'
UV_VERSION = '0.13.7'
UV_SHA256 = '0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021'
UV_URLS = (
    f'https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip',
    f'https://releases.astral.sh/github/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip',
)
TORCH_INDEX = 'https://download.pytorch.org/whl/cu130'
TORCH_PACKAGES = ('torch', 'torchvision', 'torchaudio')


def run(*args, env=None):
    cmd = [str(s) for s in args]
    print('>', subprocess.list2cmdline(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=str(ROOT), env=env)


def get_uv() -> None:
    if UV_EXE.is_file():
        run(UV_EXE, '--version')
        return
    TOOLS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='comfymax_uv_') as folder:
        archive = Path(folder) / 'uv.zip'
        last_error = None
        for url in UV_URLS:
            try:
                print(f'Downloading uv {UV_VERSION}: {url}', flush=True)
                req = urllib.request.Request(url, headers={'User-Agent': 'Forge-Comfy-Neo-v1.0.3'})
                with urllib.request.urlopen(req, timeout=120) as resp, archive.open('wb') as output:
                    shutil.copyfileobj(resp, output)
                actual = hashlib.sha256(archive.read_bytes()).hexdigest()
                if actual != UV_SHA256:
                    raise RuntimeError(f'uv SHA256 mismatch; got {actual}, expected {UV_SHA256}')
                with zipfile.ZipFile(archive) as z:
                    names = [n for n in z.namelist() if n.replace('\\', '/').endswith('/uv.exe') or n == 'uv.exe']
                    if len(names) != 1:
                        raise RuntimeError('uv.exe not found exactly once in official archive')
                    with z.open(names[0]) as original, UV_EXE.open('wb') as target:
                        shutil.copyfileobj(original, target)
                break
            except Exception as exc:
                last_error = exc
                if UV_EXE.exists():
                    UV_EXE.unlink()
                print('[WARN] uv download failed:', exc, flush=True)
        else:
            raise RuntimeError(f'Could not download verified uv: {last_error}')
    run(UV_EXE, '--version')


def main():
    if os.name != 'nt':
        raise RuntimeError('This installer targets Windows x64 only.')
    if sys.maxsize < 2 ** 32:
        raise RuntimeError('Run with 64-bit Forge Python.')
    if not (CORE / 'main.py').is_file() or not (CORE / 'comfy' / 'sd.py').is_file():
        raise RuntimeError('Embedded Comfy source missing; use the Comfy Neo Install button.')
    if not (CORE / 'requirements.txt').is_file():
        raise RuntimeError('Embedded Comfy requirements.txt missing.')

    print('==============================================================', flush=True)
    print(' Forge Comfy Neo v1.0.3 — install private Windows Python runtime', flush=True)
    print(' Does NOT modify Forge Python, system Python or other ComfyUI.', flush=True)
    print('==============================================================', flush=True)
    print('Runtime folder:', RUNTIME, flush=True)
    print('Private Python:', ENV_PYTHON, flush=True)
    print('PyTorch: latest stable from', TORCH_INDEX, flush=True)
    print('Large downloads required. No model weights are downloaded.', flush=True)
    if MARKER.exists():
        MARKER.unlink()  # prevent use of half-updated installs
    get_uv()
    env = os.environ.copy()
    env.update({
        'UV_PYTHON_INSTALL_DIR': str(RUNTIME / 'python'),
        'UV_PYTHON_BIN_DIR': str(RUNTIME / 'python-bin'),
        'UV_CACHE_DIR': str(RUNTIME / 'cache'),
        'UV_MANAGED_PYTHON': '1',
        'UV_NO_CONFIG': '1',
        'UV_NO_PROJECT': '1',
        'UV_LINK_MODE': 'copy',
        'PIP_DISABLE_PIP_VERSION_CHECK': '1',
    })
    if not ENV_PYTHON.is_file():
        # --managed-python disallows accidental reuse of a system or Forge Python.
        run(UV_EXE, 'venv', str(ENV_DIR), '--python', '3.13.18', '--managed-python', '--no-project', env=env)
    else:
        print('[INFO] Existing private venv found. Updating dependencies in-place.', flush=True)

    # Install the explicitly selected official Windows CUDA wheel first.
    run(UV_EXE, 'pip', 'install', '--python', ENV_PYTHON,
        *TORCH_PACKAGES,
        '--index-url', TORCH_INDEX, env=env)

    # The official source requirements include an unpinned torch and torchvision.
    # Pass explicit constraints so they cannot silently replace CUDA PyTorch.
    constraints = RUNTIME / 'constraints_private.txt'
    version_code = 'import importlib.metadata as m, json; print(json.dumps({p:m.version(p) for p in ("torch","torchvision","torchaudio")}))'
    versions = json.loads(subprocess.check_output([str(ENV_PYTHON), '-c', version_code], cwd=str(ROOT), env=env, text=True))
    if any(not v.endswith('+cu130') for v in versions.values()):
        raise RuntimeError(f'Expected CUDA 13.0 wheels: {versions}')
    constraints.write_text(''.join(f'{p}=={v}\n' for p,v in versions.items()), encoding='utf-8')
    run(UV_EXE, 'pip', 'install', '--python', ENV_PYTHON,
        '-r', CORE / 'requirements.txt', '-c', constraints,
        '--index-url', 'https://pypi.org/simple', '--extra-index-url', TORCH_INDEX, env=env)
    run(UV_EXE, 'pip', 'check', '--python', ENV_PYTHON, env=env)

    smoke = r'''
import importlib, sys, torch, pathlib
required = ('torch', 'torchvision', 'torchaudio', 'comfy_aimdo', 'comfy_kitchen', 'aiohttp',
 'av','yaml','safetensors','transformers','tokenizers','sentencepiece','einops',
 'psutil','PIL','comfyui_frontend_package')
for name in required:
 importlib.import_module(name)
print('Python:',sys.executable)
print('Torch:',torch.__version__, 'CUDA:',torch.version.cuda)
print('GPU:',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NOT AVAILABLE')
assert sys.version_info[:2] == (3, 13)
assert torch.__version__.endswith('+cu130'), 'Incorrect PyTorch build'
assert torch.version.cuda == '13.0', 'Incorrect PyTorch CUDA build (CPU wheel?)'
assert torch.cuda.is_available(), 'CUDA GPU not available'
'''
    run(ENV_PYTHON, '-c', smoke, env=env)
    verified = json.loads(subprocess.check_output([str(ENV_PYTHON), '-c', version_code], cwd=str(ROOT), env=env, text=True))
    if verified != versions:
        raise RuntimeError(f'CUDA package versions changed: {verified}')
    MARKER.write_text(json.dumps({
        'runtime': 'Forge Comfy Neo private Python',
        'python': str(ENV_PYTHON),
        'python_version': '3.13',
        'torch': versions['torch'], 'torchvision': versions['torchvision'], 'torchaudio': versions['torchaudio'], 'torch_cuda': '13.0', 'comfy_source': 'a277b5bf213e8a44fa34817b7770b54f86ef4cea',
        'uv': UV_VERSION
    }, indent=2), encoding='utf-8')
    print('\n[OK] Private Comfy Python is installed and CUDA verified.', flush=True)
    print('Private Python will use:', ENV_PYTHON, flush=True)
    print('No external Comfy Python is required from now on.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('\n[ERROR] Private environment was not marked ready.', flush=True)
        print(type(exc).__name__ + ': ' + str(exc), flush=True)
        sys.exit(1)
