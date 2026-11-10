"""Regression for a Forge Hires scheduler accidentally submitted to Comfy Neo."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from forge_h3 import comfy_sampler_ui as sampler_ui


class ForgeSchedulerBridgeTests(unittest.TestCase):
    def setUp(self):
        self.choices = patch.object(sampler_ui, 'available_choices', return_value=(
            ['er_sde', 'res_multistep', 'euler'], ['simple', 'beta', 'normal']))
        self.choices.start()
        self.addCleanup(self.choices.stop)
        self.opts = SimpleNamespace(comfy_anima_t2i_sampler='er_sde',
                                    comfy_anima_t2i_scheduler='beta',
                                    comfy_anima_i2i_scheduler='normal',
                                    h3_t2i_sampler='res_multistep',
                                    h3_t2i_scheduler='simple')

    def resolve(self, sampler='er_sde', scheduler='Use same scheduler', *, preset='comfy_anima', mode='t2i'):
        return sampler_ui.resolve_forge_sampling(
            sampler, scheduler, preset=preset, mode=mode, opts=self.opts)

    def test_anima_hires_sentinel_reads_preset_beta(self):
        self.assertEqual(self.resolve(), ('er_sde', 'beta'))

    def test_h3_hires_sentinel_reads_h3_default(self):
        self.assertEqual(self.resolve('res_multistep', preset='h3'), ('res_multistep', 'simple'))

    def test_img2img_uses_its_own_scheduler(self):
        self.assertEqual(self.resolve(mode='i2i'), ('er_sde', 'normal'))

    def test_preserve_selected_dropdown_value(self):
        self.assertEqual(self.resolve(scheduler='normal'), ('er_sde', 'normal'))

    def test_resolve_hires_sampler_if_sent(self):
        self.assertEqual(self.resolve('Use same sampler'), ('er_sde', 'beta'))

    def test_invalid_concrete_scheduler_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not available'):
            self.resolve(scheduler='made_up_scheduler')

    def test_invalid_stored_scheduler_uses_safe_anima_default(self):
        self.opts.comfy_anima_t2i_scheduler = 'Use same scheduler'
        self.assertEqual(self.resolve(), ('er_sde', 'beta'))

    def test_no_engine_cross_contamination(self):
        self.assertEqual(self.resolve('res_multistep', 'Use same scheduler', preset='h3'), ('res_multistep', 'simple'))


if __name__ == '__main__':
    unittest.main()
