# Deployment details

## Configuration reference

All host-specific values belong in `config.local.json`. Relative paths resolve
against the directory containing that configuration, not the caller's directory.
To choose a different configuration, put `--config PATH` before the subcommand,
or set `WYZE_STREAM_CONFIG`. Secret file names must end in `.local.json`.

| Setting | Meaning |
| --- | --- |
| `camera.id` | Exactly one Wyze Bridge camera identifier; lists are rejected |
| `camera.model` | Optional single model filter; empty disables the model filter |
| `camera.lan_ip` | Optional Gwell LAN IPv4 override; empty omits it |
| `camera.stream_name` | Exact normalized bridge RTSP name |
| `bridge.image` | Fully qualified container image; prefer a tested multi-architecture digest |
| `bridge.container_name` | Local Podman container name |
| `bridge.state_dir` | Private persistent Wyze state directory |
| `bridge.dashboard_port` / `rtsp_port` | Host loopback ports; internal ports remain 5080 / 8554 |
| `bridge.log_level` | Bridge logging level; avoid verbose logs in public reports |
| `youtube.server` | YouTube RTMPS server ending in `/live2`; TCP 443 only |
| `youtube.retry_seconds` | Delay between encoder attempts |
| `youtube.stall_seconds` | Allowed upload inactivity after initial progress |
| `youtube.startup_seconds` | Allowed time without an initial upload connection |
| `youtube.poll_seconds` | TCP-counter polling interval |
| `youtube.check_seconds` | Default local media test duration |
| `secrets_file` | Mode-600 private JSON credential file |

Defaults such as internal container ports, AAC format, authenticated loopback
binding, and passthrough encoding describe the pipeline rather than a particular
installation. They intentionally stay in code. This deployment uses rootless
Podman `pasta` networking because a prior Window Cam installation froze when
media traveled through a public relay with a different NAT backend. Revalidate
LAN reception on each new network and camera model.

## Secrets and credentials

The private secret fields are `wyze_email`, `wyze_password`, `wyze_api_id`,
`wyze_api_key`, optional `wyze_totp_key`, `viewer_password`, and `youtube_key`.
Wyze login values can be absent while a saved bridge session remains usable;
store the login values to allow fresh login when supported by upstream.
`viewer_password` is generated as random hexadecimal text. Update only YouTube:

```sh
python3 wyze_stream.py configure --youtube-only
systemctl --user restart wyze-stream-relay
```

Credentials are supplied to Podman through the process environment, and to FFmpeg
through anonymous file descriptors, avoiding credentials in command arguments.
Relay logs redact loaded credentials and authenticated RTSP URLs. Files are
still accessible to this Linux user and privileged administrators; filesystem
permissions do not protect against a compromised account. Podman inspection,
saved sessions, and generated bridge configuration can also contain credentials.
Never attach those files or raw diagnostic archives to public issues.

## Maintenance and diagnosis

```sh
systemctl --user is-enabled wyze-stream-bridge wyze-stream-relay
loginctl show-user "$USER" -p Linger
journalctl --user -u wyze-stream-bridge -u wyze-stream-relay --since '10 minutes ago'
podman ps
```

Use the configured container name for `podman logs`. Review/redact bridge logs
before sharing them. For upload diagnosis, compare FFmpeg's `bytes_acked` counters
in `ss -Otinp state established dport = :443` several seconds apart. An established
socket with fixed counters can be stalled. The watchdog uses this exact signal.
It does not inspect viewer playback or guarantee that YouTube accepted the event.

Opening a native Wyze live view interrupted a prior Window Cam installation.
Avoid competing sessions with the assigned camera and use the bridge dashboard
or YouTube to inspect it. Low native bitrate alone does not establish a stall.

During service migration, a fresh Window Cam connection required several Gwell
handshake retries and roughly four minutes before RTSP media returned. The relay
recovered automatically. Allow for this delay when checking cold starts; the
bridge's reported `streaming` state can precede actual received video bytes.

An earlier installation showed invalid NAL-unit and repeated-timestamp warnings
while video continued decoding. Watch for actual freezes or ingestion errors;
the passthrough relay does not repair source bitstreams. Pi throughput and
multiple hosts on one Wyze account remain unverified until hardware testing.

## Reboot acceptance test

First verify both enabled units and `Linger=yes`, then perform a controlled
service stop/start and check local decoding plus advancing upload counters.
During a scheduled maintenance window, reboot and check those same signals from
a fresh login, along with YouTube Studio and viewer playback. A service restart
is not a substitute for this full reboot test. This project never reboots the host
as part of installation.

## Changing settings and upgrading

For camera, image, container-name, port, or state-directory changes, stop the
services while the old configuration is still present:

```sh
systemctl --user stop wyze-stream-relay wyze-stream-bridge
# Edit config.local.json in your editor, keeping private values out of Git.
python3 wyze_stream.py validate
systemctl --user start wyze-stream-bridge wyze-stream-relay
```

To upgrade the scripts on an installed host:

```sh
systemctl --user stop wyze-stream-relay wyze-stream-bridge
git pull --ff-only
python3 wyze_stream.py validate
python3 wyze_stream.py install-services
```

The installer refreshes the generated units as well as enabling and starting
them. These commands leave ignored configuration, credentials, and session state
on the host. For rollback, stop both services, check out a previously tested
revision, and reinstall its units. Keep private backups outside the repository.

The YouTube broadcast lifecycle is configured in Studio, independently of Linux
services. Check the selected event, auto-start/auto-stop behavior, visibility,
and Stream health after recovery. Reusing an ingestion key does not itself
guarantee that an ended event will retain the same watch-page URL.

## Preparing GitHub publication

1. Run tests and the public audit on the machine holding the private deployment.
2. Review the explicit allowlist in `tools/prepare_public.py` and the staged diff.
3. Confirm `config.local.json`, `secrets.local.json`, state, traces, and credentials
   are ignored and absent from the Git index and commit history.
4. Create the public repository and push only the reviewed clean commit.

The release helper intentionally excludes archives rather than assuming they
were sanitized. Public examples use a documentation-only IP and empty credentials.
Git commit author addresses can disclose personal email: use your verified GitHub
noreply identity when preparing the initial public commit.
