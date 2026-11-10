"""Comfy Neo: file-backed API workflow registry and private-runner adapter.

Does not change the existing MiniMax H3 backend or its Forge integration.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "workflows"
MAX_WORKFLOW_BYTES = 4 * 1024 * 1024
H3_PRESETS = {
    "h3:auto": "MiniMax H3 — Auto (FL2VA / Ref2VA)",
    "h3:fl2va": "MiniMax H3 — FL2VA (first / last frame)",
    "h3:ref2va": "MiniMax H3 — Ref2VA (references)",
}
_PLACEHOLDER = re.compile(r"\{\{(prompt|negative_prompt|width|height|frames|steps|seed|cfg|image|last_image)\}\}")
_FULL_PLACEHOLDER = re.compile(r"^\{\{(prompt|negative_prompt|width|height|frames|steps|seed|cfg|image|last_image)\}\}$")
_engine_lock = threading.Lock()


def list_workflows():
    """Stable names. New trusted API workflows can be placed into workflows/."""
    found = [(name, key) for key, name in H3_PRESETS.items()]
    if WORKFLOWS.is_dir():
        for path in sorted(WORKFLOWS.glob("*.json"), key=lambda p: p.name.lower()):
            if path.is_file() and not path.is_symlink():
                found.append(("API — " + path.stem, "api:" + path.name))
    return found


def is_h3(key):
    return str(key) in H3_PRESETS


def load_api_workflow(key):
    if not isinstance(key, str) or not key.startswith("api:"):
        raise ValueError("Choose an API workflow")
    name = key[4:]
    if not name or name != Path(name).name or not name.lower().endswith(".json") or name.startswith("."):
        raise ValueError("Invalid workflow file name")
    path = WORKFLOWS / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_WORKFLOW_BYTES:
        raise ValueError("Missing, linked, or oversized workflow file (max 4 MiB)")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not data:
        raise ValueError("Expected an API-format ComfyUI workflow object")
    if "nodes" in data and isinstance(data["nodes"], list):
        raise ValueError("Editor-format workflow detected. In ComfyUI choose Save (API Format), not Save.")
    for node_id, node in data.items():
        if (not isinstance(node_id, str) or not isinstance(node, dict)
                or not isinstance(node.get("class_type"), str)
                or not isinstance(node.get("inputs"), dict)):
            raise ValueError(f"Malformed API workflow node: {node_id!r}")
    return data


def bind_placeholders(graph, variables):
    """Only replace explicitly marked tokens. Never silently rewrite saved prompts."""
    def visit(obj):
        if isinstance(obj, dict):
            return {k: visit(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [visit(v) for v in obj]
        if isinstance(obj, str):
            full = _FULL_PLACEHOLDER.fullmatch(obj)
            if full:
                value = variables[full.group(1)]
                if value is None:
                    raise ValueError(f"Workflow requires {{{{{full.group(1)}}}}}; supply the input image")
                return value
            return _PLACEHOLDER.sub(lambda m: str(variables[m.group(1)] if variables[m.group(1)] is not None else ""), obj)
        return obj
    return visit(graph)


def image_data_url(path):
    if not path:
        return None
    path = Path(path if isinstance(path, (str, Path)) else path.name)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Input image unavailable or exceeds 16 MiB")
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        raise ValueError("Supported input images: PNG, JPG, WEBP, GIF")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _engine_media(first_image, last_image, extra_refs):
    first = image_data_url(first_image)
    last = image_data_url(last_image)
    refs = [image_data_url(p) for p in (extra_refs or [])]
    return json.dumps({"init_image": first, "stitch_images": ([last] if last else []) + refs})


def _resolve_h3_checkpoint(name):
    from .integration import checkpoint_info
    info = checkpoint_info(name)
    if info is not None:
        return str(info.filename)
    candidate = Path(str(name or ""))
    if candidate.is_file():
        return str(candidate.resolve())
    raise ValueError("H3 checkpoint not found in Forge. Refresh the model list or enter its full path.")


def _workflow_mode(checkpoint_path):
    from .models import inspect_model
    item = inspect_model(checkpoint_path)
    if not item or item.role != "dit":
        raise ValueError("Selected checkpoint is not a MiniMax H3 diffusion model")
    return item.mode if hasattr(item, "mode") else ("ref2va" if "ref2va" in Path(checkpoint_path).name.lower() else "fl2va")


def _safe_output_path(core, spec):
    if not isinstance(spec, dict) or not isinstance(spec.get("filename"), str):
        return None
    filename = spec["filename"]
    kind = str(spec.get("type", "output"))
    if kind not in ("output", "temp"):
        return None
    root = (core / kind).resolve()
    value = (root / str(spec.get("subfolder") or "") / filename).resolve()
    if not value.is_relative_to(root) or not value.is_file():
        return None
    return str(value)


def _collect_outputs(history, core):
    images, videos, files = [], [], []
    for output in (history.get("outputs") or {}).values():
        if not isinstance(output, dict):
            continue
        for kind in ("images", "videos", "gifs", "audio"):
            for item in (output.get(kind) or []):
                path = _safe_output_path(core, item)
                if not path:
                    continue
                suffix = Path(path).suffix.lower()
                if suffix in (".png", ".jpg", ".jpeg", ".webp"):
                    images.append(path)
                elif suffix in (".mp4", ".webm", ".mov"):
                    videos.append(path)
                files.append(path)
    return list(dict.fromkeys(images)), (videos[0] if videos else None), list(dict.fromkeys(files))


def _run_api(key, params, first_image, last_image):
    """Submit any *installed-node-compatible API graph* to our verified private Comfy process."""
    from . import comfy_full_backend as backend
    graph = load_api_workflow(key)
    backend._prepare_embedded_model_paths(None, [])
    if _stop_requested.is_set():
        return [], None, [], "Stopped"
    backend.ensure_server()
    if _stop_requested.is_set():
        return [], None, [], "Stopped"
    if backend._active_prompt_id is not None:
        raise RuntimeError("Another Comfy Neo / MiniMax job is already running")
    actual_seed = int(params["seed"]) if int(params["seed"]) >= 0 else secrets.randbelow(2**63)
    variables = dict(params, seed=actual_seed, image=None, last_image=None)
    # Optional uploads are only performed when a workflow explicitly uses the tokens.
    graph_as_text = json.dumps(graph, ensure_ascii=False)
    if "{{image}}" in graph_as_text:
        variables["image"] = backend._upload_browser_image(image_data_url(first_image), "engine_image")["name"] if first_image else None
    if "{{last_image}}" in graph_as_text:
        variables["last_image"] = backend._upload_browser_image(image_data_url(last_image), "engine_last")["name"] if last_image else None
    graph = bind_placeholders(graph, variables)
    prompt_id = None
    token = backend._session_token(auto_activate=True)
    try:
        if not backend._session_current(token) or _stop_requested.is_set():
            return [], None, [], "Stopped"
        prompt_id = backend._post("/prompt", {"prompt": graph, "client_id": str(uuid.uuid4())})["prompt_id"]
        with backend._lock:
            backend._active_prompt_id = prompt_id
        print(f"[Comfy Neo] Submitted {key} as {prompt_id}")
        deadline = time.monotonic() + 3600
        stop_sent = False
        while time.monotonic() < deadline:
            if not backend._session_current(token):
                return [], None, [], "Stopped: Comfy session closed"
            if _stop_requested.is_set() and not stop_sent:
                backend.interrupt()
                stop_sent = True
            try:
                response = backend._get("/history/" + str(prompt_id))
            except Exception:
                if not backend._session_current(token):
                    return [], None, [], "Stopped"
                raise
            if prompt_id in response:
                result = response[prompt_id]
                status = result.get("status") or {}
                state = status.get("status_str")
                if state in ("interrupted", "cancelled"):
                    return [], None, [], "Stopped"
                if state == "error":
                    raise RuntimeError("Comfy workflow failed: " + json.dumps(status, ensure_ascii=False)[:1500])
                images, video, files = _collect_outputs(result, Path(backend._cfg()["comfy_root"]))
                if status.get("completed") or state == "success":
                    if not files:
                        return [], None, [], "Completed, but no saved image/video/audio found. Add a Save node."
                    return images, video, files, f"Completed · seed {actual_seed} · {len(files)} saved file(s)"
            time.sleep(0.3)
        raise TimeoutError("Comfy Neo workflow exceeded the 60-minute execution timeout")
    finally:
        with backend._lock:
            if backend._active_prompt_id == prompt_id:
                backend._active_prompt_id = None


def run_workflow(key, prompt, negative_prompt, width, height, steps, seed, cfg,
                 frames, checkpoint, modules, first_image=None, last_image=None, extra_refs=None):
    """One generation per Comfy Neo tab. H3 runs through the unchanged DEV07 implementation."""
    if not _engine_lock.acquire(blocking=False):
        raise RuntimeError("Comfy Neo is already generating. Use Stop or wait until finished.")
    _stop_requested.clear()
    try:
        if int(width) < 64 or int(height) < 64 or int(steps) < 1:
            raise ValueError("Width/height must be at least 64, steps at least 1")
        from . import comfy_full_backend as backend
        if backend._active_prompt_id is not None:
            raise RuntimeError("An existing MiniMax / Comfy job is still running")
        # The separate worker needs the GPU. Avoid competing Forge model allocations.
        backend._release_forge_models_for_h3()
        if _stop_requested.is_set():
            return [], None, [], "Stopped"
        if is_h3(key):
            checkpoint_path = _resolve_h3_checkpoint(checkpoint)
            actual_mode = _workflow_mode(checkpoint_path)
            expected = key.split(":", 1)[1]
            if expected != "auto" and actual_mode != expected:
                raise ValueError(f"This checkpoint is {actual_mode.upper()}. Choose the matching workflow or Auto.")
            mod_names = [str(x) for x in (modules or [])]
            if key == "h3:ref2va" or (key == "h3:auto" and actual_mode == "ref2va"):
                images = [last_image] if last_image else []
                images += list(extra_refs or [])
            else:
                images = [last_image] if last_image else []
                if extra_refs:
                    raise ValueError("FL2VA supports a first and last frame, not extra references")
            media = _engine_media(first_image, images[0] if images else None, images[1:])
            path = backend.generate(prompt, width, height, frames, seed, steps, checkpoint_path, mod_names, media)
            if not path:
                return [], None, [], "Stopped"
            # Reuse the already-tested H3 hand-off: Gradio preview uses a safe temp copy.
            from . import integration
            preview_path = integration.adopt_comfy_video(path)
            return [], preview_path, [preview_path], "Completed · MiniMax H3 · " + Path(path).name
        params = {
            "prompt": str(prompt or ""), "negative_prompt": str(negative_prompt or ""),
            "width": int(width), "height": int(height), "steps": int(steps),
            "seed": int(seed), "cfg": float(cfg), "frames": int(frames),
        }
        return _run_api(key, params, first_image, last_image)
    finally:
        _engine_lock.release()

_stop_requested = threading.Event()


def request_stop():
    _stop_requested.set()
    from . import comfy_full_backend as backend
    backend.interrupt()
    return "Stop requested. The active Comfy job will be interrupted."


def release_worker():
    from . import comfy_full_backend as backend
    if _engine_lock.locked():
        return "Cannot release while a Comfy Neo job is running. Press Stop first."
    backend.deactivate_session("Comfy Neo: release memory")
    return "Release requested. The private Comfy worker will exit and return its memory."
