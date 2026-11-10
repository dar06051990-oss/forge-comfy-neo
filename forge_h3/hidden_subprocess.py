"""Best-effort Windows console suppression for background child processes.

The Comfy Neo backend is headless; users should never need to interact with
Windows console windows from its worker and auxiliary Python processes.
"""
from __future__ import annotations

import os
import subprocess


def hidden_windows_kwargs():
    """Additional Popen/run options; no effect outside Windows.

    CREATE_NO_WINDOW prevents allocation of a new console. STARTUPINFO's
    SW_HIDE additionally requests a hidden window from console launchers.
    """
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startup}
