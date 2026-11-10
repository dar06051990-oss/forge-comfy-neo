import json
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from forge_h3 import comfy_engine as e


class RegistryTests(unittest.TestCase):
    def test_builtins(self):
        result = e.list_workflows()
        self.assertEqual(len(result), 3)
        self.assertEqual([item[1] for item in result], ['h3:auto', 'h3:fl2va', 'h3:ref2va'])

    def test_api_graph(self):
        with tempfile.TemporaryDirectory() as p, patch.object(e, 'WORKFLOWS', Path(p)):
            path = Path(p) / 'anima.json'
            path.write_text(json.dumps({'10': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '{{prompt}}'}}}), encoding='utf8')
            self.assertEqual(len(e.list_workflows()), 4)
            self.assertEqual(e.load_api_workflow('api:anima.json')['10']['class_type'], 'CLIPTextEncode')

    def test_editor_format_rejected(self):
        with tempfile.TemporaryDirectory() as p, patch.object(e, 'WORKFLOWS', Path(p)):
            (Path(p) / 'editor.json').write_text('{"nodes":[]}', encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'Editor-format'):
                e.load_api_workflow('api:editor.json')

    def test_path_traversal_rejected(self):
        for key in ('api:../test.json', 'api:.hidden.json', 'api:/tmp/good.json', 'api:..\\bad.json'):
            with self.assertRaises((ValueError, OSError)):
                e.load_api_workflow(key)

    def test_template_types_preserved(self):
        orig = {'1': {'inputs': {'seed': '{{seed}}', 'steps': '{{steps}}', 'cfg': '{{cfg}}', 'text': 'Hello {{prompt}}'}, 'class_type': 'KSampler'}}
        result = e.bind_placeholders(orig, dict(seed=82, steps=4, cfg=3.5, prompt='world'))
        self.assertEqual(result['1']['inputs']['seed'], 82)
        self.assertEqual(result['1']['inputs']['steps'], 4)
        self.assertEqual(result['1']['inputs']['cfg'], 3.5)
        self.assertEqual(result['1']['inputs']['text'], 'Hello world')
        self.assertEqual(orig['1']['inputs']['seed'], '{{seed}}')

    def test_missing_image_token(self):
        with self.assertRaisesRegex(ValueError, 'requires'):
            e.bind_placeholders({'a': '{{image}}'}, {'image': None})

    def test_safe_output_paths(self):
        with tempfile.TemporaryDirectory() as p:
            core = Path(p)
            folder = core / 'output' / 'video'
            folder.mkdir(parents=True)
            (folder / 'a.mp4').write_bytes(b'video')
            self.assertEqual(e._safe_output_path(core, {'filename': 'a.mp4', 'subfolder': 'video', 'type': 'output'}), str(folder / 'a.mp4'))
            self.assertIsNone(e._safe_output_path(core, {'filename': '../secret.txt', 'type': 'output'}))
            images, video, files = e._collect_outputs({'outputs': {'4': {'videos': [{'filename': 'a.mp4', 'subfolder': 'video', 'type': 'output'}]}}}, core)
            self.assertEqual(video, str(folder / 'a.mp4'))
            self.assertEqual(len(files), 1)

    def test_input_images(self):
        with tempfile.TemporaryDirectory() as p:
            image = Path(p) / 'test.png'
            image.write_bytes(b'\x89PNG\r\n')
            self.assertTrue(e.image_data_url(image).startswith('data:image/png;base64,'))
            with self.assertRaises(ValueError):
                e.image_data_url(Path(p) / 'missing.png')

    def test_h3_mode(self):
        models = types.ModuleType('forge_h3.models')
        models.inspect_model = MagicMock(return_value=MagicMock(role='dit', mode='ref2va'))
        with patch.dict(sys.modules, {'forge_h3.models': models}):
            self.assertEqual(e._workflow_mode('some.safetensors'), 'ref2va')

    def test_generic_api_execution_via_private_backend(self):
        import threading
        import sys
        import types
        with tempfile.TemporaryDirectory() as root, patch.object(e, 'WORKFLOWS', Path(root) / 'workflows'):
            e.WORKFLOWS.mkdir()
            graph = {'3': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'Engine'}}}
            (e.WORKFLOWS / 'simple.json').write_text(json.dumps(graph), encoding='utf8')
            output = Path(root) / 'output'
            output.mkdir()
            (output / 'result.png').write_bytes(b'PNG')
            backend = types.ModuleType('forge_h3.comfy_full_backend')
            backend._active_prompt_id = None
            backend._lock = threading.RLock()
            backend._prepare_embedded_model_paths = MagicMock()
            backend.ensure_server = MagicMock()
            backend._session_token = MagicMock(return_value=1)
            backend._session_current = MagicMock(return_value=True)
            backend._cfg = MagicMock(return_value={'comfy_root': root})
            backend._post = MagicMock(return_value={'prompt_id': 'demo-id'})
            backend._get = MagicMock(return_value={'demo-id': {
                'status': {'status_str': 'success', 'completed': True},
                'outputs': {'3': {'images': [{'filename': 'result.png', 'type': 'output'}]}}}})
            with patch.dict(sys.modules, {'forge_h3.comfy_full_backend': backend}):
                images, video, files, message = e._run_api('api:simple.json', {'seed': 12}, None, None)
            self.assertEqual(images, [str(output / 'result.png')])
            self.assertIsNone(video)
            self.assertEqual(files, images)
            self.assertIn('Completed', message)
            self.assertIsNone(backend._active_prompt_id)
            backend._post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
