"""Offline tests: synthetic inputs only, no production API or model execution."""
import ast
import contextlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'pod_batch'))

def run_isolated(code, extra=None):
    env = {k:v for k,v in os.environ.items() if not k.startswith(('FEISHU_', 'ERP_', 'POD_', 'COMFY_'))}
    env['PYTHONPATH'] = str(ROOT) + os.pathsep + str(ROOT/'pod_batch')
    env['PYTHONUTF8'] = '1'
    env['POD_ENV_FILE'] = os.devnull
    env.update(extra or {})
    return subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding='utf-8', timeout=15)

class ConfigurationTests(unittest.TestCase):
    def test_import_does_not_require_unused_feishu_credentials(self):
        run = run_isolated('import env_config; import push_to_erp; print("READY")')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('READY', run.stdout)

    def test_explicit_env_file_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            envfile = Path(folder)/'sample.env'
            envfile.write_text('\ufeff# synthetic\nERP_USER="from-file"\nERP_PASS=local-test\n', encoding='utf-8')
            run = run_isolated('import env_config,os; print(os.environ["ERP_USER"]); print(bool(os.environ.get("ERP_PASS")))',
                               {'POD_ENV_FILE':str(envfile), 'ERP_USER':'from-environment'})
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(run.stdout.splitlines(), ['from-environment','True'])

    def test_missing_feishu_config_fails_before_request(self):
        code = '''from unittest.mock import patch
import feishu_schema
with patch('urllib.request.urlopen') as request:
    try:
        feishu_schema.get_token()
    except SystemExit:
        assert not request.called
        print('MISSING_CONFIG')
    else:
        raise AssertionError('Expected a missing-config error')
'''
        run=run_isolated(code)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('MISSING_CONFIG',run.stdout)

    def test_erp_requires_explicit_endpoint(self):
        run=run_isolated('import push_to_erp; push_to_erp.need_creds()', {'ERP_USER':'test-user','ERP_PASS':'test-value'})
        self.assertNotEqual(run.returncode,0)
        self.assertIn('ERP_URL',run.stderr)
        self.assertNotIn('test-value',run.stderr)

    def test_erp_rejects_plain_http_for_remote_credentials(self):
        run=run_isolated('import push_to_erp; push_to_erp.need_creds()',
                         {'ERP_URL':'http://example.com','ERP_USER':'test-user','ERP_PASS':'test-value'})
        self.assertNotEqual(run.returncode,0)
        self.assertIn('HTTPS',run.stderr)

    def test_comfy_host_can_be_configured_without_feishu(self):
        run=run_isolated('import comfy_batch; print(comfy_batch.COMFY_HOST)', {'COMFY_HOST':'http://127.0.0.1:9999/'})
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(run.stdout.strip(),'http://127.0.0.1:9999')

    def test_erp_accepts_loopback_but_rejects_embedded_credentials(self):
        for url, valid in [('http://127.0.0.1:8000', True),
                           ('https://user:pass@example.com', False),
                           ('https://example.com?token=private', False)]:
            with self.subTest(url=url):
                run=run_isolated('import push_to_erp; push_to_erp.need_creds()',
                    {'ERP_URL':url,'ERP_USER':'test-user','ERP_PASS':'test-value'})
                self.assertEqual(run.returncode == 0, valid, run.stderr)

class WorkflowTests(unittest.TestCase):
    def test_graph_is_copied_and_output_names_are_changed(self):
        mod=importlib.import_module('comfy_batch')
        for name in ['workflow_api.json','workflow_api_gguf.json','workflow_api_fp8_backup.json']:
            graph=mod.load_workflow(str(ROOT/'pod_batch'/name))
            before=json.dumps(graph,sort_keys=True)
            job=mod.build_job(graph,'geometric mountain',123,'001','demo')
            self.assertEqual(job[mod.find_positive_node(job)]['inputs']['text'],'geometric mountain')
            self.assertEqual(json.dumps(graph,sort_keys=True),before)
            self.assertEqual(job['44']['inputs']['filename_prefix'],'png/001_demo')

    def test_dry_run_filters_completed_rows_without_network(self):
        mod=importlib.import_module('comfy_batch')
        with tempfile.TemporaryDirectory() as folder:
            table=Path(folder)/'synthetic.csv'
            table.write_text('编号,主体,状态,seed\n001,mountain,待生成,123\n002,leaf,已生成,124\n',encoding='utf-8-sig')
            output=io.StringIO()
            with patch.object(sys,'argv',['comfy_batch','--csv',str(table),'--dry-run']), patch('urllib.request.urlopen') as request, contextlib.redirect_stdout(output):
                self.assertEqual(mod.main(),0)
                request.assert_not_called()
        self.assertIn('会提交 1 条, 跳过 1 条',output.getvalue())

class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        comfy=Path(cls.temp.name)
        (comfy/'models').mkdir(); (comfy/'main.py').touch()
        cls.env=patch.dict(os.environ,{'COMFY_ROOT':str(comfy)})
        cls.env.start()
        cls.mod=importlib.import_module('compose_mockups')

    @classmethod
    def tearDownClass(cls):
        cls.env.stop(); cls.temp.cleanup()

    def test_trim_and_contain_preserve_art_aspect_ratio(self):
        from PIL import Image, ImageDraw
        art=Image.new('RGBA',(100,100))
        ImageDraw.Draw(art).rectangle((10,20,49,39),fill=(200,10,10,255))
        trimmed=self.mod.trim_alpha(art)
        self.assertEqual(trimmed.size,(40,20))
        self.assertEqual(self.mod.fit_into(trimmed,60,60).size,(60,30))
        self.assertIsNone(self.mod.trim_alpha(Image.new('RGBA',(20,20))))

    def test_print_size_alpha_and_top_anchor(self):
        from PIL import Image
        art=Image.new('RGBA',(40,20),(20,30,40,255))
        printed=self.mod.make_print_file(art,{'inches':(1,2),'anchor':'top'})
        self.assertEqual(printed.size,(300,600))
        self.assertEqual(printed.mode,'RGBA')
        self.assertEqual(printed.getchannel('A').getbbox(),(0,0,300,150))

class PrivacyTests(unittest.TestCase):
    def cutout_runner(self):
        tree=ast.parse((ROOT/'cutout_service.py').read_text(encoding='utf-8'))
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_realesrgan']
        self.assertEqual(len(nodes),1)
        scope={'os':os,'shutil':shutil,'subprocess':subprocess}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'cutout_runner','exec'),scope)
        return scope['run_realesrgan']

    def test_cutout_resolves_path_before_setting_process_directory(self):
        runner=self.cutout_runner()
        with tempfile.TemporaryDirectory() as folder:
            resolved=str(Path(folder)/'realesrgan-ncnn-vulkan.exe')
            with patch('shutil.which',return_value=resolved), patch('subprocess.run') as child:
                runner(['realesrgan-ncnn-vulkan.exe','-n','synthetic-model'])
            self.assertEqual(child.call_args.args[0][0],resolved)
            self.assertEqual(child.call_args.kwargs['cwd'],folder)

    def test_missing_upscaler_fails_without_starting_a_process(self):
        runner=self.cutout_runner()
        with patch('shutil.which',return_value=None), patch('subprocess.run') as child:
            with self.assertRaisesRegex(FileNotFoundError,'REALESRGAN_EXE'):
                runner(['missing-upscaler.exe'])
            child.assert_not_called()

    def test_erp_does_not_forward_authentication_on_redirect(self):
        code='''import urllib.request, urllib.error
import push_to_erp as erp
handler=next(h for h in erp._direct.handlers if isinstance(h,urllib.request.HTTPRedirectHandler))
request=urllib.request.Request('https://example.com/api/me',headers={'Authorization':'Basic synthetic'})
try:
    redirected=handler.redirect_request(request,None,302,'redirect',{},'https://other.example/')
except urllib.error.HTTPError:
    pass
else:
    assert redirected is None, 'Authenticated requests must not follow redirects'
'''
        run=run_isolated(code)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_optional_blank_cutout_settings_have_defaults(self):
        tree=ast.parse((ROOT/'cutout_service.py').read_text(encoding='utf-8'))
        assignments=[n for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in {'WORK_DIR','REALESRGAN_EXE'}]
        scope={'os':os,'__file__':str(ROOT/'cutout_service.py')}
        with patch.dict(os.environ,{'POD_WORK_DIR':'','REALESRGAN_EXE':''}):
            exec(compile(ast.Module(body=assignments,type_ignores=[]),'config','exec'),scope)
        self.assertEqual(scope['WORK_DIR'],str(ROOT/'work'))
        self.assertTrue(scope['REALESRGAN_EXE'])

    def test_erp_errors_do_not_disclose_response_or_credentials(self):
        code='''import io,urllib.error
from unittest.mock import patch
import push_to_erp as erp
error=urllib.error.HTTPError('https://example.com',401,'unauthorized',{},io.BytesIO(b'PRIVATE_RESPONSE_MARKER'))
with patch.object(erp._direct,'open',side_effect=error):
    try: erp.call('/api/me')
    except RuntimeError as exc:
        assert '401' in str(exc)
        assert 'PRIVATE_RESPONSE_MARKER' not in str(exc)
        assert 'test-user' not in str(exc)
        assert 'test-value' not in str(exc)
    else: raise AssertionError('Expected HTTP failure')
'''
        run=run_isolated(code,{'ERP_URL':'https://example.com','ERP_USER':'test-user','ERP_PASS':'test-value'})
        self.assertEqual(run.returncode,0,run.stderr)

    def test_erp_check_does_not_print_identity(self):
        code='''from unittest.mock import patch
import push_to_erp as erp
with patch.object(erp,'call',return_value={'username':'PRIVATE_IDENTITY_MARKER'}): erp.check()
'''
        run=run_isolated(code)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertNotIn('PRIVATE_IDENTITY_MARKER',run.stdout)

if __name__=='__main__':unittest.main()
