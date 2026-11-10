"""Kill the dedicated Comfy worker as soon as the Forge process exits.

Windows note: merely being able to OpenProcess() does *not* mean a process is
still running. A terminated process object may stay queryable while another
handle to it exists. v14-v17 used that test, which could leave the Comfy worker
alive after Forge had closed. This version waits on a real SYNCHRONIZE handle to
the Forge process; Windows signals that handle at the instant Forge terminates.
"""
import argparse
import os
import subprocess
import time


def _kill_tree(pid):
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=15,
            )
        else:
            try:
                os.killpg(os.getpgid(int(pid)), 15)
            except Exception:
                os.kill(int(pid), 15)
    except Exception:
        pass


def _pid_running_windows(pid):
    """True only while PID is genuinely STILL_ACTIVE, not merely openable."""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(h)


def _wait_for_parent_windows(parent_pid, child_pid):
    """Block on Forge's process handle and kill Comfy when it is signalled."""
    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    parent = kernel32.OpenProcess(SYNCHRONIZE, False, int(parent_pid))
    if not parent:
        # Forge was already gone before the watchdog got its handle.
        _kill_tree(child_pid)
        return
    try:
        while True:
            result = kernel32.WaitForSingleObject(parent, 1000)
            if result == WAIT_OBJECT_0:
                _kill_tree(child_pid)
                return
            if result != WAIT_TIMEOUT:
                # Unexpected wait failure: fail safe and do not leave a 20+ GB
                # orphan worker behind.
                _kill_tree(child_pid)
                return
            if not _pid_running_windows(child_pid):
                return
    finally:
        kernel32.CloseHandle(parent)


def _pid_running_posix(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent", type=int, required=True)
    ap.add_argument("--child", type=int, required=True)
    ns = ap.parse_args()

    if os.name == "nt":
        _wait_for_parent_windows(ns.parent, ns.child)
        return

    while _pid_running_posix(ns.child):
        if not _pid_running_posix(ns.parent):
            _kill_tree(ns.child)
            return
        time.sleep(0.5)


if __name__ == "__main__":
    main()
