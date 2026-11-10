"""Regression tests for console-free owned-worker cleanup on Windows."""
import ctypes
import unittest
from unittest.mock import Mock

from forge_h3.windows_job import (
    _BasicLimitInformation, _ExtendedLimitInformation, WindowsKillJob
)


class WindowsJobTests(unittest.TestCase):
    def test_native_information_layout(self):
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(_BasicLimitInformation), 64)
            self.assertEqual(ctypes.sizeof(_ExtendedLimitInformation), 144)

    def test_job_is_kill_on_close_and_assigns_our_process_only(self):
        k = Mock()
        k.CreateJobObjectW.return_value = 123
        k.SetInformationJobObject.return_value = True
        k.AssignProcessToJobObject.return_value = True
        captured = []

        def set_info(handle, info_class, info_ptr, info_size):
            info = ctypes.cast(info_ptr, ctypes.POINTER(_ExtendedLimitInformation)).contents
            captured.append((handle, info_class, info_size, info.BasicLimitInformation.LimitFlags))
            return True
        k.SetInformationJobObject.side_effect = set_info
        job = WindowsKillJob(kernel32=k)
        self.assertEqual(captured, [(123, 9, ctypes.sizeof(_ExtendedLimitInformation), 0x2000)])
        proc = Mock(_handle=54321)
        job.assign(proc)
        k.AssignProcessToJobObject.assert_called_once_with(123, 54321)
        job.close()
        job.close()
        k.CloseHandle.assert_called_once_with(123)

    def test_assign_pid_when_virtualenv_child_was_spawned_early(self):
        k = Mock()
        k.CreateJobObjectW.return_value = 123
        k.SetInformationJobObject.return_value = True
        k.OpenProcess.return_value = 9009
        k.IsProcessInJob.side_effect = lambda ph, jh, ptr: True
        job = WindowsKillJob(kernel32=k)
        job.assign_pid(81999)
        k.AssignProcessToJobObject.assert_called_once_with(123, 9009)
        k.CloseHandle.assert_called_once_with(9009)
        job.close()

    def test_already_enrolled_listener_is_not_reassigned(self):
        k = Mock()
        k.CreateJobObjectW.return_value = 123
        k.SetInformationJobObject.return_value = True
        k.OpenProcess.return_value = 9009
        def in_job(ph, jh, ptr):
            ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int)).contents.value = 1
            return True
        k.IsProcessInJob.side_effect = in_job
        job = WindowsKillJob(kernel32=k)
        job.assign_pid(81999)
        k.AssignProcessToJobObject.assert_not_called()
        job.close()

    def test_closed_job_cannot_take_new_process(self):
        k = Mock()
        k.CreateJobObjectW.return_value = 123
        k.SetInformationJobObject.return_value = True
        job = WindowsKillJob(kernel32=k)
        job.close()
        with self.assertRaisesRegex(RuntimeError, 'already closed'):
            job.assign(Mock(_handle=88))

    def test_job_creation_failure_never_assigns_process(self):
        k = Mock()
        k.CreateJobObjectW.return_value = 0
        with self.assertRaises(OSError):
            WindowsKillJob(kernel32=k)
        k.AssignProcessToJobObject.assert_not_called()

    def test_shutdown_closes_owned_job_even_if_worker_is_alive(self):
        from unittest.mock import patch
        from forge_h3 import comfy_full_backend as backend
        proc = Mock()
        proc.pid = 444
        proc.poll.return_value = None
        job = Mock()
        with patch.object(backend, '_proc', proc), \
             patch.object(backend, '_worker_job', job), \
             patch.object(backend, '_watchdog', None), \
             patch.object(backend, '_log', None), \
             patch.object(backend, '_worker_job_listener_pid', 555), \
             patch.object(backend, '_kill_process_tree') as killer:
            self.assertTrue(backend.shutdown_worker('test'))
        job.close.assert_called_once_with()
        killer.assert_not_called()
        proc.wait.assert_called_once_with(timeout=5)

    def test_watchdog_has_no_conflicting_detached_process_flags(self):
        from pathlib import Path
        source=(Path(__file__).parents[1]/'forge_h3'/'comfy_full_backend.py').read_text('utf-8')
        self.assertNotIn("getattr(subprocess,'DETACHED_PROCESS'", source)
        self.assertNotIn("getattr(subprocess,'CREATE_NEW_PROCESS_GROUP'", source)
        self.assertIn('WindowsKillJob()', source)
        self.assertIn('candidate_job.assign(_proc)', source)
        self.assertIn('_worker_job.assign(proc)', source)


if __name__ == '__main__':
    unittest.main()
