"""Runs under ComfyUI's Python. Internal worker for comfy_bridge.py."""
import json
import sys
from pathlib import Path

req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = Path(req["comfy_root"]).resolve()
sys.path.insert(0, str(root))

import torch
import comfy.sd
import comfy.model_management

encoder = str(Path(req["encoder"]).resolve())
prompt = req["prompt"]
output = Path(req["output"])

print(f"[H3 bridge worker] ComfyUI root: {root}")
print(f"[H3 bridge worker] encoder: {Path(encoder).name}")
clip = comfy.sd.load_clip([encoder], clip_type=comfy.sd.CLIPType.MINIMAX)
tokens = clip.tokenize(prompt)
out = clip.encode_from_tokens(tokens, return_pooled=True, return_dict=True)
cond = out["cond"].detach().float().cpu().contiguous()
print(f"[H3 bridge worker] conditioning: {tuple(cond.shape)} {cond.dtype}")
torch.save(cond, output)
del out, cond, clip
comfy.model_management.unload_all_models()
if torch.cuda.is_available():
    torch.cuda.empty_cache()
