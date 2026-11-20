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
                    'rtsp://' + 'viewer:example@host/camera']
        for example in examples:
            with self.subTest(example_type=example[:5]):
                self.assertTrue(public.audit({'README.md': example.encode()}))

    def test_empty_secret_example_only(self):
        self.assertFalse(public.audit({'secrets.example.json': b'{"youtube_key": ""}'}))
        self.assertTrue(public.audit({'secrets.example.json': b'{"youtube_key": "populated"}'}))

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


if __name__ == '__main__':
    unittest.main()
