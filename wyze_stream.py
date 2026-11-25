#!/usr/bin/env python3
"""One camera, one host, one YouTube broadcast. Python standard library only."""

import argparse
import fcntl
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / 'config.local.json'
SECRET_FIELDS = {
    'wyze_email', 'wyze_password', 'wyze_api_id', 'wyze_api_key',
    'wyze_totp_key', 'viewer_password', 'youtube_key', 'camera_rtsp_url',
}
ENV_FIELDS = {
    'wyze_email': 'WYZE_EMAIL', 'wyze_password': 'WYZE_PASSWORD',
    'wyze_api_id': 'WYZE_API_ID', 'wyze_api_key': 'WYZE_API_KEY',
    'wyze_totp_key': 'WYZE_TOTP_KEY',
}


class ConfigError(ValueError):
    pass


class StopRequested(BaseException):
    pass


def private_write(path, data):
    """Atomically replace a private JSON file without a world-readable window."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, indent=2)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, private=False):
    try:
        if private:
            mode = path.stat()
            if mode.st_uid != os.getuid() or mode.st_mode & 0o077:
                raise ConfigError('Secrets must be owned by this user with mode 600.')
        result = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f'Cannot read {path.name}; create it with configure.') from error
    if not isinstance(result, dict):
        raise ConfigError(f'{path.name} must contain a JSON object.')
    return result


def validate_config(config):
    schema = json.loads((ROOT / 'config.example.json').read_text())
    if set(config) != set(schema):
        raise ConfigError('Configuration sections must match config.example.json.')
    for section in ('camera', 'bridge', 'youtube'):
        if not isinstance(config[section], dict) or set(config[section]) != set(schema[section]):
            raise ConfigError(f'Configuration keys in {section} must match the example.')
        for key, value in config[section].items():
            expected = schema[section][key]
            if type(value) is not type(expected):
                raise ConfigError(f'{section}.{key} has the wrong type.')
            if isinstance(value, str) and any(c in value for c in '\n\r\x00'):
                raise ConfigError(f'{section}.{key} must be on one line.')
    camera, bridge, youtube = (config[s] for s in ('camera', 'bridge', 'youtube'))
    if not re.fullmatch(r'[A-Za-z0-9_:-]{1,128}', camera['id']) or 'REPLACE' in camera['id']:
        raise ConfigError('Set camera.id to exactly one real camera ID; lists are not allowed.')
    if not re.fullmatch(r'[a-z0-9_]{1,128}', camera['stream_name']):
        raise ConfigError('camera.stream_name must be the normalized bridge name (a-z, 0-9, _).')
    if camera['model'] and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', camera['model']):
        raise ConfigError('camera.model must be a single model, or empty.')
    if camera['lan_ip']:
        try:
            ipaddress.IPv4Address(camera['lan_ip'])
        except ipaddress.AddressValueError as error:
            raise ConfigError('camera.lan_ip must be an IPv4 address, or empty.') from error
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', bridge['container_name']):
        raise ConfigError('Invalid bridge.container_name.')
    # Fully qualified image reference, never interpreted by a shell.
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9./_:@-]{1,255}', bridge['image']):
        raise ConfigError('Invalid bridge.image.')
    for key in ('dashboard_port', 'rtsp_port'):
        if not 1024 <= bridge[key] <= 65535:
            raise ConfigError(f'bridge.{key} must be between 1024 and 65535.')
    if bridge['dashboard_port'] == bridge['rtsp_port']:
        raise ConfigError('Dashboard and RTSP ports must differ.')
    if not bridge['state_dir'] or bridge['log_level'] not in ('trace', 'debug', 'info', 'warn', 'error'):
        raise ConfigError('Invalid bridge state directory or log level.')
    if not re.fullmatch(r'rtmps://(?:[a-zA-Z0-9-]+\.)+youtube\.com(?::443)?/live2', youtube['server']):
        raise ConfigError('youtube.server must be a YouTube RTMPS URL ending in /live2.')
    for key in ('retry_seconds', 'stall_seconds', 'startup_seconds', 'poll_seconds', 'check_seconds'):
        if not 1 <= youtube[key] <= 86400:
            raise ConfigError(f'youtube.{key} must be between 1 and 86400.')
    if min(youtube['stall_seconds'], youtube['startup_seconds']) < youtube['poll_seconds']:
        raise ConfigError('Watchdog timeouts must be at least poll_seconds.')
    if not isinstance(config['secrets_file'], str) or not config['secrets_file'].endswith('.local.json'):
        raise ConfigError('secrets_file must end in .local.json so Git ignores it.')
    if any(c in config['secrets_file'] for c in '\n\r\x00'):
        raise ConfigError('Invalid secrets_file path.')
    return config


def load_config(path):
    return validate_config(read_json(path))


def relative_path(config_path, value):
    return (config_path.parent / value).resolve()


def load_secrets(config, config_path):
    data = read_json(relative_path(config_path, config['secrets_file']), private=True)
    if set(data) - SECRET_FIELDS:
        raise ConfigError('Unrecognized secrets fields; see secrets.example.json.')
    for value in data.values():
        if not isinstance(value, str) or any(c in value for c in '\n\r\x00'):
            raise ConfigError('Secrets must be single-line strings.')
    password = data.get('viewer_password', '')
    if not re.fullmatch(r'[a-fA-F0-9]{32,128}', password):
        raise ConfigError('viewer_password must contain 32–128 hexadecimal characters; run configure.')
    key = data.get('youtube_key', '')
    if key and not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', key):
        raise ConfigError('Invalid YouTube stream key format.')
    if data.get('camera_rtsp_url'):
        validate_camera_url(data['camera_rtsp_url'])
    return data


def validate_camera_url(url):
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in ('rtsp', 'rtsps') and parsed.hostname
                 and parsed.path and not parsed.fragment
                 and not any(c.isspace() or c in "'\\" for c in url))
        # Accessing port also validates its syntax and range.
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError('camera_rtsp_url must be an RTSP(S) URL; percent-encode spaces, quotes, and backslashes.')
    return url


def camera_source(config, data):
    if data.get('camera_rtsp_url'):
        return validate_camera_url(data['camera_rtsp_url'])
    return ("rtsp://viewer:" + data['viewer_password'] +
            f"@127.0.0.1:{config['bridge']['rtsp_port']}/{config['camera']['stream_name']}")


def require_youtube(data):
    if not data.get('youtube_key'):
        raise ConfigError('YouTube key missing. Run: python3 wyze_stream.py configure --youtube-only')


def bridge_environment(config, data):
    camera = config['camera']
    env = {name: data[key] for key, name in ENV_FIELDS.items() if data.get(key)}
    env.update({
        'BRIDGE_AUTH': 'true', 'BRIDGE_USERNAME': 'viewer',
        'BRIDGE_PASSWORD': data['viewer_password'],
        'STREAM_AUTH': 'viewer:' + data['viewer_password'],
        'GWELL_ENABLED': 'true', 'FILTER_MACS': camera['id'],
        # Upstream combines allow selectors with OR, so a model selector would
        # also admit every other camera of that model on the account.
        'FILTER_MODELS': '',
        'SNAPSHOT_INTERVAL': '0', 'RECORD_ALL': 'false', 'AUDIO': 'false',
        'LOG_LEVEL': config['bridge']['log_level'],
    })
    if camera['lan_ip']:
        env['GWELL_LAN_IPS'] = camera['id'] + '=' + camera['lan_ip']
    return env


def bridge_command(config, config_path, env):
    bridge = config['bridge']
    command = ['podman', 'run', '--detach', '--rm', '--name', bridge['container_name'],
               '--network=pasta', '--stop-timeout=15', '--label', 'io.wyze-stream.managed=true']
    for key, internal in [('dashboard_port', 5080), ('rtsp_port', 8554)]:
        command.extend(['--publish', f'127.0.0.1:{bridge[key]}:{internal}'])
    command.extend(['--volume', f'{relative_path(config_path, bridge["state_dir"])}:/config'])
    for key in env:
        command.extend(['--env', key])
    return [*command, bridge['image']]


def require_tools(*names):
    missing = [name for name in names if not shutil.which(name)]
    if missing:
        raise ConfigError('Missing required tools: ' + ', '.join(missing))


def podman(*args, **kwargs):
    # Capture stderr too: never expose container environment or credential-bearing URLs.
    return subprocess.run(['podman', *args], capture_output=True, text=True, timeout=60, **kwargs)


def bridge(config, config_path, data, supervise=False):
    if data.get('camera_rtsp_url'):
        raise ConfigError('Native RTSP(S) is selected; start the relay directly or configure --bridged first.')
    require_tools('podman', 'pasta')
    name = config['bridge']['container_name']
    state = relative_path(config_path, config['bridge']['state_dir'])
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    state.chmod(0o700)
    status = podman('container', 'exists', name)
    if status.returncode not in (0, 1):
        raise ConfigError('Cannot access rootless Podman; run as the deployment user.')
    if status.returncode == 0:
        inspected = podman('inspect', name)
        if inspected.returncode:
            raise ConfigError('Cannot inspect configured container.')
        info = json.loads(inspected.stdout)[0]
        actual_env = dict(item.split('=', 1) for item in info['Config']['Env'] if '=' in item)
        if actual_env.get('FILTER_MACS') != config['camera']['id']:
            raise ConfigError('Existing container selects another camera; stop it before changing configuration.')
        if actual_env.get('BRIDGE_PASSWORD') != data['viewer_password']:
            raise ConfigError('Existing container credentials differ; stop it before changing configuration.')
        if not info['State']['Running']:
            if podman('rm', name).returncode:
                raise ConfigError('Cannot remove stopped deployment container.')
            status.returncode = 1
    if status.returncode == 1:
        saved_auth = False
        try:
            saved_auth = bool(json.loads((state / 'wyze-bridge.state.json').read_text()).get('auth', {}).get('access_token'))
        except (OSError, ValueError, AttributeError):
            pass
        if not saved_auth and not all(data.get(key) for key in list(ENV_FIELDS)[:4]):
            raise ConfigError('No saved Wyze session or complete login. Run configure.')
        env = bridge_environment(config, data)
        result = subprocess.run(bridge_command(config, config_path, env),
                                env={**os.environ, **env}, capture_output=True, text=True)
        if result.returncode:
            raise ConfigError('Bridge launch failed; check image availability, ports, and pasta support.')
    print('Camera bridge is running.', flush=True)
    if supervise:
        # Recreate the container after exits, including OOMs, via systemd Restart=always.
        result = subprocess.run(['podman', 'wait', name], capture_output=True, text=True)
        print('Camera bridge exited; supervisor will restart it.', flush=True)
        return result.returncode or 1
    return 0


def stop_bridge(config):
    name = config['bridge']['container_name']
    result = podman('container', 'exists', name)
    if result.returncode == 1:
        return 0
    if result.returncode:
        return 1
    return podman('stop', '--time', '15', name).returncode


def ffmpeg_command(config, source_fd, key_fd, check=False, seconds=None):
    command = [
        'ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'warning', '-stats', '-stats_period', '30',
        '-protocol_whitelist', 'pipe,file,tcp,tls,rtp,rtsp,rtsps,udp', '-thread_queue_size', '128',
        '-f', 'concat', '-safe', '0', '-i', f'/dev/fd/{source_fd}',
        '-thread_queue_size', '64', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
        '-map', '0:v:0', '-map', '1:a:0', '-shortest', '-c:v', 'copy',
        '-c:a', 'aac', '-b:a', '128k', '-flvflags', 'no_duration_filesize',
    ]
    if check:
        command.extend(['-t', str(seconds or config['youtube']['check_seconds']), '-f', 'flv', '-y', '/dev/null'])
    else:
        command.extend(['-fpre', f'/dev/fd/{key_fd}', '-rtmp_app', 'live2', '-tls_verify', '1',
                        '-ca_file', '/etc/ssl/certs/ca-certificates.crt', '-f', 'flv', config['youtube']['server']])
    return command


def redact(line, data):
    for value in sorted((v for v in data.values() if v), key=len, reverse=True):
        line = line.replace(value, '[redacted]')
    return re.sub(r'(rtsps?://)[^\s/@]+:[^\s/@]+@', r'\1[redacted]@', line)


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def encode(config, data, check=False, seconds=None, watchdog=None):
    source = ("ffconcat version 1.0\nfile '" + camera_source(config, data) + "'\n"
              'option rtsp_transport tcp\noption timeout 15000000\n')
    # Anonymous file descriptors keep credentials out of argv and named files.
    with tempfile.TemporaryFile() as source_file, tempfile.TemporaryFile() as key_file:
        source_file.write(source.encode())
        source_file.seek(0)
        key_file.write(('rtmp_playpath=' + data.get('youtube_key', '') + '\n').encode())
        key_file.seek(0)
        process = subprocess.Popen(ffmpeg_command(config, source_file.fileno(), key_file.fileno(), check, seconds),
                                   pass_fds=(source_file.fileno(), key_file.fileno()),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            for line in process.stderr:
                print(redact(line.rstrip(), data), file=sys.stderr, flush=True)
                if watchdog is not None and watchdog.poll() is not None:
                    raise ConfigError('Upload watchdog exited; restarting relay.')
            return process.wait()
        finally:
            process.stderr.close()
            stop_process(process)


def relay(config, config_path, data, check=False, seconds=None):
    require_tools('ffmpeg', 'ss')
    if not check:
        require_youtube(data)
    state = relative_path(config_path, config['bridge']['state_dir'])
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (state / 'relay.lock').open('a') as lock:
        if not check:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ConfigError('A relay already owns this deployment; refusing a duplicate upload.') from error
        if check:
            return encode(config, data, check=True, seconds=seconds)
        youtube = config['youtube']
        watchdog = subprocess.Popen([
            sys.executable, str(ROOT / 'youtube-watchdog.py'), str(os.getpid()),
            '--interval', str(youtube['poll_seconds']), '--stall', str(youtube['stall_seconds']),
            '--startup', str(youtube['startup_seconds']),
        ])
        try:
            while True:
                if watchdog.poll() is not None:
                    raise ConfigError('Upload watchdog exited; restarting relay.')
                encode(config, data, watchdog=watchdog)
                print(f"Encoder exited; reconnecting in {youtube['retry_seconds']} seconds.", flush=True)
                time.sleep(youtube['retry_seconds'])
        finally:
            stop_process(watchdog)


def configure(config_path, youtube_only=False, native_rtsp=False, bridged=False):
    if not sys.stdin.isatty():
        raise ConfigError('Run configure in your own terminal; secret input is hidden.')
    existing = config_path.exists()
    if existing:
        config = load_config(config_path)
    else:
        if youtube_only:
            raise ConfigError('Create the deployment configuration first.')
        config = read_json(ROOT / 'config.example.json')
    secret_path = relative_path(config_path, config['secrets_file'])
    data = read_json(secret_path, private=True) if secret_path.exists() else {}
    direct = native_rtsp or (bool(data.get('camera_rtsp_url')) and not bridged)
    if not youtube_only and direct:
        value = getpass.getpass('Camera RTSP(S) URL including credentials (hidden; blank keeps existing) > ').strip()
        url = value or data.get('camera_rtsp_url', '')
        data['camera_rtsp_url'] = validate_camera_url(url)
        if not existing:
            # Unused bridge fields retain a valid shape for later migration.
            config['camera'] = {'id': 'NATIVE_RTSP', 'stream_name': 'native_camera',
                                'model': '', 'lan_ip': ''}
    elif not youtube_only and (not existing or bridged):
        placeholder = config['camera']['id'] == 'NATIVE_RTSP'
        for key, prompt in [('id', 'Camera ID (exactly one)'), ('stream_name', 'Normalized RTSP camera name'),
                            ('model', 'Camera model (optional metadata)'), ('lan_ip', 'Camera LAN IPv4 (blank if unnecessary)')]:
            previous = config['camera'][key] if existing else ''
            if placeholder:
                previous = ''
            config['camera'][key] = input(prompt + ' (blank keeps existing, if set) > ').strip() or previous
        data.pop('camera_rtsp_url', None)
    validate_config(config)
    if not data.get('viewer_password'):
        data['viewer_password'] = secrets.token_hex(16)
    if not youtube_only and not direct:
        print('Enter Wyze login credentials. Leave an existing value blank to keep it.')
        for field, label in [('wyze_email', 'Wyze email'), ('wyze_password', 'Wyze password'),
                             ('wyze_api_id', 'Wyze Developer API ID'), ('wyze_api_key', 'Wyze Developer API key'),
                             ('wyze_totp_key', 'Authenticator secret (optional)')]:
            value = getpass.getpass(label + ' (hidden) > ')
            if value:
                data[field] = value
    key = getpass.getpass('YouTube stream key (hidden; blank keeps existing) > ')
    if key:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', key):
            raise ConfigError('Invalid stream key format; nothing saved.')
        data['youtube_key'] = key
    private_write(secret_path, data)
    private_write(config_path, config)
    load_secrets(config, config_path)
    print('Private configuration saved with mode 600. No secrets were printed.')


def unit_quote(value):
    value = str(value)
    if any(c in value for c in '\n\r\x00'):
        raise ConfigError('Service paths must be single-line strings.')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def render_units(config_path, direct=False):
    replacements = {
        # WorkingDirectory is a path directive, not an Exec command: quotes are literal.
        '@WORKDIR@': str(ROOT).replace('%', '%%'),
        '@COMMAND@': ' '.join(unit_quote(v) for v in
                             [sys.executable, ROOT / 'wyze_stream.py', '--config', config_path]),
    }
    result = {}
    for template in sorted((ROOT / 'systemd').glob('*.service.in')):
        text = template.read_text()
        for old, new in replacements.items():
            text = text.replace(old, new)
        if direct and template.name == 'wyze-stream-relay.service.in':
            text = text.replace('Wants=wyze-stream-bridge.service\n', '')
            text = text.replace('After=wyze-stream-bridge.service\n', '')
        result[template.name.removesuffix('.in')] = text
    return result


def install_services(config, config_path, data, no_start=False):
    direct = bool(data.get('camera_rtsp_url'))
    require_tools('ffmpeg', 'ss', 'systemctl', 'loginctl')
    if not direct:
        require_tools('podman', 'pasta')
    require_youtube(data)
    directory = Path.home() / '.config/systemd/user'
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in render_units(config_path, direct=direct).items():
        (directory / name).write_text(text)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['loginctl', 'enable-linger', getpass.getuser()], check=True)
    command = ['systemctl', '--user', 'enable']
    if not no_start:
        command.append('--now')
    units = ['wyze-stream-relay.service']
    if direct:
        stop = [] if no_start else ['--now']
        subprocess.run(['systemctl', '--user', 'disable', *stop, 'wyze-stream-bridge.service'], check=True)
    else:
        units.insert(0, 'wyze-stream-bridge.service')
    subprocess.run([*command, *units], check=True)
    print('Reboot services installed and enabled; user lingering is enabled.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('WYZE_STREAM_CONFIG', DEFAULT_CONFIG)))
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('configure', help='Create private configuration with hidden secret prompts')
    mode = setup.add_mutually_exclusive_group()
    mode.add_argument('--youtube-only', action='store_true')
    mode.add_argument('--native-rtsp', action='store_true', help='Configure a direct RTSP(S) camera URL privately')
    mode.add_argument('--bridged', action='store_true', help='Configure Wyze Bridge and clear the native camera URL')
    commands.add_parser('validate', help='Validate configuration and private credentials without printing them')
    run_bridge = commands.add_parser('bridge')
    run_bridge.add_argument('--supervise', action='store_true')
    commands.add_parser('stop-bridge')
    run_relay = commands.add_parser('relay')
    run_relay.add_argument('--check', action='store_true', help='Test locally, without uploading to YouTube')
    run_relay.add_argument('--seconds', type=int, help='Local check duration, 1–3600 seconds')
    install = commands.add_parser('install-services')
    install.add_argument('--no-start', action='store_true')
    args = parser.parse_args()
    config_path = args.config.expanduser().resolve()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(StopRequested()))
    try:
        if args.command == 'configure':
            configure(config_path, args.youtube_only, args.native_rtsp, args.bridged)
            return
        config = load_config(config_path)
        if args.command == 'stop-bridge':
            raise SystemExit(stop_bridge(config))
        data = load_secrets(config, config_path)
        if args.command == 'validate':
            require_youtube(data)
            source = 'native RTSP(S)' if data.get('camera_rtsp_url') else 'Wyze Bridge'
            print(f'Configuration and secrets are valid. Exactly one camera is selected via {source}.')
        elif args.command == 'bridge':
            raise SystemExit(bridge(config, config_path, data, args.supervise))
        elif args.command == 'relay':
            if args.seconds is not None and (not args.check or not 1 <= args.seconds <= 3600):
                raise ConfigError('--seconds requires --check and a duration from 1 to 3600.')
            raise SystemExit(relay(config, config_path, data, args.check, args.seconds))
        elif args.command == 'install-services':
            install_services(config, config_path, data, args.no_start)
    except (KeyboardInterrupt, StopRequested):
        return
    except (ConfigError, OSError, subprocess.SubprocessError) as error:
        # Subprocess errors can embed argv. Those are designed to contain no secrets.
        print(f'wyze-stream: {error}', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
