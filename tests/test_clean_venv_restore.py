"""A deleted runtime/venv must not force manual cleanup of ready markers."""
import pathlib
import tempfile
import unittest
from unittest.mock import patch, Mock
from forge_h3 import runtime_manager as manager


class RestoreDeletedVenvTest(unittest.TestCase):
    def test_install_can_proceed_when_only_old_marker_remains(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = pathlib.Path(td) / 'runtime'
            runtime.mkdir()
            ready = runtime / 'private_env_ready.json'
            ready.write_text('{"old": true}', encoding='utf-8')
            script = runtime / 'setup_complete.py'
            script.write_text('print(123)', encoding='utf-8')
            log = runtime / 'forge_comfy_neo_install.log'
            proc = Mock()
            proc.poll.return_value = None
            proc.pid = 313
            with (
                patch.object(manager, 'RUNTIME', runtime),
                patch.object(manager, 'READY', ready),
                patch.object(manager, 'PRIVATE_PYTHON', runtime/'venv'/'Scripts'/'python.exe'),
                patch.object(manager, 'INSTALLER', script),
                patch.object(manager, 'LOG', log),
                patch.object(manager, 'runtime_state', return_value=(False, 'missing venv')),
                patch.object(manager.subprocess, 'Popen', return_value=proc) as run,
                patch.object(manager, '_process', None),
                patch.object(manager, '_completed', None),
            ):
                manager.start('install')
            self.assertTrue(run.called)

    def test_does_not_overwrite_partially_present_venv(self):
        with tempfile.TemporaryDirectory() as td:
            runtime = pathlib.Path(td) / 'runtime'
            (runtime/'venv').mkdir(parents=True)
            ready = runtime / 'private_env_ready.json'
            ready.write_text('{"old": true}', encoding='utf-8')
            with (
                patch.object(manager, 'RUNTIME', runtime),
                patch.object(manager, 'READY', ready),
                patch.object(manager, 'PRIVATE_PYTHON', runtime/'venv'/'Scripts'/'python.exe'),
                patch.object(manager, 'runtime_state', return_value=(False, 'partial venv')),
                patch.object(manager, '_process', None),
                patch.object(manager.subprocess, 'Popen') as run,
            ):
                result = manager.start('install')
            self.assertIn('partial', result.lower())
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

class InstallerMarkerRecoveryTest(unittest.TestCase):
    def test_stale_marker_is_archived_before_pinned_install(self):
        import importlib.util
        import sys
        setup_path = pathlib.Path(__file__).resolve().parents[1] / 'runtime' / 'setup_complete.py'
        spec = importlib.util.spec_from_file_location('_neo_setup_complete_for_test', setup_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            ext = pathlib.Path(td)
            runtime = ext / 'runtime'
            core = runtime / 'core'
            (core / 'comfy').mkdir(parents=True)
            (core / 'main.py').write_text('', encoding='utf-8')
            (core / 'LICENSE').write_text('GPL', encoding='utf-8')
            py = runtime / 'venv' / 'Scripts' / 'python.exe'
            ready = runtime / 'private_env_ready.json'
            ready.write_text('{"old": 1}', encoding='utf-8')
            for name in ['install_private_env.py','audit_private_runtime.py','audit_core_layout.py']:
                (runtime/name).write_text('', encoding='utf-8')
            def fake_run(args):
                if str(args[-1]).endswith('install_private_env.py'):
                    py.parent.mkdir(parents=True)
                    py.write_text('', encoding='utf-8')
                    ready.write_text('{"python": "good"}', encoding='utf-8')
                return 0
            with (
                patch.object(module, 'RUNTIME', runtime),
                patch.object(module, 'ROOT', ext),
                patch.object(module, 'CORE', core),
                patch.object(module, 'VENV_PYTHON', py),
                patch.object(module, 'MARKER', ready),
                patch.object(module, 'run', side_effect=fake_run),
            ):
                self.assertEqual(module.main(), 0)
            self.assertTrue((runtime/'private_env_ready.stale.json').exists())
            self.assertIn('old', (runtime/'private_env_ready.stale.json').read_text('utf-8'))
            self.assertTrue(ready.exists())

