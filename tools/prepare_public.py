#!/usr/bin/env python3
"""Audit and optionally export an explicit allowlist. Never copy runtime data."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
    '.gitignore', '.github/workflows/checks.yml', 'README.md', 'LICENSE',
    'THIRD_PARTY_NOTICES.md', 'config.example.json', 'secrets.example.json',
    'wyze_stream.py', 'test-bridge.py', 'youtube-relay.sh', 'youtube-watchdog.py',
    'systemd/wyze-stream-bridge.service.in', 'systemd/wyze-stream-relay.service.in',
    'tools/prepare_public.py', 'tests/test_wyze_stream.py', 'tests/test_watchdog.py',
    'tests/test_public_release.py', 'docs/deployment.md',
)
PATTERNS = (
    ('private key block', re.compile(r'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----')),
    ('GitHub token', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b')),
    ('AWS access key', re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('credential-bearing RTSP URL', re.compile(r'rtsp://[^\s/]+:[^\s/@]+@')),
    ('JWT', re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b')),
)


def private_values(root):
    """Compare release bytes to known local credentials without printing any value."""
    values = set()
    paths = list(root.glob('*.local.json'))
    paths += list((root / 'bridge-state').glob('*.json'))
    config_path = root / 'config.local.json'
    if config_path.exists():
        config = json.loads(config_path.read_text())
        secret_path = root / config.get('secrets_file', 'secrets.local.json')
        if secret_path.exists():
            paths.append(secret_path)
        state_dir = root / config.get('bridge', {}).get('state_dir', 'bridge-state')
        paths.extend(state_dir.glob('*.json'))
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'secrets_file':
                    continue  # A public configuration field naming the private file.
                if isinstance(item, str) and len(item) >= 8 and any(
                    part in key.lower() for part in ('password', 'token', 'key', 'email', 'api_id', 'secret', 'enr')
                ):
                    values.add(item)
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    for path in paths:
        try:
            visit(json.loads(path.read_text()))
        except (OSError, ValueError):
            raise ValueError('Cannot audit private comparison input: ' + path.name) from None
    if config_path.exists():
        config = json.loads(config_path.read_text())
        for value in config.get('camera', {}).values():
            if isinstance(value, str) and len(value) >= 8:
                values.add(value)
    return values


def audit(files, known=()):
    failures = []
    for name, blob in files.items():
        if name not in PUBLIC_FILES:
            failures.append((name, 'not in the public allowlist'))
            continue
        try:
            text = blob.decode('utf-8')
        except UnicodeDecodeError:
            failures.append((name, 'binary content is not allowed'))
            continue
        for label, pattern in PATTERNS:
            if pattern.search(text):
                failures.append((name, label))
        if any(value in text for value in known):
            failures.append((name, 'contains a private local value'))
        if name == 'secrets.example.json':
            if any(json.loads(text).values()):
                failures.append((name, 'secret examples must be empty'))
    return failures


def collect(root, staged=False):
    if staged:
        # Inspect the entire index, including unchanged files, before publication.
        entries = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=root).decode().split('\x00')
        paths = []
        for entry in filter(None, entries):
            metadata, name = entry.split('\t', 1)
            if metadata.split()[0] not in ('100644', '100755'):
                raise ValueError('Forbidden Git file type: ' + name)
            paths.append(name)
        if not paths:
            raise ValueError('Git index is empty; stage the public allowlist first.')
        return {name: subprocess.check_output(['git', 'show', ':' + name], cwd=root)
                for name in paths if name}
    result = {}
    for name in PUBLIC_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing file or forbidden symlink: ' + name)
        result[name] = path.read_bytes()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-only', action='store_true')
    parser.add_argument('--staged', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        files = collect(ROOT, args.staged)
        failures = audit(files, private_values(ROOT))
        if failures:
            for name, reason in failures:
                print(f'BLOCKED {name}: {reason}', file=sys.stderr)
            return 1
        print(f'Audit passed for {len(files)} allowlisted text files; no known private values found.')
        if args.output:
            destination = args.output.resolve()
            if destination.exists():
                raise ValueError('Output must be a new directory.')
            destination.mkdir(parents=True)
            for name, blob in files.items():
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob)
            print('Created clean public export at ' + str(destination))
        elif not (args.audit_only or args.staged):
            parser.error('Use --audit-only, --staged, or --output PATH.')
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Public preparation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
