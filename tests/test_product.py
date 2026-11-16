import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from viceclip.cli import main
from viceclip.onboarding import base_config, private_json, setup
from viceclip.rights import check_rights, load_config
from viceclip.scoring import select_candidates
from viceclip import service


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_selected_sources_work_without_per_clip_permission_ledger(self):
        config=base_config(self.root/'config.json')
        check_rights(config,'inbox','publish','youtube',commercial=True)
        config['sources'][0]['allow_publish']=False
        with self.assertRaises(ValueError):check_rights(config,'inbox','publish','youtube')

    def test_source_policy_requires_one_time_explicit_operator_setting(self):
        config=base_config(self.root/'config.json')
        config['source_policy']['user_responsible']=False
        with self.assertRaises(ValueError):check_rights(config,'inbox','edit')

    def test_noninteractive_setup_preserves_existing_configuration(self):
        path=self.root/'config.json'
        with contextlib.redirect_stdout(io.StringIO()):
            setup(path,non_interactive=True,accept_terms=True,no_asr=True)
        original=path.read_bytes()
        with self.assertRaises(ValueError):setup(path,non_interactive=True,accept_terms=True)
        self.assertEqual(path.read_bytes(),original)
        config=load_config(path)
        self.assertFalse(config['autopilot']['execute'])
        self.assertEqual(config['autopilot']['privacy'],'public')

    def test_custom_setup_next_commands_keep_the_custom_path(self):
        path=self.root/'another profile/config.json'
        output=io.StringIO()
        with contextlib.redirect_stdout(output):setup(path,non_interactive=True,accept_terms=True,no_asr=True)
        self.assertIn('--config',output.getvalue())
        self.assertIn(str(path),output.getvalue())

    def test_private_secret_loading_and_no_parent_permission_change(self):
        parent=self.root/'already_exists';parent.mkdir(mode=0o755)
        old=parent.stat().st_mode&0o777
        secret=parent/'env.json';private_json(secret,{'S3_ACCESS_KEY_ID':'test-key'})
        self.assertEqual(secret.stat().st_mode&0o777,0o600)
        self.assertEqual(parent.stat().st_mode&0o777,old)
        config=self.root/'config.json';private_json(config,{'secret_env_file':str(secret)})
        with patch.dict(os.environ,{},clear=True):
            load_config(config)
            self.assertEqual(os.environ['S3_ACCESS_KEY_ID'],'test-key')

    def test_stop_and_logs_still_work_when_config_is_broken(self):
        path=self.root/'config.json';path.write_text('{broken')
        with patch('viceclip.service.stop',return_value={'running':False}) as stop,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['autopilot','stop','--config',str(path)]),0)
        stop.assert_called_once_with(str(path))
        with patch('viceclip.service.logs',return_value='safe log'),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['autopilot','logs','--config',str(path)]),0)

    def test_service_stop_keeps_record_until_current_render_finishes(self):
        config=self.root/'config.json'
        config.write_text('{}')
        _,directory,record,_=service.paths(config)
        directory.mkdir()
        record.write_text(json.dumps({'pid':23456,'provider':'process'}))
        with patch('viceclip.service._owned_process',return_value=True),patch('viceclip.service.time.sleep'),patch('viceclip.service.os.kill'):
            result=service.stop(config)
        self.assertTrue(result['stopping'])
        self.assertTrue(record.exists())

    def test_action_without_commentary_can_pass_normal_quality_threshold(self):
        analysis={'duration':30,'audio_dbfs':[-9]*30,'motion':[28]*30}
        clip=select_candidates(analysis,[],top=1,min_duration=10,max_duration=20)[0]
        self.assertGreaterEqual(clip['score'],45)
        self.assertEqual(clip['evidence']['mode'],'audio_motion')
        quiet=select_candidates({'duration':30,'audio_dbfs':[-70]*30,'motion':[0]*30},[],top=1)[0]
        self.assertLess(quiet['score'],45)

    def test_promo_cli_saves_real_customer_settings(self):
        path=self.root/'config.json'
        private_json(path,base_config(path))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code=main(['promo','--config',str(path),'--text','Мой продукт · Ссылка в профиле',
                       '--url','https://example.test/?ref=buyer','--position','bottom'])
        self.assertEqual(code,0)
        receipt=json.loads(out.getvalue())
        self.assertEqual(receipt['applies_to'],'new_renders_only')
        campaign=load_config(path)['campaign']
        self.assertTrue(campaign['sponsored'])
        self.assertEqual(campaign['platforms'],['youtube'])
        self.assertIn('ref=buyer',campaign['url'])

    def test_promo_cli_wizard_has_three_actual_inputs(self):
        path=self.root/'config.json'
        private_json(path,base_config(path))
        with patch('builtins.input',side_effect=['Продукт · Ссылка в профиле','https://example.test/offer','top']) as inputs,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['promo','--config',str(path)]),0)
        self.assertEqual(inputs.call_count,3)
        self.assertEqual(load_config(path)['campaign']['position'],'top')

    def test_promo_show_is_read_only_and_off_keeps_profile(self):
        path=self.root/'config.json'
        private_json(path,base_config(path))
        original=path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['promo','--show','--config',str(path)]),0)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(main(['promo','--off','--config',str(path)]),0)
        config=load_config(path)
        self.assertFalse(config['campaign']['enabled'])
        self.assertEqual(config['sources'][0]['id'],'inbox')


if __name__=='__main__':unittest.main()
