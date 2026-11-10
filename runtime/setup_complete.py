"""Install missing local backend source and private Python with one command.

Uses the existing pinned DEV01 source installer and DEV03 private env installer.
Never re-installs or upgrades a complete existing environment automatically.
"""
from pathlib import Path
import json
import subprocess
import sys

RUNTIME=Path(__file__).resolve().parent
ROOT=RUNTIME.parent
CORE=RUNTIME/'core'
VENV_PYTHON=RUNTIME/'venv'/'Scripts'/'python.exe'
MARKER=RUNTIME/'private_env_ready.json'
PINNED='bdb0816a292d221efeb6ec4ae41bcdff3f91fc86'

def run(args):
    cmd=[str(x) for x in args]
    print('> '+' '.join(cmd),flush=True)
    return subprocess.run(cmd,cwd=ROOT,check=False).returncode

def main():
    print('Forge Comfy Neo v1.0.3 — one-time runtime setup')
    print('All Comfy source + Python packages are installed inside:',ROOT)
    print('Existing Forge Python is ONLY used as an installer coordinator.')
    if not (CORE/'main.py').is_file():
        installer=RUNTIME/'bootstrap_embedded.py'
        if not installer.is_file():
            print('[ERROR] Missing bootstrap_embedded.py');return 1
        print('[1/3] Downloading pinned headless source...')
        rc=run([sys.executable,installer])
        if rc:return rc
    else:
        print('[1/3] Existing Comfy source found; skipping download.')
    if not (CORE/'LICENSE').is_file():
        print('[ERROR] Bundled Comfy license not found.');return 2
    # Recovery for a common user action: deleting the large runtime/venv
    # folder to save space while leaving its tiny ready-marker behind.
    # Move the stale marker aside rather than treating it as a healthy venv.
    # Do not auto-touch a partially present venv; it may contain user changes.
    if MARKER.is_file() and not VENV_PYTHON.is_file() and not (RUNTIME/'venv').exists():
        stale = RUNTIME/'private_env_ready.stale.json'
        counter = 1
        while stale.exists():
            stale = RUNTIME/f'private_env_ready.stale.{counter}.json'
            counter += 1
        MARKER.rename(stale)
        print('[INFO] Private venv was deleted; saved stale marker as', stale.name, flush=True)
        print('[INFO] Recreating private Python from the pinned recipe.', flush=True)
    if VENV_PYTHON.is_file() and MARKER.is_file():
        try:
            m=json.loads(MARKER.read_text('utf-8'))
            if Path(m['python']).resolve()!=VENV_PYTHON.resolve():
                raise ValueError('runtime marker refers to a different Python')
            if m.get('comfy_source')!=PINNED:
                raise ValueError('runtime marker references a different Comfy source')
            print('[2/3] Existing pinned private Python found; skipping all installs.')
        except Exception as exc:
            print('[ERROR] Existing runtime marker mismatch:',exc)
            print('       No automatic repair. Back up runtime and follow README Recovery.')
            return 2
    else:
        if VENV_PYTHON.exists() or MARKER.exists():
            print('[ERROR] Incomplete runtime detected. No automatic overwrite.')
            print('       Follow README Recovery after making a backup.')
            return 2
        installer=RUNTIME/'install_private_env.py'
        if not installer.is_file():
            print('[ERROR] Missing install_private_env.py');return 2
        print('[2/3] Installing private Python and pinned GPU libraries...')
        rc=run([sys.executable,installer]);
        if rc:return rc
    print('[3/3] Verifying the private runtime and core structure...')
    for script in ('audit_private_runtime.py','audit_core_layout.py'):
        path=RUNTIME/script
        if not path.is_file():
            print('[ERROR] Audit script missing:',script);return 2
        rc=run([VENV_PYTHON,path])
        if rc:
            print('[ERROR] Audit failed; do not launch until fixed.')
            return rc
    print('[OK] Local source + Python environment ready. No external ComfyUI install is needed.')
    return 0

if __name__=='__main__':sys.exit(main())
