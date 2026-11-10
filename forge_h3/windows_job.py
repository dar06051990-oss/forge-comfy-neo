"""Own only the child ComfyUI processes via a Windows kill-on-close job.

The Forge process alone holds the job handle. When Forge exits (normally or
abruptly), Windows closes that handle and kills every process in the job,
including the private venv redirector and its spawned interpreter.

No foreign ComfyUI process is ever assigned to this job.
"""
from __future__ import annotations

import ctypes
import os


# Use explicitly sized Win32 fields: ctypes.wintypes.DWORD has different sizes
# on non-Windows hosts, which would invalidate the job information structure.
DWORD = ctypes.c_uint32
ULONG_PTR = ctypes.c_size_t
LARGE_INTEGER = ctypes.c_int64


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', LARGE_INTEGER),
        ('PerJobUserTimeLimit', LARGE_INTEGER),
        ('LimitFlags', DWORD),
        ('MinimumWorkingSetSize', ULONG_PTR),
        ('MaximumWorkingSetSize', ULONG_PTR),
        ('ActiveProcessLimit', DWORD),
        ('Affinity', ULONG_PTR),
        ('PriorityClass', DWORD),
        ('SchedulingClass', DWORD),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [
        ('ReadOperationCount', ctypes.c_uint64),
        ('WriteOperationCount', ctypes.c_uint64),
        ('OtherOperationCount', ctypes.c_uint64),
        ('ReadTransferCount', ctypes.c_uint64),
        ('WriteTransferCount', ctypes.c_uint64),
        ('OtherTransferCount', ctypes.c_uint64),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', _BasicLimitInformation),
        ('IoInfo', _IOCounters),
        ('ProcessMemoryLimit', ULONG_PTR),
        ('JobMemoryLimit', ULONG_PTR),
        ('PeakProcessMemoryUsed', ULONG_PTR),
        ('PeakJobMemoryUsed', ULONG_PTR),
    ]


class WindowsKillJob:
    """Job Object bound to one Forge install's spawned private processes.

    When the LAST handle closes, Windows forcibly stops these processes. The
    handle must never be inherited by the worker, or it would keep itself alive.
    """
    def __init__(self, kernel32=None):
        if os.name != 'nt' and kernel32 is None:
            raise OSError('Windows Job Objects require Windows')
        k = kernel32 or ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, DWORD]
        k.SetInformationJobObject.restype = ctypes.c_int
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.AssignProcessToJobObject.restype = ctypes.c_int
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int
        h = k.CreateJobObjectW(None, None)  # non-inheritable anonymous handle
        if not h:
            raise OSError(getattr(ctypes, 'get_last_error', lambda: 0)(), 'CreateJobObjectW failed')
        self._kernel32 = k
        self._handle = h
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            self.close()
            raise OSError(getattr(ctypes, 'get_last_error', lambda: 0)(), 'SetInformationJobObject failed')

    def assign(self, proc):
        if not self._handle:
            raise RuntimeError('Comfy process job has already closed')
        native_handle = getattr(proc, '_handle', None)
        if not native_handle:
            raise RuntimeError('Child process has no Windows handle')
        if not self._kernel32.AssignProcessToJobObject(self._handle, int(native_handle)):
            raise OSError(getattr(ctypes, 'get_last_error', lambda: 0)(), 'AssignProcessToJobObject failed')

    def assign_pid(self, pid):
        """Also enrol a verified owned interpreter if the venv redirector raced.

        Called only after the parent code has confirmed that *pid* belongs to
        this extension's private worker tree. Never enumerate/attach by port
        without verifying ancestry first.
        """
        if not self._handle:
            raise RuntimeError('Comfy process job has already closed')
        k = self._kernel32
        k.OpenProcess.argtypes = [DWORD, ctypes.c_int, DWORD]
        k.OpenProcess.restype = ctypes.c_void_p
        k.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        k.IsProcessInJob.restype = ctypes.c_int
        PROCESS_TERMINATE = 0x0001
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ph = k.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA |
                           PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if not ph:
            raise OSError(getattr(ctypes, 'get_last_error', lambda: 0)(), 'OpenProcess failed')
        try:
            in_job = ctypes.c_int()
            if not k.IsProcessInJob(ph, self._handle, ctypes.byref(in_job)):
                raise OSError(getattr(ctypes, 'get_last_error', lambda: 0)(), 'IsProcessInJob failed')
            if not in_job.value and not k.AssignProcessToJobObject(self._handle, ph):
                raise OSError(getattr(ctypes, 'get_last_error', lambda: 0)(), 'AssignProcessToJobObject failed')
        finally:
            k.CloseHandle(ph)

    def close(self):
        if getattr(self, '_handle', None):
            h, self._handle = self._handle, None
            self._kernel32.CloseHandle(h)

    def __del__(self):
        # The owning Forge interpreter normally closes its handle on process exit.
        # This also covers module cleanup during a normal interpreter shutdown.
        try:
            self.close()
        except Exception:
            pass
