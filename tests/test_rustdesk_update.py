import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'rustdesk_update', Path(__file__).resolve().parents[1] /
    'tools/pi-baseline/rustdesk-auto-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


def release():
    name = 'rustdesk-1.5.3-aarch64.deb'
    return {'tag_name': '1.5.3', 'draft': False, 'prerelease': False,
            'assets': [{'name': name, 'size': 100, 'digest': 'sha256:' + 'a' * 64,
                        'browser_download_url':
                        'https://github.com/rustdesk/rustdesk/releases/download/1.5.3/' + name}]}


class RustDeskUpdateTests(unittest.TestCase):
    def test_requires_official_stable_release_with_digest(self):
        changes = [lambda r: r.update(prerelease=True),
                   lambda r: r.update(draft=True),
                   lambda r: r['assets'][0].update(digest=None),
                   lambda r: r['assets'][0].update(browser_download_url='https://example.com/package.deb'),
                   lambda r: r['assets'][0].update(size=updater.MAX_DOWNLOAD + 1)]
        for change in changes:
            data = release()
            change(data)
            with self.subTest(change=change), patch.object(
                    updater.urllib.request, 'urlopen', return_value=io.StringIO(json.dumps(data))):
                with self.assertRaises(RuntimeError):
                    updater.release_info()

    def test_accepts_expected_official_arm64_asset(self):
        with patch.object(updater.urllib.request, 'urlopen',
                          return_value=io.StringIO(json.dumps(release()))):
            version, asset = updater.release_info()
        self.assertEqual(version, '1.5.3')
        self.assertTrue(asset['name'].endswith('-aarch64.deb'))

    def test_checksum_mismatch_is_rejected_before_package_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'package.deb'
            path.write_bytes(b'synthetic package contents')
            with patch.object(updater, 'run') as command:
                with self.assertRaisesRegex(RuntimeError, 'SHA-256 mismatch'):
                    updater.verify_package(path, '1.5.3', 'sha256:' + '0' * 64)
                command.assert_not_called()

    def test_wrong_package_identity_version_or_architecture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'package.deb'
            path.write_bytes(b'synthetic package contents')
            digest = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
            for package, version, arch in [('other', '1.5.3', 'arm64'),
                                           ('rustdesk', '1.5.2', 'arm64'),
                                           ('rustdesk', '1.5.3', 'amd64')]:
                metadata = f'Package: {package}\nVersion: {version}\nArchitecture: {arch}'
                with self.subTest(metadata=metadata), patch.object(updater, 'run', return_value=metadata):
                    with self.assertRaisesRegex(RuntimeError, 'mismatch'):
                        updater.verify_package(path, '1.5.3', digest)

    def test_verified_package_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'package.deb'
            path.write_bytes(b'synthetic package contents')
            digest = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(updater, 'run', return_value=
                              'Package: rustdesk\nVersion: 1.5.3\nArchitecture: arm64'):
                updater.verify_package(path, '1.5.3', digest)


if __name__ == '__main__':
    unittest.main()
