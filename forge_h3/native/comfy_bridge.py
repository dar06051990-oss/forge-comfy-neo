"""Experimental MiniMax H3 text-encoder bridge to a local ComfyUI install.

Only used for the NVFP4 Qwen encoder.  ComfyUI runs the encoder in a short-lived
subprocess, writes the final H3 conditioning to a temporary .pt file, exits (so
its VRAM is released), then Forge continues with its own H3 DiT.

This deliberately starts with text-only T2V.  Image/reference conditioning is
left on the native Forge path until the bridge is proven on the known NVFP4 bug.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

import torch

from ..hidden_subprocess import hidden_windows_kwargs

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / "comfy_bridge.json"
_HELPER = Path(__file__).with_name("comfy_bridge_worker.py")
_encoder_path: Path | None = None

_DEFAULTS = {
    "enabled": True,
    "comfy_root": "S:/ComfyUI/ComfyUI",
    "comfy_python": "S:/ComfyUI/python_embeded/python.exe",
    "timeout_seconds": 900,
}


def set_encoder(path) -> None:
    global _encoder_path
    _encoder_path = Path(path).resolve()


def _config() -> dict:
    cfg = dict(_DEFAULTS)
    if _CONFIG.exists():
        try:
            cfg.update(json.loads(_CONFIG.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[MiniMax H3 Comfy Bridge] config error: {e}")
    return cfg


def enabled() -> bool:
    cfg = _config()
    return bool(cfg.get("enabled")) and _encoder_path is not None


def encode(prompt: str) -> torch.Tensor:
    """Return ComfyUI H3 conditioning as CPU float32 [1,L,5120]."""
    if _encoder_path is None:
        raise RuntimeError("[MiniMax H3 Comfy Bridge] text encoder path was not captured")
    cfg = _config()
    comfy_root = Path(str(cfg["comfy_root"]))
    comfy_python = Path(str(cfg["comfy_python"]))
    if not comfy_root.exists():
        raise RuntimeError(f"[MiniMax H3 Comfy Bridge] ComfyUI folder not found: {comfy_root}. Edit {_CONFIG.name}")
    if not comfy_python.exists():
        raise RuntimeError(f"[MiniMax H3 Comfy Bridge] ComfyUI Python not found: {comfy_python}. Edit {_CONFIG.name}")

    temp = Path(tempfile.gettempdir()) / f"forge_h3_comfy_{uuid.uuid4().hex}.pt"
    request = Path(tempfile.gettempdir()) / f"forge_h3_comfy_{uuid.uuid4().hex}.json"
    request.write_text(json.dumps({
        "comfy_root": str(comfy_root),
        "encoder": str(_encoder_path),
        "prompt": prompt,
        "output": str(temp),
    }, ensure_ascii=False), encoding="utf-8")

    print(f"[MiniMax H3 Comfy Bridge] encoding prompt with ComfyUI: {_encoder_path.name}")
    try:
        proc = subprocess.run(
            [str(comfy_python), str(_HELPER), str(request)],
            cwd=str(comfy_root), text=True, capture_output=True,
            timeout=int(cfg.get("timeout_seconds", 900)),
            **hidden_windows_kwargs(),
        )
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.returncode != 0:
            raise RuntimeError("[MiniMax H3 Comfy Bridge] worker failed:\n" + (proc.stderr or proc.stdout or "unknown error")[-5000:])
        if not temp.exists():
            raise RuntimeError("[MiniMax H3 Comfy Bridge] worker returned no conditioning file")
        cond = torch.load(temp, map_location="cpu", weights_only=True)
        if not isinstance(cond, torch.Tensor) or cond.ndim != 3 or cond.shape[-1] != 5120:
            raise RuntimeError(f"[MiniMax H3 Comfy Bridge] unexpected conditioning: {type(cond)} {getattr(cond, 'shape', None)}")
        cond = cond.float().contiguous()
        print(f"[MiniMax H3 Comfy Bridge] OK: conditioning {tuple(cond.shape)} {cond.dtype}; Comfy process exited, VRAM released")
        return cond
    finally:
        for p in (request, temp):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
