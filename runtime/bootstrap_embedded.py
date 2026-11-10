"""Install the headless official ComfyUI backend into THIS extension.

Does not install Python packages or change Forge's existing Python environment.
"""
import argparse
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import urllib.request
import shutil

COMFY_COMMIT = 'bdb0816a292d221efeb6ec4ae41bcdff3f91fc86'
SRC_URL = f'https://github.com/Comfy-Org/ComfyUI/archive/{COMFY_COMMIT}.tar.gz'
HERE = Path(__file__).resolve().parent
CORE = HERE / 'core'

BACKEND_DIRS = (
    'app/', 'api_server/', 'blueprints/', 'comfy/', 'comfy_api/',
    'comfy_config/', 'comfy_execution/', 'comfy_extras/',
    'middleware/', 'utils/',
)
BACKEND_FILES = {
    'main.py','nodes.py','server.py','execution.py','folder_paths.py',
    'node_helpers.py','protocol.py','cuda_malloc.py','latent_preview.py',
    'hook_breaker_ac10a0.py','comfyui_version.py','requirements.txt',
    'openapi.yaml','LICENSE',
}


def allowed(path):
    if path in BACKEND_FILES: return True
    return any(path.startswith(prefix) for prefix in BACKEND_DIRS)


def install():
    if CORE.exists() and (CORE/'main.py').is_file():
        print('Existing headless runtime found:', CORE)
        print('To reinstall, rename runtime/core first.')
        return
    with tempfile.TemporaryDirectory(prefix='h3_embedded_') as td:
        archive=Path(td)/'comfy_source.tar.gz'
        print('Downloading pinned ComfyUI source',COMFY_COMMIT)
        with urllib.request.urlopen(SRC_URL, timeout=120) as r, archive.open('wb') as out:
            shutil.copyfileobj(r,out)
        stage=Path(td)/'core'
        stage.mkdir()
        files=0
        with tarfile.open(archive,'r:gz') as tar:
            for member in tar:
                if not member.isfile() or member.issym() or member.islnk():
                    continue
                # github archive names are ComfyUI-<sha>/relative/path
                name=member.name.split('/',1)
                if len(name)!=2: continue
                rel=name[1]
                if not allowed(rel): continue
                if '__pycache__' in rel or rel.endswith('.pyc'):continue
                dst=stage/rel
                if not dst.resolve().is_relative_to(stage.resolve()):
                    raise RuntimeError('Unsafe source path: '+rel)
                dst.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as source, dst.open('wb') as sink:
                    shutil.copyfileobj(source,sink)
                files+=1
        if not (stage/'main.py').is_file() or not (stage/'comfy'/'sd.py').is_file():
            raise RuntimeError('Missing required ComfyUI source files')
        for name in ('input','output','temp','models','user'):
            (stage/name).mkdir(exist_ok=True)
        shutil.move(str(stage), str(CORE))  # supports C: temp -> another drive
        print('Installed',files,'backend source files into:',CORE)
        print('No node editor, browser frontend or custom nodes were copied.')
        print('Check Python dependencies before starting Forge.')
        print('ComfyUI source uses GPLv3; see runtime/core/LICENSE.')

if __name__=='__main__':
    install()
