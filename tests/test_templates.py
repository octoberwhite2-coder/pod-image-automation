"""Registered mockup tests use synthetic artwork and canvases only."""
import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'pod_batch')]
from PIL import Image, ImageChops

class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'models').mkdir()
        (self.root / 'main.py').touch()
        with patch.dict(os.environ, {'COMFY_ROOT': str(self.root)}):
            self.c = importlib.import_module('compose_mockups')
        self.registry = self.root / 'templates.json'
        Image.new('RGB', (120, 160), 'white').save(self.root / 'canvas.png')
        self.entry = {
            'id': 'demo_shirt', 'garment': 'tshirt', 'color': 'white',
            'file': 'canvas.png', 'size': [120, 160],
            'placement': {'box': [20, 30, 100, 130], 'anchor': 'center'}
        }
        self.data = {'schema_version': 1, 'templates': [self.entry]}
        self.write()

    def write(self):
        self.registry.write_text(json.dumps(self.data), encoding='utf-8')

    def load(self, names=None):
        return self.c.load_templates(registry_path=self.registry, template_ids=names)

    def test_ids_select_distinct_same_color_templates(self):
        second = copy.deepcopy(self.entry)
        second.update(id='demo_hoodie', garment='hoodie')
        self.data['templates'].append(second)
        self.write()
        result = self.load(['demo_hoodie', 'demo_shirt', 'demo_hoodie'])
        self.assertEqual(list(result), ['demo_hoodie', 'demo_shirt'])
        for im in result.values():
            im.close()

    def test_unknown_disabled_duplicate_and_unsafe_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.load(['missing'])
        self.entry['enabled'] = False
        self.write()
        with self.assertRaises(ValueError):
            self.load(['demo_shirt'])
        self.entry['enabled'] = True
        self.data['templates'].append(copy.deepcopy(self.entry))
        self.write()
        with self.assertRaises(ValueError):
            self.load()
        self.data['templates'].pop()
        for bad in ['../outside', 'CON', 'a/b', 'a.b']:
            with self.subTest(id=bad):
                self.entry['id'] = bad
                self.write()
                with self.assertRaises(ValueError):
                    self.load()

    def test_changed_size_and_out_of_bounds_rejected(self):
        for key, value in [('size', [121, 160]), ('placement', {'box': [-1, 0, 50, 50]})]:
            with self.subTest(field=key):
                original = self.entry[key]
                self.entry[key] = value
                self.write()
                with self.assertRaises(ValueError):
                    self.load()
                self.entry[key] = original

    def test_invalid_metadata_and_external_image_paths_rejected(self):
        for field, value in [('file', '../outside.png'), ('file', str(self.root / 'canvas.png')),
                             ('size', [True, 160]), ('placement', {'box': [0, 0, 120, 161]}),
                             ('placement', {'box': [0, 0, 100, 100], 'anchor': 'invalid'}),
                             ('placement', {'box': [0, 0, 100, 100], 'inches': [0, 2]}),
                             ('placement', {'box': [0, 0, 100, 100], 'occlusion_polygons': [[[0, 0], [1, 1]]]}),
                             ('placement', {'box': [0, 0, 100, 100], 'inches': [float('nan'), 2]})]:
            with self.subTest(field=field, value=value):
                original = self.entry[field]
                self.entry[field] = value
                self.write()
                with self.assertRaises(ValueError):
                    self.load()
                self.entry[field] = original

    def test_contain_preserves_art_and_changes_only_print_box(self):
        template = self.load()['demo_shirt']
        for size in [(90, 30), (30, 90), (60, 60)]:
            with self.subTest(size=size):
                art = Image.new('RGBA', size, (200, 20, 30, 255))
                result, fitted = self.c.place(art, template, self.entry['placement'])
                bbox = ImageChops.difference(result, template).getbbox()
                self.assertTrue(20 <= bbox[0] < bbox[2] <= 100)
                self.assertTrue(30 <= bbox[1] < bbox[3] <= 130)
                self.assertLessEqual(abs(fitted.height - fitted.width * size[1] / size[0]), 1)
                self.assertEqual(fitted.getchannel('A').getextrema(), (255, 255))

    def test_occlusion_restores_template_pixels_without_cutting_print_art(self):
        self.entry['placement']['occlusion_polygons'] = [[[20, 30], [60, 30], [60, 80], [20, 80]]]
        self.write()
        template = self.load()['demo_shirt']
        result, fitted = self.c.place(Image.new('RGBA', (80, 100), 'red'), template, self.entry['placement'])
        self.assertEqual(result.getpixel((30, 40)), (255, 255, 255))
        self.assertEqual(result.getpixel((90, 110)), (255, 0, 0))
        self.assertEqual(fitted.getpixel((10, 10)), (255, 0, 0, 255))

    def test_photo_boundaries_never_infer_physical_print_size(self):
        art_path = self.root / '7_demo_00001_.png'
        Image.new('RGBA', (40, 60), 'red').save(art_path)
        with patch.object(self.c, 'OUT_DIR', str(self.root / 'mockups')), patch.object(self.c, 'PRINT_DIR', str(self.root / 'prints')):
            result = self.c.compose_one(art_path, self.load())
        self.assertEqual(list(result), ['demo_shirt_front'])
        self.assertTrue(Path(result['demo_shirt_front']).is_file())
        self.assertFalse((self.root / 'prints').exists())

    def test_explicit_inches_and_unique_template_print_names(self):
        self.entry['placement']['inches'] = [1, 2]
        second = copy.deepcopy(self.entry)
        second.update(id='demo_hoodie', garment='hoodie')
        second['placement']['inches'] = [2, 1]
        self.data['templates'].append(second)
        self.write()
        art_path = self.root / '7_demo_00001_.png'
        Image.new('RGBA', (40, 60), 'red').save(art_path)
        with patch.object(self.c, 'OUT_DIR', str(self.root / 'mockups')), patch.object(self.c, 'PRINT_DIR', str(self.root / 'prints')):
            result = self.c.compose_one(art_path, self.load())
        self.assertEqual(len(result), 2)
        for ident, size in [('demo_shirt', (300, 600)), ('demo_hoodie', (600, 300))]:
            with Image.open(self.root / 'prints' / ('7_demo_' + ident + '_front_print.png')) as im:
                self.assertEqual(im.size, size)
                self.assertEqual(im.mode, 'RGBA')

    def test_empty_selection_does_not_fall_back_to_business_templates(self):
        art_path = self.root / '7_demo.png'
        Image.new('RGBA', (30, 30), 'red').save(art_path)
        with patch.object(self.c, 'load_templates', side_effect=AssertionError('unexpected fallback')):
            self.assertEqual(self.c.compose_one(art_path, {}), {})

    def test_place_rejects_invalid_box_before_compositing(self):
        with self.assertRaises(ValueError):
            self.c.place(Image.new('RGBA', (10, 10), 'red'), Image.new('RGB', (20, 20)),
                         {'box': [0, 0, 25, 25], 'anchor': 'center'})


    def test_registry_cli_runs_without_comfyui_and_calibrates(self):
        inputs = self.root / 'png'
        inputs.mkdir()
        Image.new('RGBA', (40, 60), 'red').save(inputs / '7_demo.png')
        env = {k: v for k, v in os.environ.items() if not k.startswith(('COMFY_', 'POD_', 'ERP_', 'FEISHU_'))}
        env.update(POD_ENV_FILE=os.devnull, PYTHONUTF8='1')
        command = [sys.executable, str(ROOT / 'pod_batch' / 'compose_mockups.py'),
                   '--registry', str(self.registry), '--templates', 'demo_shirt',
                   '--png-dir', str(inputs), '--out-dir', str(self.root / 'out'),
                   '--print-dir', str(self.root / 'prints')]
        run = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue((self.root / 'out' / '7_demo_demo_shirt_front.png').exists())
        run = subprocess.run(command + ['--calibrate'], cwd=self.root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue((self.root / 'out' / '_calibrate_demo_shirt.png').exists())


    def test_demo_creates_only_synthetic_inputs_and_refuses_overwrite(self):
        target = self.root / 'demo'
        command = [sys.executable, str(ROOT / 'examples' / 'make_template_demo.py'), '--output', str(target)]
        run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue((target / 'templates.json').is_file())
        self.assertTrue((target / 'png' / '1_demo.png').is_file())
        first = (target / 'templates.json').read_bytes()
        run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual((target / 'templates.json').read_bytes(), first)


    def test_numbered_input_files_do_not_overwrite_each_other(self):
        outputs = []
        with patch.object(self.c, 'OUT_DIR', str(self.root / 'mockups')):
            for name in ['design_01.png', 'design_02.png']:
                source = self.root / name
                Image.new('RGBA', (30, 30), 'red').save(source)
                outputs.append(self.c.compose_one(source, self.load(), save_print=False)['demo_shirt_front'])
        self.assertNotEqual(outputs[0], outputs[1])
        self.assertTrue(all(Path(p).is_file() for p in outputs))

if __name__ == '__main__':
    unittest.main()
