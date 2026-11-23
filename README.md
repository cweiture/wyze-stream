# wyze-stream

One Wyze camera → one Linux host → one YouTube Live broadcast.

[![checks](https://github.com/cweiture/wyze-stream/actions/workflows/checks.yml/badge.svg)](https://github.com/cweiture/wyze-stream/actions/workflows/checks.yml)

Run a selected camera from a Wyze account on a Raspberry Pi or Linux machine.
Wyze Bridge receives the camera video, and FFmpeg copies its H.264 packets into
an encrypted YouTube RTMPS upload with silent stereo AAC audio. Video is not
re-encoded. Each host is configured for one camera even when the account has more.

## Requirements

- 64-bit Linux, Python 3.10+, systemd, Bash, FFmpeg, Podman with `pasta` support,
  `passt`, iproute2 (`ss`), and system CA certificates.
- A supported Wyze camera with compatible firmware, reachable from the host.
- Wyze login and Developer API ID/key, plus a configured YouTube Live event/key.

A Raspberry Pi 5 with 8 GB RAM and 64-bit Raspberry Pi OS based on Debian 13 has
been hardware-tested with one 1080p H.264 passthrough stream, rootless Podman
5.4.2, and `pasta`. A Pi 4 with 2 GB or more is an untested starting point for
the same workload. Use suitable power/cooling and prefer Ethernet; Wi-Fi also
worked in the Pi 5 test. The example pins the verified multi-architecture Wyze
Bridge 4.5.0 release manifest, including `linux/amd64` and `linux/arm64`.

For optional SSH, Wi-Fi profiles, Tailscale, headless RustDesk, and automatic
maintenance, see the [new Pi baseline guide](docs/pi-baseline.md). These services
are separate from the streaming installer.

## Set up one host

On Ubuntu 24.04, install dependencies:

```sh
sudo apt update
sudo apt install git podman passt ffmpeg iproute2 ca-certificates python3
git clone https://github.com/cweiture/wyze-stream.git
cd wyze-stream
```

Other distributions need equivalent packages and a Podman release supporting
`--network=pasta`. Use this project as your normal Linux user, not root.
Keep the checkout at its installed path: the generated services refer to it.

Before setup, enable encoder-based live streaming on your YouTube channel and
choose the intended event, visibility, RTMPS server, and stream key in YouTube
Studio. Enter the server under `youtube.server` in the private configuration if
it differs from the example. The RTMPS ingestion address is separate from the
YouTube watch-page URL you share with viewers. See [YouTube's encoder setup](https://support.google.com/youtube/answer/2907883?hl=en)
and [stream settings](https://support.google.com/youtube/answer/9854503?hl=en).

```sh
python3 wyze_stream.py configure
python3 wyze_stream.py validate
python3 wyze_stream.py bridge
python3 wyze_stream.py relay --check
```

`configure` asks for one camera ID, its normalized RTSP name (for example
`front_door`), optional model metadata and LAN IP, account credentials, and the
YouTube stream key. All credential prompts are hidden. It creates:

- `config.local.json`: per-host settings, based on `config.example.json`.
- `secrets.local.json`: credentials, with mode 600. Both files are ignored by Git.
- `bridge-state/`: private saved Wyze session, generated media configuration,
  and relay lock. Its directory has mode 700 and is ignored by Git.

Use the camera ID reported by Wyze Bridge, not a guessed hardware address.
Gwell Window Cams may use a `GW_WC_...` ID. The stream name is the camera's
normalized Wyze nickname: lowercase with spaces replaced by underscores.
For a Window Cam, set its LAN IP and model `GW_WC`; reserve the IP in DHCP.
For other models, check upstream support and leave unneeded metadata/IP fields empty.

The local dashboard is at `http://127.0.0.1:5080/` by default. Both published
ports bind to loopback and require authentication. Username: `viewer`.
The generated viewer password is in the private secrets file. View it locally;
do not paste it into issue reports. The RTSP port and dashboard port are configurable.
Camera audio, recording, and periodic snapshots are disabled by this deployment.

A local check writes FLV to `/dev/null` and never contacts YouTube. A short check:

```sh
python3 wyze_stream.py relay --check --seconds 15
```

## Survive reboot and logout

Stop any older relay for this camera/key before enabling the new relay. Then:

```sh
python3 wyze_stream.py install-services
systemctl --user status wyze-stream-bridge wyze-stream-relay
journalctl --user -u wyze-stream-relay -f
```

The installer writes persistent user services, enables user lingering, and starts
both services. Lingering lets the user's services start at boot without a desktop
login. If policy prevents `loginctl enable-linger`, an administrator must enable it
for the deployment user before unattended boot will work. Installation does not reboot.

The bridge service adopts the matching running container or recreates it from
private configuration. systemd restarts it after container exits. The relay retries
camera/YouTube connection failures. An upload watchdog checks acknowledged TCP
bytes and recycles FFmpeg after a stall. systemd restarts the relay if its parent
or watchdog exits. Saved Wyze sessions and optional stored login credentials allow
noninteractive startup; revoked credentials or expired access may require setup again.

Network connectivity can arrive after the user manager starts: services keep
retrying without relying on a user-level `network-online.target`. Camera media
startup may take additional time after the units become active. Confirm the picture
and ingestion health in YouTube Studio; active services alone do not prove playback.

To stop broadcasting without it returning on reboot:

```sh
systemctl --user disable --now wyze-stream-relay wyze-stream-bridge
```

Stop both services before changing deployment settings, then edit the private
configuration, validate it, and start both again. Stop before editing so the old
container name and state path remain available during shutdown. See the exact
commands in [deployment details](docs/deployment.md#changing-settings-and-upgrading).
The relay lock prevents a second managed upload from this deployment.
`test-bridge.py` and `youtube-relay.sh` remain compatibility entry points.

## Repeat on another Pi

Clone the same repository and run setup independently. Give the second Pi its own
camera selection, private state/secrets, and YouTube event/key. Do not copy one
Pi's runtime state to another. Account-level Wyze discovery may list all cameras;
the wrapper sends `FILTER_MACS` as its only allow selector. Verify that the bridge
admits exactly the assigned camera before broadcasting: upstream falls back to
all cameras if no ID matches. Simultaneous hosts sharing one account have been
tested with distinct camera selections and YouTube keys; revalidate each deployment.

See [deployment and troubleshooting](docs/deployment.md) for settings, maintenance,
and the validation limits. Upstream [camera compatibility](https://github.com/IDisposable/docker-wyze-bridge#camera-support)
varies by model and firmware. This repository packages original deployment scripts;
it does not bundle the upstream bridge. See [third-party notices](THIRD_PARTY_NOTICES.md).

## Development and public release

```sh
python3 -m unittest discover -s tests -v
bash -n youtube-relay.sh
python3 tools/prepare_public.py --audit-only
python3 tools/prepare_public.py --output /tmp/wyze-stream-public
```

The release tool copies only an explicit allowlist of source, examples, tests, and
documentation. It excludes runtime data, private tooling, logs, and capture archives.
It blocks common credential formats and matches against known local secrets and
camera settings without printing those values. This is a useful guard, not proof
that arbitrary new content is safe: review the final file list and staged diff.
`--staged` audits the entire Git index before a push. Never run `git add -f` on runtime data.

No credentials, camera footage, saved sessions, or trace archives belong in Git.
The original project scripts and documentation are MIT licensed.

For bug reports, open a [GitHub issue](https://github.com/cweiture/wyze-stream/issues)
with the host architecture, OS, Podman/FFmpeg versions, camera model and firmware,
and a redacted error excerpt. Keep account credentials, stream keys, camera IDs,
private network details, and raw state/capture files out of public reports.
