"""Forge Comfy Neo v1.0.3 — interactive installation of the private runtime.

Only Python standard-library dependencies. The Forge interpreter coordinates
installing; dependency installation runs exclusively in runtime/venv.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from .hidden_subprocess import hidden_windows_kwargs

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
LOG = RUNTIME / 'forge_comfy_neo_install.log'
READY = RUNTIME / 'private_env_ready.json'
PRIVATE_PYTHON = RUNTIME / 'venv' / 'Scripts' / 'python.exe'
CORE = RUNTIME / 'core'
INSTALLER = RUNTIME / 'setup_complete.py'
GIT_UPDATER = RUNTIME / 'update_from_git.py'
COMFY_PIN = 'bdb0816a292d221efeb6ec4ae41bcdff3f91fc86'
TORCH_PIN = '2.11.0'

_lock = threading.RLock()
_process = None
_action = None
_completed = None


def runtime_state():
    """Return (ready, explanation) without importing torch or starting CUDA."""
    if not (CORE / 'main.py').is_file() or not (CORE / 'comfy' / 'sd.py').is_file():
        return False, 'ComfyUI core is missing.'
    if not PRIVATE_PYTHON.is_file():
        return False, 'Private Python is missing.'
    if not READY.is_file():
        return False, 'Private runtime has not been verified.'
    try:
        marker = json.loads(READY.read_text(encoding='utf-8'))
        if Path(marker['python']).resolve() != PRIVATE_PYTHON.resolve():
            return False, 'Private Python path changed (runtime cannot be moved as a plain folder).'
        if marker.get('comfy_source') != COMFY_PIN:
            return False, 'ComfyUI source differs from the pinned release.'
        if marker.get('torch') != TORCH_PIN or marker.get('torch_cuda') != '13.0':
            return False, 'PyTorch/CUDA version differs from the pinned release.'
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        return False, f'Runtime marker is invalid: {exc}'
    return True, 'Installed — private Python, CUDA PyTorch and ComfyUI source are present.'


def _local_only(request):
    """Do not let an internet-exposed Forge UI trigger code downloads."""
    client = getattr(request, 'client', None)
    ip = getattr(client, 'host', '')
    return ip in ('127.0.0.1', '::1', 'localhost')


def _short_log(lines=11):
    if not LOG.is_file():
        return ''
    # Bounded read, including very large pip download logs.
    with LOG.open('rb') as f:
        f.seek(0, os.SEEK_END)
        length = f.tell()
        f.seek(max(0, length - 16000))
        raw = f.read()
    return '\n'.join(raw.decode('utf-8', errors='replace').splitlines()[-lines:])


def status():
    with _lock:
        ready, reason = runtime_state()
        if _process is not None:
            code = _process.poll()
            if code is None:
                heading = f'{_action or "Install"} in progress (PID {_process.pid}).'
            elif code == 0:
                if _action == 'Update':
                    heading = 'Git update finished. Restart Forge if new code was downloaded.'
                else:
                    heading = f'Install finished (structure/CUDA check only). ' + (reason if ready else 'Runtime verification is still incomplete.')
            else:
                heading = f'{_action or "Install"} failed (exit code {code}). Review the log below.'
        elif _completed:
            heading = _completed
        else:
            heading = reason
        tail = _short_log()
        return heading + (('\n\nLast log lines:\n' + tail) if tail else '')


def start(action: str, request=None):
    """Called from an explicit Gradio button click; returns immediately."""
    global _process, _action, _completed
    if action not in ('install', 'update'):
        raise ValueError('Unsupported runtime action')
    if request is not None and not _local_only(request):
        return 'For security, runtime installation and updates require opening Forge locally (localhost).'
    with _lock:
        if _process is not None and _process.poll() is None:
            return status()
        ready, reason = runtime_state()
        if action == 'install':
            if ready:
                _completed = 'Runtime is already installed and verified.'
                return status()
            # A deliberately deleted runtime/venv may leave behind the old
            # ready marker. This is safe to recreate from Install because the
            # *entire* private environment directory is absent. Never touch an
            # existing or partially created venv automatically.
            empty_env = not (RUNTIME / 'venv').exists()
            if PRIVATE_PYTHON.exists() or (READY.exists() and not empty_env):
                return (reason + ' Existing partial/moved installation was not overwritten. '
                        'Use the recovery instructions in README.')
            if (RUNTIME / 'venv').exists():
                return (reason + ' A partial private venv is present. '
                        'Back it up and follow the recovery instructions in README.')
            script = INSTALLER
            if not script.is_file():
                return 'Missing runtime/setup_complete.py — repair the Git checkout first.'
        else:
            # Git update is usable even before the private runtime is installed.
            # It never touches the runtime/venv or installs packages.
            script = GIT_UPDATER
            if not script.is_file():
                return 'Missing runtime/update_from_git.py — repair the Git checkout first.'
        RUNTIME.mkdir(parents=True, exist_ok=True)
        _action = 'Install' if action == 'install' else 'Update'
        _completed = None
        LOG.write_text('Forge Comfy Neo v1.0.3 — ' + _action + '\n'
                       'ComfyUI pinned commit: ' + COMFY_PIN + '\n'
                       'Torch pinned version: ' + TORCH_PIN + ' (cu130)\n', encoding='utf-8')
        try:
            stream = LOG.open('ab', buffering=0)
            try:
                _process = subprocess.Popen([sys.executable, '-I', str(script)],
                                            cwd=str(ROOT), stdout=stream,
                                            stderr=subprocess.STDOUT,
                                            **hidden_windows_kwargs())
            finally:
                stream.close()
        except (OSError, subprocess.SubprocessError) as exc:
            _process = None
            _completed = f'Could not launch installer: {exc}'
        return status()


def _display_status():
    """A clear summary and an optional log, without claiming GPU testing."""
    with _lock:
        ready, reason = runtime_state()
        running = _process is not None and _process.poll() is None
        finished = _process is not None and _process.poll() == 0 and _action == 'Install'
        failed = _process is not None and _process.poll() not in (None, 0)
        tail = _short_log(24)
        if _action == 'Update' and _process is not None:
            if running:
                message = '### Updating Comfy Neo source\nChecking tracked Git branch. The private venv is untouched.'
            elif _process.poll() == 0:
                message = '### ✅ Update completed\nRestart Forge to apply any changed source files.'
            else:
                message = '### ❌ Update failed\nOpen **Show installation log** for details.'
        elif running:
            stage = 'Downloading / preparing runtime'
            if '[3/3]' in tail:
                stage = 'Step 3/3 — Checking installed runtime'
            elif '[2/3]' in tail:
                stage = 'Step 2/3 — Installing isolated Python and dependencies'
            elif '[1/3]' in tail:
                stage = 'Step 1/3 — Preparing ComfyUI source'
            message = f'### Installation in progress\n{stage}. The Forge window can stay open.'
        elif ready:
            message = ('### ✅ Comfy Neo installed successfully\n'
                       '**Restart Forge to activate the full Comfy Neo interface.**  \n'
                       'Private runtime files are present and structural checks passed. '
                       '**GPU generation has not yet been tested.**')
            if not (finished or _action == 'Install'):
                message = '### ✅ Comfy Neo runtime ready\n' + reason
        elif failed:
            message = ('### ❌ Installation failed\n'
                       'Open **Show installation log** for the error. '
                       'Existing Forge Python was not modified.')
        else:
            message = f'### Comfy Neo is not installed\n{reason}\nClick **Install** to begin.'
        return message, (tail or 'No installation log yet.')


def _runtime_widgets(*, install_screen=False):
    import gradio as gr

    if install_screen:
        gr.Markdown('## Forge Comfy Neo v1.0.3 — first-time setup')
        gr.Markdown('Install the isolated ComfyUI core and CUDA runtime. This downloads large packages; '
                    '**Forge Python and other ComfyUI installations are not modified.**')
    else:
        gr.Markdown('### Comfy Neo Runtime')
        gr.Markdown('**Update** fast-forwards the extension from its tracked Git branch. '
                    'It never overwrites local edits, updates PyTorch or modifies the private `venv`. '
                    'Restart Forge after a source update; runtime compatibility is checked at startup.')
    with gr.Row():
        install = gr.Button('Install' if install_screen else 'Update', variant='primary')
        refresh = None if install_screen and hasattr(gr, 'Timer') else gr.Button('Refresh Status', variant='secondary')
    overview, log = _display_status()
    info = gr.Markdown(overview)
    with gr.Accordion('Show installation log', open=False):
        details = gr.Textbox(label='Technical details', value=log,
                             interactive=False, lines=8, max_lines=16)

    def run_action(request):
        start('install' if install_screen else 'update', request=request)
        return _display_status()

    # Gradio injects Request into this annotated callback.
    run_action.__annotations__['request'] = gr.Request

    install.click(fn=run_action, inputs=None, outputs=[info, details],
                  queue=False, show_progress=False)
    if refresh is not None:
        refresh.click(fn=_display_status, inputs=None, outputs=[info, details],
                      queue=False, show_progress=False)
    if hasattr(gr, 'Timer'):
        ticker = gr.Timer(3.0)
        ticker.tick(fn=_display_status, inputs=None, outputs=[info, details],
                    queue=False, show_progress=False)
    if install_screen:
        gr.Markdown('After successful installation, **restart Forge** to load the full Comfy Neo interface. '
                    'Compatible model files remain in your existing Forge model folders.')
    else:
        gr.Markdown('Git must be installed and the extension must be cloned from a tracked branch. '
                    'A newer extension version may require a compatible runtime migration; '
                    'the Update action never changes its Python/PyTorch environment without an explicit installer.')


def create_install_screen():
    _runtime_widgets(install_screen=True)


def settings_component(label=None, value=None, elem_id=None, **_kwargs):
    """Forge Settings component factory; state is never persisted to config."""
    import gradio as gr
    _runtime_widgets(install_screen=False)
    return gr.State(None)
