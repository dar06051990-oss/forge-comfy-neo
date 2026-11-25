"""Standalone audio references: validate, stage as WAV, and wire Ref2VA."""
from pathlib import Path
import os
import subprocess
import uuid
from .hidden_subprocess import hidden_windows_kwargs

FORMATS = ('.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus')
MAX_BYTES = 96 * 1024 * 1024


def audio_path(upload):
    if isinstance(upload, dict):
        upload = upload.get('path') or upload.get('name')
    elif not isinstance(upload, (str, os.PathLike)):
        upload = getattr(upload, 'name', None)
    if not upload:
        raise ValueError('Select an audio file.')
    p = Path(upload)
    if p.is_symlink() or not p.is_file() or p.suffix.lower() not in FORMATS:
        raise ValueError('Select a supported regular audio file.')
    if not 0 < p.stat().st_size <= MAX_BYTES:
        raise ValueError('Audio file must be nonempty and at most 96 MiB.')
    return p


def select_audio(paths):
    for raw in paths:
        p = Path(raw)
        if str(raw).startswith(('\\\\', '//')) or not p.is_absolute() or p.suffix.lower() not in FORMATS:
            continue
        return str(audio_path(p))
    raise ValueError('Copy a local audio file in Windows Explorer first.')


def attach_audio(graph, upload, input_dir, slot, frames):
    from .media import find_ffmpeg
    if not 0 <= slot < 3:
        raise ValueError('Ref2VA supports up to three standalone audio references.')
    node = graph.get('105:128', {})
    if node.get('class_type') != 'MiniMaxH3ReferenceToVideo':
        raise ValueError('Audio Reference requires a Ref2VA checkpoint.')
    source = audio_path(upload)
    input_dir = Path(input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    staged = input_dir / ('comfyneo_ref_audio_' + uuid.uuid4().hex + '.wav')
    try:
        result = subprocess.run([find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(source), '-map', '0:a:0', '-vn', '-t', str(min(15.0, float(frames)/24)), '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', str(staged)], capture_output=True, timeout=30, **hidden_windows_kwargs())
        if result.returncode or not staged.is_file() or staged.stat().st_size <= 44:
            raise ValueError(f'Ref2VA: could not decode audio {source.name}.')
        key = f'h3_refaudio_load_{slot}'
        graph[key] = {'class_type': 'LoadAudio', 'inputs': {'audio': staged.name}}
        node['inputs'][f'ref_audios.ref_audio_{slot}'] = [key, 0]
        print(f'[Comfy Neo Ref2VA] Audio reference {slot+1}: {source.name}', flush=True)
        return staged
    except Exception:
        staged.unlink(missing_ok=True)
        raise
