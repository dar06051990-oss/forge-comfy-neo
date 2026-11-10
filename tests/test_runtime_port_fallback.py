"""Regression: one Forge install must not use or terminate another's Comfy server."""
import unittest
from unittest.mock import patch, Mock

from forge_h3 import runtime_manager


class PrivatePortTests(unittest.TestCase):
    def setUp(self):
        from forge_h3 import comfy_full_backend
        self.backend = comfy_full_backend

    def test_occupied_default_selects_free_port_without_killing_foreign_worker(self):
        with patch.object(self.backend, '_proc', None), \
             patch.object(self.backend, 'PORT', 8191), \
             patch.object(self.backend, '_user_selected_port', return_value=False), \
             patch.object(self.backend, '_port_available', side_effect=lambda p: p == 8193), \
             patch.object(self.backend, '_kill_process_tree') as killer:
            self.backend._auto_select_worker_port()
            self.assertEqual(self.backend.PORT, 8193)
            killer.assert_not_called()

    def test_user_selected_port_never_silently_changes(self):
        with patch.object(self.backend, '_proc', None), \
             patch.object(self.backend, 'PORT', 8191), \
             patch.object(self.backend, '_user_selected_port', return_value=True), \
             patch.object(self.backend, '_port_available') as check:
            self.backend._auto_select_worker_port()
            self.assertEqual(self.backend.PORT, 8191)
            check.assert_not_called()

    def test_existing_owned_worker_port_never_changes(self):
        active = Mock()
        active.poll.return_value = None
        with patch.object(self.backend, '_proc', active), \
             patch.object(self.backend, 'PORT', 8191), \
             patch.object(self.backend, '_user_selected_port', return_value=False), \
             patch.object(self.backend, '_port_available') as check:
            self.backend._auto_select_worker_port()
            check.assert_not_called()
            self.assertEqual(self.backend.PORT, 8191)

    def test_no_available_ports_fails_instead_of_using_foreign_server(self):
        with patch.object(self.backend, '_proc', None), \
             patch.object(self.backend, 'PORT', 8191), \
             patch.object(self.backend, '_user_selected_port', return_value=False), \
             patch.object(self.backend, '_port_available', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'no free local TCP port'):
                self.backend._auto_select_worker_port()

    def test_foreign_server_never_considered_our_worker(self):
        with patch.object(self.backend, '_proc', None), \
             patch.object(self.backend, '_auto_select_worker_port'), \
             patch.object(self.backend, '_alive', return_value=True), \
             patch.object(self.backend, '_kill_process_tree') as killer:
            with self.assertRaisesRegex(RuntimeError, 'another process'):
                self.backend.ensure_server(timeout=0.1)
            killer.assert_not_called()


class InstallStatusTests(unittest.TestCase):
    def test_ready_and_completed_reports_restart_without_false_gpu_claim(self):
        process = Mock()
        process.poll.return_value = 0
        with patch.object(runtime_manager, '_process', process), \
             patch.object(runtime_manager, '_action', 'Install'), \
             patch.object(runtime_manager, 'runtime_state', return_value=(True, 'ready')), \
             patch.object(runtime_manager, '_short_log', return_value='[OK] structural check'):
            summary, log = runtime_manager._display_status()
        self.assertIn('Restart Forge', summary)
        self.assertIn('GPU generation has not yet been tested', summary)
        self.assertIn('structural check', log)

    def test_install_failure_does_not_claim_success(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(runtime_manager, '_process', process), \
             patch.object(runtime_manager, '_action', 'Install'), \
             patch.object(runtime_manager, 'runtime_state', return_value=(False, 'missing')), \
             patch.object(runtime_manager, '_short_log', return_value='error: missing'):
            summary, log = runtime_manager._display_status()
        self.assertIn('Installation failed', summary)
        self.assertNotIn('successfully', summary)
        self.assertIn('error: missing', log)


if __name__ == '__main__':
    unittest.main()
