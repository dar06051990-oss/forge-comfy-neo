import json
import unittest
from pathlib import Path
from forge_h3.workflow_native import compile_h3_graph

ROOT = Path(__file__).resolve().parents[1]

class NativeGraphTests(unittest.TestCase):
    def test_all_original_api_workflows_use_only_native_nodes_after_compile(self):
        for filename in ('comfy_h3_ref2va_api_workflow.json', 'comfy_h3_fl2va_api_workflow.json','comfy_h3_api_workflow.json'):
            graph=json.loads((ROOT/filename).read_text('utf-8'))
            result=compile_h3_graph(graph,steps=9)
            names={node['class_type'] for node in result.values()}
            self.assertFalse(names.intersection({'ComfySwitchNode','PrimitiveInt','PrimitiveBoolean','PrimitiveFloat','ComfyMathExpression','EmptyLatentAdvancedSelector'}))
            self.assertEqual(result['105:9']['inputs']['steps'],9)
            self.assertEqual(result['105:9']['inputs']['model'],['105:121',0])
            self.assertEqual(result['105:16']['inputs']['model'],['105:121',0])
            self.assertNotIn('127',result)
            self.assertEqual(graph['105:9']['inputs']['steps'],['105:123',0])

    def test_non_turbo_uses_original_model(self):
        graph=json.loads((ROOT/'comfy_h3_fl2va_api_workflow.json').read_text('utf-8'))
        graph['105:126']['inputs']['value']=False
        result=compile_h3_graph(graph,steps=22)
        self.assertNotIn('105:121',result)
        self.assertEqual(result['105:9']['inputs']['model'],['105:6',0])
        self.assertEqual(result['105:16']['inputs']['model'],['105:6',0])

if __name__=='__main__':unittest.main()
