# Deployment details

## Supported streaming modes

| Mode | Source | Services |
| --- | --- | --- |
| Native v4 RTSPS / native RTSP | Private `camera_rtsp_url` → FFmpeg → YouTube | Relay enabled; bridge stopped/disabled |
| Legacy / Window Cam bridge | Selected Wyze camera → Wyze Bridge RTSP → FFmpeg → YouTube | Bridge and relay enabled |

Both paths use TCP input transport, copy H.264 video, and add silent stereo AAC
audio. The host never uploads the camera microphone. Source H.265/HEVC is not
supported by this H.264/FLV relay. The YouTube upload uses verified TLS.

Native v4 RTSPS was exercised on Ubuntu with camera firmware 4.52.17.27,
2560×1440 at 20 fps, local captures/full decoding, local FLV checks, and advancing
upload bytes on the same FFmpeg process. The Window Cam bridge was exercised at
1920×1080, including a Pi 5 with 8 GB RAM, 64-bit Raspberry Pi OS based on
Debian 13, rootless Podman 5.4.2, and `pasta`. Native 1440p on a Pi, every firmware
version, long-term uptime, and a native-mode reboot have not been hardware-verified.
Use the acceptance procedure below for each host.

## Configuration reference

Host settings live in `config.local.json`; secrets live in mode-600
`secrets.local.json`. Relative paths resolve against the configuration directory.
Use `--config PATH` before a subcommand, or set `WYZE_STREAM_CONFIG`, to select
another deployment. Secret file names must end in `.local.json`.

| Setting | Meaning |
| --- | --- |
| `camera.id` | One exact Wyze Bridge camera ID; unused in native mode |
| `camera.model` | Optional bridge model metadata, never an allow selector |
| `camera.lan_ip` | Optional Gwell LAN IPv4 override; empty omits it |
| `camera.stream_name` | Exact normalized bridge RTSP name; unused in native mode |
| `bridge.image` | Fully qualified pinned container image |
| `bridge.container_name` | Local Podman container name |
| `bridge.state_dir` | Private bridge state and relay-lock directory; needed in both modes |
| `bridge.dashboard_port` / `rtsp_port` | Loopback host ports; unused for native camera input |
| `bridge.log_level` | Bridge logging verbosity |
| `youtube.server` | YouTube RTMPS destination ending in `/live2`, TCP 443 only |
| `youtube.retry_seconds` | Delay between encoder attempts |
| `youtube.stall_seconds` / `startup_seconds` | Watchdog upload inactivity/startup limits |
| `youtube.poll_seconds` | Upload-counter polling interval |
| `youtube.check_seconds` | Default local test duration |
| `secrets_file` | Private credential JSON path |

The same configuration schema is retained in both modes. Fresh native setup
uses inert bridge camera placeholders; switching an existing bridge host to
native preserves its bridge selection for rollback. No camera ID is required
at native setup. Camera `stream_name` and native RTSP authentication username
are independent and need not match.

## Secrets and credentials

Private fields are optional `camera_rtsp_url`, `wyze_email`, `wyze_password`,
`wyze_api_id`, `wyze_api_key`, `wyze_totp_key`, plus `viewer_password` and
`youtube_key`. Setup generates a viewer password even for native mode; it is
only used if a bridge is run. Native mode needs no Wyze account/API login.
Bridged mode needs a saved account session or sufficient account/API credentials.
Revoked sessions may require fresh setup.

### Native RTSP or RTSPS cameras

Enable and save the camera's RTSP/RTSPS settings in the Wyze app. Use its separate
stream username/password, not the account login. Reserve the camera's LAN IP.
The tested v4 secure endpoint is port 322, path `/stream0`; follow the URL shown
by the camera rather than assuming all models use those values.

```sh
python3 wyze_stream.py configure --native-rtsp
python3 wyze_stream.py validate
python3 wyze_stream.py relay --check --seconds 30
```

Enter the complete authenticated URL at the hidden prompt. Percent-encode
reserved characters in the user-info component; do not put credentials in shell
arguments/history. The saved URL overrides bridge input. Never place it in public
configuration/examples. RTSPS encrypts camera traffic, but this implementation
does not verify the camera TLS certificate. Ordinary RTSP is also accepted and
has no camera-link encryption. YouTube TLS verification is independent and enabled.

### Legacy and Window Cam bridge

```sh
python3 wyze_stream.py configure --bridged
python3 wyze_stream.py validate
python3 wyze_stream.py bridge
python3 wyze_stream.py relay --check --seconds 30
```

Use the camera ID from bridge discovery, including any model-specific prefix.
For Window Cams, use `GW_WC` model metadata and a reserved LAN IPv4 address.
The normalized stream name is the Wyze nickname lowercased with spaces replaced
by underscores. Only `FILTER_MACS` is passed as an allow selector. Upstream ORs
model/ID filters and falls back to all cameras if none matches; confirm exactly
the intended camera in the authenticated loopback dashboard before broadcasting.

The rootless bridge uses `pasta` networking because an earlier Window Cam stream
froze over a public relay with another NAT backend. Revalidate local reception
on each new network. Native mode bypasses Podman and all bridge networking.

### Rotation, storage, and backups

`configure` preserves the current mode; blank hidden inputs keep saved values.
Use explicit `--native-rtsp` or `--bridged` when switching modes. `--bridged`
removes the native URL from active secrets while retaining other credentials.
Never source credential files as shell code. The application does not load `.env`;
its canonical private environment is `config.local.json`, `secrets.local.json`,
and the ignored state directory. Existing local `.env` values are also included
in publication audits without executing the file.

For YouTube-key rotation in either mode:

```sh
systemctl --user stop wyze-stream-relay
python3 wyze_stream.py configure --youtube-only
python3 wyze_stream.py validate
systemctl --user start wyze-stream-relay
```

For native camera credential rotation, save the new values on the camera, stop
the relay, run `configure --native-rtsp`, validate and locally test, then start
the relay. A mode change also needs service reinstallation as described below.
Store any backups outside the repository or under an ignored `*.local.json`
name with mode 600. Do not duplicate active credentials in ordinary scripts.

FFmpeg receives source credentials and the YouTube key through anonymous file
descriptors, not process arguments. Podman receives account credentials through
its environment. Relay logs redact loaded secrets and authenticated RTSP(S)
URLs. The secrets file is mode 600 and state directory mode 700; the deployment
user and administrators can still read them. Container inspection, saved sessions,
and generated bridge configuration can contain credentials. Never publish them.

## Services and upload recovery

```sh
python3 wyze_stream.py install-services
systemctl --user status wyze-stream-relay --no-pager
loginctl show-user "$USER" -p Linger
```

The installer enables lingering and enables/starts services appropriate to the
saved source. Native mode removes the relay's bridge dependency and disables/
stops the bridge. Bridged mode enables/starts both. Stop the relay before running
the installer when changing its source: installation does not restart an already
active relay. `install-services --no-start` writes/enables the selected units and
disables an unused bridge without starting/stopping services; it does not switch
an active process. Restart explicitly when ready.

The relay retries connection failures. Its watchdog checks acknowledged TCP
upload bytes, kills a stalled FFmpeg process, and lets the relay reconnect.
Systemd restarts the relay if its parent/watchdog exits and restarts a bridge
after container exits. The relay lock prevents duplicate managed uploads from
the same deployment. Networking can arrive after boot; processes continue retrying.

In native mode, expect relay `enabled/active`, bridge `disabled/inactive`, and
no bridge container. In bridged mode, expect both `enabled/active` and one camera
in the bridge dashboard. Native mode does not expose that dashboard.

To stop a broadcast and prevent restart at boot:

```sh
systemctl --user disable --now wyze-stream-relay wyze-stream-bridge
```

## Maintenance and diagnosis

```sh
journalctl --user -u wyze-stream-relay --since '10 minutes ago'
python3 wyze_stream.py relay --check --seconds 15
ss -Otinp state established dport = :443
```

Compare `bytes_acked` for the same FFmpeg process several seconds apart. Fixed
counters on an established socket can mean a stall. This verifies transport
progress, not YouTube event acceptance or viewer playback. Also check the intended
picture, Stream health in Studio, and viewer playback. Local `relay --check`
remuxes to `/dev/null` and never contacts YouTube; it does not fully decode video.
Perform a separate private capture/decode when investigating corruption and keep
footage out of Git.

Native diagnosis:

- `401 Unauthorized` / `Invalid Authorization`: the camera is reachable; recheck
  the exact saved stream username/password, including spelling, case, and URL
  encoding. Re-save in the camera app, then retry. Do not print the URL in logs.
- Connection refusal/timeout: check RTSPS is enabled, the displayed URL/port,
  DHCP reservation, network reachability, and firewall. An account-login fix on
  Ubuntu is not needed for a direct camera connection.
- A startup warning about an unset packet timestamp was observed with the v4;
  local FLV checks and upload continued successfully. Watch for ongoing timestamp
  failures, freezes, or restarts rather than treating a single warning as a stall.
- If the main stream changes to HEVC, select H.264 before using this relay.

Bridge-only diagnosis:

```sh
systemctl --user status wyze-stream-bridge --no-pager
journalctl --user -u wyze-stream-bridge --since '10 minutes ago'
podman ps
```

Use the configured container name for `podman logs` and redact before sharing.
An older container can remain after shutdown; if startup reports a different
camera, verify it is stopped and remove that specific stopped container with
`podman rm <configured-container-name>`, then restart the bridge. Never remove a
running container for another deployment.

Opening the native Wyze live view interrupted an earlier Window Cam session;
avoid competing camera sessions. A Window Cam cold connection previously needed
several Gwell retries and about four minutes. The bridge may report `streaming`
before media bytes arrive. Invalid NAL and repeated-timestamp warnings were also
observed while video still decoded; passthrough does not repair malformed source
bitstreams. Neither nominal state nor low bitrate alone proves stream health.

## Changing settings, modes, and upgrading

Always stop services before changing camera/image/container/port/state settings,
while the old configuration is still present:

```sh
systemctl --user stop wyze-stream-relay wyze-stream-bridge
```

Bridge → native v4 RTSPS:

```sh
python3 wyze_stream.py configure --native-rtsp
python3 wyze_stream.py validate
python3 wyze_stream.py relay --check --seconds 30
python3 wyze_stream.py install-services
```

Native → legacy/Window Cam bridge, or rollback to retained bridge settings:

```sh
python3 wyze_stream.py configure --bridged
python3 wyze_stream.py validate
python3 wyze_stream.py bridge
python3 wyze_stream.py relay --check --seconds 30
python3 wyze_stream.py install-services
```

In bridged setup, blank metadata inputs retain existing values when available.
If this host was originally native-only, supply actual bridge camera settings;
its native placeholders do not identify a Wyze device. Confirm only the intended
camera is admitted. If a native connection fails, the relay retries that URL;
it does not silently switch to another camera or account discovery.

After ordinary private configuration edits, validate, start the bridge manually
only for bridged mode, run a local check, then reinstall the selected services.
For script upgrades after stopping services:

```sh
git pull --ff-only
python3 wyze_stream.py validate
python3 wyze_stream.py install-services
```

Private configuration/secrets/state remain on the host. To roll back software,
stop services, check out a compatible tested revision, and reinstall its units.
Before reverting to a revision without `camera_rtsp_url` support, remove that
field privately and restore bridged mode using the current version first.
Do not reboot as part of an unattended upgrade without an agreed maintenance window.

YouTube's event lifecycle is independent of these services. Check the selected
event, auto-start/auto-stop, visibility, and health after recovery. Reusing an
ingestion key alone does not guarantee the same watch-page URL if an event ended.

## Reboot acceptance test

Schedule this interruption. Installation or a service restart does not prove a
successful reboot. Before reboot, verify lingering and the mode-specific service
states above, run a local media check, and record the boot ID privately:

```sh
cat /proc/sys/kernel/random/boot_id
systemctl --user is-enabled wyze-stream-relay
loginctl show-user "$USER" -p Linger
sudo systemctl reboot
```

After reconnecting:

```sh
cat /proc/sys/kernel/random/boot_id
systemctl --user status wyze-stream-relay --no-pager
journalctl -b _SYSTEMD_USER_UNIT=wyze-stream-relay.service --no-pager
python3 wyze_stream.py relay --check --seconds 15
ss -Otinp state established dport = :443
```

Confirm a changed boot ID, enabled/active relay, advancing upload bytes on the
same encoder across samples, decoded media, Studio health, and viewer playback.
Native: confirm bridge disabled/inactive and a camera RTSPS connection on its
configured port. Bridged: additionally check bridge enabled/active, this boot's
bridge logs, exactly one selected camera, and allow several minutes for Window
Cam handshakes. With optional administration services, also verify VPN access,
remote desktop, and maintenance timers.

## Preparing GitHub publication

Enable the repository's local pre-commit guard once per clone:

```sh
git config core.hooksPath .githooks
```

The hook audits the entire index against the public allowlist and known local
secrets. The scanner detects authenticated RTSP/RTSPS URLs, common tokens, and
raw/URL-encoded credential components from the saved native URL. `.gitignore`
excludes `*.local.json`, `.env`, sessions, state, logs, archives, and private tools.
Never force-add those files or skip the hook. CI performs the public audit too,
but only the host holding the private files can compare against its actual secrets.

Before each publication:

1. Run the full tests, shell syntax check, and `tools/prepare_public.py --audit-only`.
2. Review changes and stage only public source, tests, examples, and documentation.
3. Run `python3 tools/prepare_public.py --staged` and review `git diff --cached`.
4. Verify private files are ignored, absent from the index, and absent from history.
5. Commit and push only the audited public changes; use a GitHub noreply author address.

For a separate distribution directory, use `--output` with a new destination;
only allowlisted text is exported. Pattern matching is not a guarantee against
all secrets, so review public content and exported file lists as well. If a secret
is ever published, rotate it and address history exposure; deleting the current
file does not erase earlier commits.
