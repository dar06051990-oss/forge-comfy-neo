"""Read-only DEV06 audit of private Comfy runtime and configured model paths.

Run using the extension runtime/venv/Scripts/python.exe. Does not start Comfy,
connect to the internet, alter files, or change PyTorch installation.
"""
from pathlib import Path
import importlib.metadata as md
import importlib.util
import json
import os
import socket
import sys

RUNTIME=Path(__file__).resolve().parent
EXT=RUNTIME.parent
CORE=RUNTIME/'core'
PRIVATE=RUNTIME/'venv'
CONF=EXT/'comfy_bridge.json'
PACKAGES={
 'torch':'torch', 'torchvision':'torchvision',
 'comfy_kitchen':'comfy-kitchen', 'comfy_aimdo':'comfy-aimdo',
 'safetensors':'safetensors', 'transformers':'transformers',
 'aiohttp':'aiohttp','av':'av','yaml':'PyYAML',
}

def stat(name,passed,details=''):
 print(f"[{'OK' if passed else 'FAIL'}] {name}"+(f' : {details}' if details else ''))
 return passed

def check():
 good=True
 print('Forge Comfy Neo v1.0.0 - read-only independent runtime audit')
 print('Extension:',EXT)
 print('Running interpreter:',sys.executable)
 good &= stat('Private Python',Path(sys.executable).resolve()==(PRIVATE/'Scripts'/'python.exe').resolve(),sys.version.split()[0])
 good &= stat('Comfy main.py', (CORE/'main.py').is_file())
 good &= stat('GPLv3 license in bundled core', (CORE/'LICENSE').is_file())
 good &= stat('Standard Comfy execution', (CORE/'execution.py').is_file())
 good &= stat('MiniMax H3 nodes', (CORE/'comfy_extras'/'nodes_minimax_h3.py').is_file())
 good &= stat('MiniMax H3 architecture', (CORE/'comfy'/'ldm'/'minimax'/'model.py').is_file())
 try:
  c=json.loads(CONF.read_text(encoding='utf-8'))
  port=int(c.get('embedded_port',8191))
  good &= stat('Embedded mode',c.get('mode','embedded')=='embedded',repr(c.get('mode','embedded')))
  good &= stat('Loopback port setting',1024<=port<=65535,str(port))
 except Exception as e:
  print('[FAIL] Configuration:',str(e));return 1
 marker=RUNTIME/'private_env_ready.json'
 if marker.is_file():
  try:
   m=json.loads(marker.read_text(encoding='utf-8'))
   good &= stat('Private runtime marker',Path(m['python']).resolve()==Path(sys.executable).resolve())
  except Exception as e:
   good &= stat('Private runtime marker',False,str(e))
 else:good &= stat('Private runtime marker',False,'missing')
 for module,distribution in PACKAGES.items():
  try:
   spec=importlib.util.find_spec(module)
   version=md.version(distribution)
   if spec is None:raise RuntimeError('module not importable')
   origin=spec.origin
   if origin in (None,'namespace'):
    locs=list(spec.submodule_search_locations or [])
    origin=str(locs[0]) if locs else ''
   origin=Path(origin).resolve()
   good &= stat(f'{module} {version}',origin.is_relative_to(PRIVATE.resolve()),str(origin))
  except Exception as e:
   good &= stat(module,False,str(e))
 try:
  import torch
  good &= stat('PyTorch 2.11.0',torch.__version__.startswith('2.11.0'),torch.__version__)
  good &= stat('CUDA 13.0',torch.version.cuda=='13.0',str(torch.version.cuda))
  good &= stat('CUDA available',torch.cuda.is_available())
  if torch.cuda.is_available():print('GPU:',torch.cuda.get_device_name(0))
 except Exception as e:good &= stat('CUDA smoke test',False,str(e))
 paths=RUNTIME/'extra_model_paths.yaml'
 if paths.is_file():
  try:
   import yaml
   y=yaml.safe_load(paths.read_text(encoding='utf-8')) or {}
   model_locations=0
   for section,entries in y.items():
    if not isinstance(entries,dict):
     good &= stat(f'YAML section {section}',False,'expected mapping');continue
    for category,multiline in entries.items():
     if category in ('base_path','is_default'):continue
     if not isinstance(multiline,str):
      good &= stat(f'YAML {section}.{category}',False,'expected multiline string');continue
     for path in multiline.splitlines():
      if not path.strip():continue
      model_locations+=1
      # Model directories can be intentionally offline or removable; warn only.
      if not Path(path).is_dir():print(f'[WARN] Model folder unavailable ({category}): {path}')
   print(f'[OK] Model path YAML: {model_locations} locations declared')
  except Exception as e:good &= stat('Model path YAML',False,str(e))
 else:print('[INFO] Model path YAML will be generated when MiniMax runs')
 try:
  with socket.create_connection(('127.0.0.1',port),timeout=0.3):
   print(f'[INFO] Port {port} occupied (normal if Forge already started)')
 except OSError:print(f'[INFO] Port {port} free')
 print('RESULT:', 'PASS' if good else 'FAIL')
 print('Read only. Did not start a server, download or install anything.')
 return 0 if good else 1

if __name__=='__main__':sys.exit(check())
