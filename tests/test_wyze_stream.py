import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import wyze_stream as ws


def configuration():
    data = json.loads((ws.ROOT / 'config.example.json').read_text())
    data['camera'].update(id='TEST_CAMERA_1234', stream_name='test_camera', lan_ip='192.0.2.10')
    return data


def credentials():
    return {'viewer_password': 'ab' * 16, 'youtube_key': 'test_' + 'x' * 24,
            'wyze_password': 'a-private-test-password', 'wyze_api_key': 'a-private-test-api-key'}


class ConfigurationTests(unittest.TestCase):
    def test_exactly_one_camera_is_selected(self):
        cfg, data = configuration(), credentials()
        env = ws.bridge_environment(cfg, data)
        self.assertEqual(env['FILTER_MACS'], 'TEST_CAMERA_1234')
        self.assertEqual(env['GWELL_LAN_IPS'], 'TEST_CAMERA_1234=192.0.2.10')
        self.assertEqual(env['AUDIO'], 'false')
        for bad in ['FIRST,SECOND', 'FIRST\nSECOND', 'REPLACE_WITH_CAMERA_ID', '']:
            cfg['camera']['id'] = bad
            with self.subTest(bad=bad), self.assertRaises(ws.ConfigError):
                ws.validate_config(cfg)

    def test_rejects_unsafe_source_and_server_settings(self):
        for section, key, value in [
            ('camera', 'stream_name', "camera'\nfile /etc/passwd"),
            ('youtube', 'server', 'rtmps://youtube.com.evil.example/live2'),
            ('youtube', 'server', 'rtmp://a.rtmp.youtube.com/live2'),
            ('bridge', 'rtsp_port', True), ('bridge', 'dashboard_port', 8554),
            ('youtube', 'stall_seconds', 0), ('camera', 'lan_ip', 'invalid'),
        ]:
            cfg = configuration()
            cfg[section][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ws.ConfigError):
                ws.validate_config(cfg)

    def test_optional_model_and_lan_ip(self):
        cfg = configuration()
        cfg['camera'].update(model='', lan_ip='')
        ws.validate_config(cfg)
        env = ws.bridge_environment(cfg, credentials())
        self.assertEqual(env['FILTER_MODELS'], '')
        self.assertNotIn('GWELL_LAN_IPS', env)

    def test_credentials_never_appear_in_process_arguments(self):
        cfg, data = configuration(), credentials()
        command = ws.bridge_command(cfg, Path('/tmp/example/config.local.json'), ws.bridge_environment(cfg, data))
        self.assertIn('127.0.0.1:5080:5080', command)
        self.assertIn('127.0.0.1:8554:8554', command)
        self.assertIn('--network=pasta', command)
        command += ws.ffmpeg_command(cfg, 10, 11)
        for value in data.values():
            self.assertNotIn(value, ' '.join(command))
        self.assertIn('-tls_verify', command)
        self.assertIn('/dev/fd/10', command)
        self.assertIn('/dev/fd/11', command)

    def test_secret_file_permissions_and_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.local.json'
            path = Path(directory) / 'secrets.local.json'
            ws.private_write(path, credentials())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(ws.load_secrets(configuration(), config_path), credentials())
            path.chmod(0o644)
            with self.assertRaises(ws.ConfigError):
                ws.load_secrets(configuration(), config_path)
            ws.private_write(path, credentials())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(Path(directory).glob('secrets.local.json.*'))), 0)

    def test_redacts_actual_secret_values_and_rtsp_credentials(self):
        data = credentials()
        line = 'Failure ' + 'rtsp://' + 'viewer:' + data['viewer_password'] + '@localhost/camera ' + data['youtube_key']
        result = ws.redact(line, data)
        for value in data.values():
            self.assertNotIn(value, result)
        self.assertIn('[redacted]', result)

    def test_local_check_cannot_upload(self):
        command = ws.ffmpeg_command(configuration(), 10, 11, check=True, seconds=3)
        self.assertEqual(command[-1], '/dev/null')
        self.assertNotIn('-fpre', command)
        self.assertFalse(any('youtube.com' in arg for arg in command))
        self.assertEqual(command[command.index('-t') + 1], '3')

    def test_bridge_refuses_to_adopt_another_camera(self):
        cfg = configuration()
        with tempfile.TemporaryDirectory() as directory:
            cfg['bridge']['state_dir'] = str(Path(directory) / 'state')
            info = [{'Config': {'Env': ['FILTER_MACS=OTHER_CAMERA', 'BRIDGE_PASSWORD=' + credentials()['viewer_password']]},
                     'State': {'Running': True}}]
            results = [subprocess.CompletedProcess([], 0, '', ''),
                       subprocess.CompletedProcess([], 0, json.dumps(info), '')]
            with patch.object(ws, 'require_tools'), patch.object(ws, 'podman', side_effect=results):
                with self.assertRaisesRegex(ws.ConfigError, 'another camera'):
                    ws.bridge(cfg, Path(directory) / 'config.local.json', credentials())

    def test_service_paths_are_quoted_and_configuration_is_external(self):
        rendered = ws.render_units(Path('/tmp/a path/config.local.json'))
        for text in rendered.values():
            self.assertIn('"/tmp/a path/config.local.json"', text)
            self.assertIn('Restart=always', text)
            self.assertIn('UMask=0077', text)
            self.assertNotIn('@COMMAND@', text)
            self.assertIn('WorkingDirectory=' + str(ws.ROOT), text)
            self.assertNotIn('youtube_key', text)
        self.assertIn('stop-bridge', rendered['wyze-stream-bridge.service'])
        self.assertIn('KillMode=control-group', rendered['wyze-stream-relay.service'])

    def test_invalid_secret_file_extension_is_rejected(self):
        cfg = configuration()
        cfg['secrets_file'] = 'public-credentials.json'
        with self.assertRaises(ws.ConfigError):
            ws.validate_config(cfg)


if __name__ == '__main__':
    unittest.main()
