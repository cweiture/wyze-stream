# wyze-stream

One Linux host → independent Wyze camera streams → separate YouTube Live broadcasts.

[![checks](https://github.com/cweiture/wyze-stream/actions/workflows/checks.yml/badge.svg)](https://github.com/cweiture/wyze-stream/actions/workflows/checks.yml)

Stream a Wyze Cam v4 over native RTSPS, or use Wyze Bridge for legacy cameras
and Window Cams. Both paths copy H.264 video into a verified, encrypted YouTube
RTMPS upload with silent stereo AAC audio. Video is not re-encoded.
Each deployment selects one camera and one managed upload. Multiple isolated
deployments can run together from the same checkout on one host, including a
native v4 front camera alongside a bridged Window Cam using Gwell.

| Mode | Video path | Camera/account requirements |
| --- | --- | --- |
| Native v4 RTSPS | Camera → FFmpeg → YouTube | Enable native RTSPS in the Wyze app; save its separate stream credentials and URL |
| Bridged legacy / Window Cam | Camera → Wyze Bridge → FFmpeg → YouTube | Supported camera/firmware, Wyze account and Developer API credentials, exact bridge camera ID |

Native v4 RTSPS has been tested on Ubuntu with firmware 4.52.17.27 at
2560×1440, 20 fps, H.264, including local remux/decode and advancing YouTube
upload counters. The legacy Window Cam path has been tested at 1920×1080;
bridged operation also worked on a Raspberry Pi 5. These are tested
configurations, not a guarantee for every firmware release or long-term uptime.

## Requirements

Both modes need 64-bit Linux, Python 3.10+, systemd, Bash, FFmpeg with RTSP/TLS
support, iproute2 (`ss`), system CA certificates, a reachable camera, and a
YouTube Live event/stream key. Native mode does not need Podman, Wyze Bridge,
or Wyze account/API credentials on the host.

Bridged mode additionally needs Podman supporting `--network=pasta`, `passt`,
and a supported Wyze camera and firmware. The example pins Wyze Bridge 4.5.0's
verified multi-architecture image digest for `linux/amd64` and `linux/arm64`.

A Pi 5 with 8 GB RAM and 64-bit Raspberry Pi OS based on Debian 13 was tested
with one bridged 1080p stream, rootless Podman 5.4.2, and `pasta`. A Pi 4 with
2 GB or more is an untested starting point for that workload. Native 1440p
operation on a Pi has not been hardware-tested. Prefer Ethernet and suitable
power/cooling; Wi-Fi also worked in the Pi 5 test.
For optional administration and maintenance, see the [Pi baseline guide](docs/pi-baseline.md).

## Install on one host

On Ubuntu 24.04:

```sh
sudo apt update
sudo apt install git ffmpeg iproute2 ca-certificates python3
git clone https://github.com/cweiture/wyze-stream.git
cd wyze-stream
git config core.hooksPath .githooks
```

For bridged mode, also install:

```sh
sudo apt install podman passt
```

Use the project as your normal Linux user, not root. Keep the checkout at its
installed path because generated services refer to it. Other distributions
need equivalent packages.

Before setup, enable encoder-based live streaming on your YouTube channel and
choose the intended event, visibility, RTMPS server, and stream key in Studio.
The ingestion address differs from the watch-page URL shared with viewers.
See [YouTube's encoder setup](https://support.google.com/youtube/answer/2907883?hl=en)
and [stream settings](https://support.google.com/youtube/answer/9854503?hl=en).
Set `youtube.server` in the private configuration if your destination differs
from the example.

### Option A: Wyze Cam v4 native RTSPS

In the Wyze app, open the camera's RTSP settings, enable RTSPS, save the RTSP
username/password, and copy the displayed URL. Credentials are separate from
the Wyze account login. The tested secure endpoint uses port 322 and path
`/stream0`; use the URL your camera actually displays and reserve its LAN IP
in DHCP.

```sh
python3 wyze_stream.py configure --native-rtsp
python3 wyze_stream.py validate
python3 wyze_stream.py relay --check --seconds 30
```

At the hidden camera URL prompt, enter the complete URL with the RTSP username
and password in its user-info component. Percent-encode reserved characters in
credentials. Never paste a credential-bearing URL into a shell command, issue,
or commit. Setup asks for the YouTube key privately and does not ask for Wyze
login/API credentials or a bridge camera ID.

The direct source must supply H.264 for this relay. The tested v4 main stream
preserves 1440p/20 fps without transcoding. Native RTSP without TLS is also
supported, but RTSPS encrypts camera traffic. Camera certificate verification
is currently not enabled; YouTube's separate TLS connection is verified.

### Option B: legacy or Window Cam through Wyze Bridge

```sh
python3 wyze_stream.py configure --bridged
python3 wyze_stream.py validate
python3 wyze_stream.py bridge
python3 wyze_stream.py relay --check --seconds 30
```

Setup asks for the exact bridge camera ID, normalized stream name, optional
model/LAN IP, Wyze account and Developer API credentials, and YouTube key.
All credential prompts are hidden. Plain `configure` retains an existing mode
and defaults to bridged setup on a new host.

Use the ID reported by Wyze Bridge, not a guessed hardware address. Window Cams
may use a `GW_WC_...` ID. Normalize the Wyze nickname to lowercase with spaces
replaced by underscores. For a Window Cam, set model `GW_WC` and its reserved
LAN IP. Leave unnecessary model/IP fields blank for other cameras. Verify that
exactly the intended camera appears before broadcasting: upstream falls back
to all cameras if no filter ID matches.

The authenticated bridge dashboard defaults to `http://127.0.0.1:5080/`.
Dashboard and RTSP ports bind only to loopback. Username: `viewer`; the generated
password is stored privately. Camera audio, recording, and periodic snapshots
are disabled. Native mode does not run this dashboard.

## Private local files

Setup writes `config.local.json` and mode-600 `secrets.local.json`; both are
ignored by Git. Native `camera_rtsp_url`, Wyze login/API credentials, viewer
password, and YouTube key belong only in the secrets file. Unused bridge
settings are retained for rollback. `bridge-state/` is ignored, mode 700, and
holds bridge sessions and the relay lock. The application does not source `.env`.

A local `relay --check` writes to `/dev/null` and never uploads to YouTube.
It checks packet reception/remuxing; separately inspect decoded video and
viewer playback. See [deployment procedures](docs/deployment.md) for diagnosis,
credential rotation, mode switching, rollback, and reboot acceptance.

## Survive reboot and logout

Stop any older relay using this camera/key, then install:

```sh
python3 wyze_stream.py install-services
systemctl --user status wyze-stream-relay --no-pager
journalctl --user -u wyze-stream-relay -f
```

The installer enables user lingering and persistent services. Native mode starts
only the relay and disables/stops the unused bridge; bridged mode enables both.
The relay retries camera/YouTube failures, and an upload watchdog recycles FFmpeg
when acknowledged upload bytes stall. In bridged mode, systemd also restarts the
bridge after container exits. Credentials stay out of FFmpeg arguments and relay
logs are redacted. Installation does not reboot.

If lingering needs administrator authorization, have an administrator enable it
for the deployment user. Services retry while networking becomes available;
active services alone do not prove viewer playback. Confirm the intended picture
and Stream health in YouTube Studio. Keep camera audio private: this relay always
uploads generated silence.

To stop broadcasting persistently:

```sh
systemctl --user disable --now wyze-stream-relay wyze-stream-bridge
```

Stop services before camera/settings/mode changes, then validate, locally test,
and reinstall services for the selected mode. Follow the exact
[switching and upgrade procedures](docs/deployment.md#changing-settings-modes-and-upgrading).
The relay lock prevents duplicate managed uploads from one deployment.
`test-bridge.py` and `youtube-relay.sh` remain compatibility entry points.

## Two streams on one host

Keep an existing default deployment running, then add a named deployment with
its own YouTube key. For example, a native v4 front stream plus a bridged Window
Cam rear stream:

```sh
python3 wyze_stream.py --config config.backyard.local.json configure --bridged
python3 wyze_stream.py --config config.backyard.local.json validate
python3 wyze_stream.py --config config.backyard.local.json bridge
python3 wyze_stream.py --config config.backyard.local.json relay --check --seconds 30
python3 wyze_stream.py --config config.backyard.local.json install-services --instance backyard
```

New `config.NAME.local.json` setup automatically selects `secrets.NAME.local.json`,
`state/NAME/`, a separate container name, and ports not already assigned in other
local configuration files. Check those ports against other applications before
starting. Set the Window Cam's reserved LAN IP and verify the bridge admits only
that camera. Enter the second event's key at the hidden prompt. Each profile has
its own credentials, relay lock, watchdog, retry loop, and systemd services.

The default front relay stays `wyze-stream-relay.service`. This example adds
`wyze-stream-backyard-bridge.service` and `wyze-stream-backyard-relay.service`;
installing or stopping them does not alter the default services. Always use the
same `--config` path and `--instance` name for subsequent installations/upgrades.

```sh
systemctl --user status wyze-stream-relay wyze-stream-backyard-relay --no-pager
journalctl --user -u wyze-stream-backyard-relay -f
# Stop only the additional deployment, including after reboot:
systemctl --user disable --now wyze-stream-backyard-relay wyze-stream-backyard-bridge
```

Never share a state directory, bridge container name, listening port, or YouTube
key between profiles. Existing profiles are not automatically rewritten. The
native front process does not depend on the rear bridge, and a native named
deployment disables only its own named bridge. Concurrent native 1440p and
bridged 1080p uploads have been tested on Ubuntu. Revalidate host capacity,
camera reception, and viewer playback for your own combination.

## Repeat on another Pi or Linux host

Clone and configure each host independently with its own camera, private files,
and YouTube key/event. Do not copy runtime state. With a bridge, the wrapper sends
only `FILTER_MACS` as its allow selector and must admit exactly the assigned camera.
Distinct bridged cameras/YouTube keys on simultaneous hosts sharing one account
were tested; revalidate each installation. Native mode avoids account discovery
on the host and connects only to its saved camera URL.

Upstream [bridge camera compatibility](https://github.com/IDisposable/docker-wyze-bridge#camera-support)
varies by model/firmware. This project does not bundle Wyze Bridge; see
[third-party notices](THIRD_PARTY_NOTICES.md).

## Development and safe publication

```sh
python3 -m unittest discover -s tests -v
bash -n youtube-relay.sh
python3 tools/prepare_public.py --audit-only
python3 tools/prepare_public.py --staged
python3 tools/prepare_public.py --output /tmp/wyze-stream-public
```

Enable `.githooks/pre-commit` with the `core.hooksPath` command above. It audits
the entire Git index before each commit and blocks files outside the public
allowlist, credential-bearing RTSP/RTSPS URLs, common tokens, and known local
secret values, including separate native URL passwords. The CI audit runs
without your private secrets. This is a guard, not proof against every possible
secret; review the staged diff and never force-add private files or bypass the hook.

The exporter copies only allowlisted text files and excludes runtime data,
private tooling, logs, footage, and capture archives. Keep credentials, account
sessions, camera IDs, private network details, and traces out of issues and Git.
The original scripts/documentation are MIT licensed. Report bugs with the mode,
OS/architecture, software versions, camera model/firmware, and a redacted error
excerpt through [GitHub issues](https://github.com/cweiture/wyze-stream/issues).
