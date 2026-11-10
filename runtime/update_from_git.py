"""Update the Forge Comfy Neo *extension source* from its tracked Git branch.

No dependency installation is performed here. Upstream runtime pins, if changed,
are handled by the newly installed extension after Forge is restarted.
Only the user's explicit local Settings -> Update action calls this coordinator.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    command = [str(arg) for arg in args]
    print('> ' + subprocess.list2cmdline(command), flush=True)
    return subprocess.run(command, cwd=str(ROOT), check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def main() -> int:
    print('Forge Comfy Neo: extension update from Git', flush=True)
    print('Repository:', ROOT, flush=True)
    print('Python/PyTorch packages and runtime/venv will NOT be changed.', flush=True)
    git = shutil.which('git')
    if git is None:
        print('[ERROR] Git is not available in PATH.', flush=True)
        return 1
    try:
        toplevel = Path(run(git, 'rev-parse', '--show-toplevel').stdout.strip()).resolve()
        if toplevel != ROOT.resolve():
            print('[ERROR] This extension is not a standalone Git checkout.', flush=True)
            print('Install it using Forge Extensions -> Install from URL.', flush=True)
            return 1
        branch = run(git, 'symbolic-ref', '--quiet', '--short', 'HEAD').stdout.strip()
        remote_branch = run(git, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}').stdout.strip()
        if not branch or not remote_branch:
            raise RuntimeError('A branch with a configured Git upstream is required')
        dirty = run(git, 'status', '--porcelain', '--untracked-files=no').stdout.strip()
        if dirty:
            print('[ERROR] Modified tracked files detected; refusing to overwrite local edits.', flush=True)
            print(dirty, flush=True)
            return 1
        before = run(git, 'rev-parse', 'HEAD').stdout.strip()
        print(f'Checking {branch} -> {remote_branch}', flush=True)
        result = run(git, 'pull', '--ff-only')
        print(result.stdout, flush=True)
        after = run(git, 'rev-parse', 'HEAD').stdout.strip()
        if before == after:
            print('[OK] Extension source is already up to date.', flush=True)
        else:
            print(f'[OK] Updated extension: {before[:12]} -> {after[:12]}', flush=True)
            print('[IMPORTANT] Restart Forge to load updated code and runtime requirements.', flush=True)
        return 0
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stdout or '', flush=True)
        print('[ERROR] Git update stopped; no forced checkout or reset was performed.', flush=True)
        print(f'{type(exc).__name__}: {exc}', flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
