import types
import unittest
from unittest.mock import patch

from forge_h3 import hidden_subprocess


class DummyStartupInfo:
    def __init__(self):
        self.dwFlags = 0
        self.wShowWindow = -1


class HiddenWindowsProcessTest(unittest.TestCase):
    def test_non_windows_options_empty(self):
        with patch.object(hidden_subprocess, "os", types.SimpleNamespace(name="posix")):
            self.assertEqual(hidden_subprocess.hidden_windows_kwargs(), {})

    def test_windows_flags_hide_window(self):
        subprocess_fake = types.SimpleNamespace(STARTUPINFO=DummyStartupInfo,
             STARTF_USESHOWWINDOW=1, SW_HIDE=0, CREATE_NO_WINDOW=0x08000000)
        with patch.object(hidden_subprocess, "os", types.SimpleNamespace(name="nt")), \
             patch.object(hidden_subprocess, "subprocess", subprocess_fake):
            kw = hidden_subprocess.hidden_windows_kwargs()
        self.assertEqual(kw['creationflags'], 0x08000000)
        self.assertEqual(kw['startupinfo'].dwFlags, 1)
        self.assertEqual(kw['startupinfo'].wShowWindow, 0)


if __name__ == '__main__':
    unittest.main()
