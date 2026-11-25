import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('prepare_public', Path(__file__).resolve().parents[1] / 'tools/prepare_public.py')
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)


class ReleaseTests(unittest.TestCase):
    def test_allowlist_excludes_runtime_and_private_directories(self):
        for name in ('secrets.local.json', 'config.local.json', 'bridge-state/wyze-bridge.state.json',
                     '.aws/credentials', '.git/config', 'trace.zip'):
            with self.subTest(name=name):
                self.assertTrue(public.audit({name: b'private data'}))

    def test_detects_known_local_secrets_without_printing_them(self):
        secret = 'synthetic-sensitive-value'
        result = public.audit({'README.md': ('accidentally included ' + secret).encode()}, {secret})
        self.assertTrue(result)
        self.assertNotIn(secret, str(result))

    def test_detects_common_tokens_and_private_keys(self):
        examples = ['gh' + 'p_' + 'x' * 36,
                    '-----BEGIN ' + 'PRIVATE KEY-----',
                    'AK' + 'IA' + 'A' * 16,
                    'rtsp://' + 'viewer:example@host/camera',
                    'RTSPS://' + 'viewer:example@host/camera']
        for example in examples:
            with self.subTest(example_type=example[:5]):
                self.assertTrue(public.audit({'README.md': example.encode()}))

    def test_empty_secret_example_only(self):
        self.assertFalse(public.audit({'secrets.example.json': b'{"youtube_key": ""}'}))
        self.assertTrue(public.audit({'secrets.example.json': b'{"youtube_key": "populated"}'}))

    def test_native_url_components_cannot_leak_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            password = 'synthetic-native-password!'
            url = 'rtsps://' + 'synthetic-user:synthetic-native-password%21@192.0.2.20:322/stream0'
            (root / 'secrets.local.json').write_text(json.dumps({'camera_rtsp_url': url}))
            known = public.private_values(root)
            self.assertIn(password, known)
            self.assertIn('synthetic-native-password%21', known)
            for value in (url, password, 'synthetic-user', '192.0.2.20'):
                self.assertTrue(public.audit({'README.md': value.encode()}, known))

    def test_dotenv_credentials_are_audited_as_data_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text(
                '# ignored comment\nexport WYZE_API_KEY="synthetic-dotenv-secret"\n'
                'WYZE-PASS=synthetic-hyphen-label-secret\n'
                f'SHELL_TEXT=$(touch {root / "should-not-exist"})\n')
            known = public.private_values(root)
            self.assertIn('synthetic-dotenv-secret', known)
            self.assertIn('synthetic-hyphen-label-secret', known)
            self.assertFalse((root / 'should-not-exist').exists())
            result = public.audit({'README.md': b'synthetic-dotenv-secret'}, known)
            self.assertTrue(result)
            self.assertNotIn('synthetic-dotenv-secret', str(result))

    def test_private_file_reference_is_not_itself_a_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.local.json').write_text(json.dumps({
                'secrets_file': 'secrets.local.json', 'bridge': {'state_dir': 'custom-state'},
            }))
            (root / 'secrets.local.json').write_text(json.dumps({'youtube_key': 'synthetic-private-key'}))
            (root / 'custom-state').mkdir()
            (root / 'custom-state/session.json').write_text(json.dumps({'auth': {'access_token': 'synthetic-private-token'}}))
            known = public.private_values(root)
            self.assertNotIn('secrets.local.json', known)
            self.assertIn('synthetic-private-key', known)
            self.assertIn('synthetic-private-token', known)

    def test_all_profile_camera_settings_and_custom_states_are_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('front', 'rear'):
                state = root / 'state' / name
                state.mkdir(parents=True)
                (root / f'config.{name}.local.json').write_text(json.dumps({
                    'camera': {'id': f'synthetic-camera-{name}'},
                    'secrets_file': f'secrets.{name}.local.json',
                    'bridge': {'state_dir': f'state/{name}'},
                }))
                (root / f'secrets.{name}.local.json').write_text(json.dumps({'youtube_key': f'synthetic-key-{name}'}))
                (state / 'session.json').write_text(json.dumps({'auth': {'access_token': f'synthetic-token-{name}'}}))
            known = public.private_values(root)
            for name in ('front', 'rear'):
                for prefix in ('synthetic-camera-', 'synthetic-key-', 'synthetic-token-'):
                    self.assertIn(prefix + name, known)


if __name__ == '__main__':
    unittest.main()
