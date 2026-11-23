# Optional Raspberry Pi baseline

This is an optional preparation guide for a Pi that will run wyze-stream and
may need remote administration. The streaming installer does not install or
configure these management services. Choose the sections you need; remote
desktop and a VPN are not prerequisites for camera streaming.

The combination was exercised on a Raspberry Pi 5 with 8 GB RAM, 64-bit
Raspberry Pi OS based on Debian 13, rootless Podman with `pasta`, Tailscale,
RustDesk, and unattended APT updates. Examples below use generic hostnames and
the current Linux user. Supply your own infrastructure, credentials, and timezone
privately. No account tokens, server keys, Wi-Fi passwords, or stream keys belong
in the repository.

## 1. Prepare the OS and administrative access

Use a 64-bit OS and a suitable power supply. Prefer Ethernet when the goal is
stable streaming; Wi-Fi also worked in the tested installation. Set the hostname
and timezone for the deployment, then patch the OS:

```sh
uname -m                         # expect aarch64
timedatectl                     # verify timezone and clock synchronization
sudo apt update
sudo apt full-upgrade
sudo reboot
```

Reconnect after the reboot and check `uname -r`. Installing a new kernel does not
activate it until a reboot. Keep a working LAN/console access path while adding
remote management.

Create a dedicated SSH key on the administration machine and install its public
key for the Pi's deployment user:

```sh
ssh-keygen -t ed25519 -f ~/.ssh/stream_pi_ed25519
ssh-copy-id -i ~/.ssh/stream_pi_ed25519.pub <deployment-user>@stream-pi.local
```

Verify the Pi's SSH host-key fingerprint through a trusted path on first use.
An SSH key and sudo authorization are separate: on the Pi, `sudo -n true` checks
whether unattended administrative commands are allowed. If unattended provisioning
needs passwordless sudo, an administrator can configure a user-specific rule with
`sudo visudo -f /etc/sudoers.d/90-stream-provisioning`. This grants administrative
access and is not required for the normal rootless streaming services.

## 2. Add optional Wi-Fi profiles

Use NetworkManager to save additional profiles without replacing the active
connection. For example:

```sh
sudo nmcli connection add type wifi ifname wlan0 \
  con-name travel-wifi ssid '<additional-ssid>' connection.autoconnect no
sudo nmcli connection modify travel-wifi \
  wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn
sudo nmtui edit travel-wifi
```

Enter the password privately in the editor, save it for the system connection,
and enable automatic connection when the profile is complete. Do not place real
passwords in copied commands or documentation. Verify profiles without displaying
their secrets:

```sh
nmcli -f NAME,TYPE,AUTOCONNECT connection show
nmcli -f GENERAL.CONNECTION,IP4.ADDRESS device show wlan0
sudo find /etc/NetworkManager/system-connections -maxdepth 1 -type f -printf '%m %f\n'
```

Saved credentials are not proof that association works at the other location.
Test each network on site and check its DNS, default route, and camera reachability.
A camera's configured LAN IP must be reachable from the Pi's selected network.

## 3. Optional Tailscale access

Follow the current [Tailscale Linux installation instructions](https://tailscale.com/download/linux)
or its [official package repository](https://pkgs.tailscale.com/stable/), selecting
the Debian release matching the Pi's OS. Then:

```sh
sudo systemctl enable --now tailscaled
sudo tailscale up
tailscale status
```

Complete the account authorization using the link printed by `tailscale up`.
An installed, running daemon is not yet an authorized device. Verify access from
the administration machine and confirm the device is online after reboot. Keep
device addresses, tailnet names, login links, and auth keys out of public reports.
This baseline uses ordinary SSH over the VPN; it does not require Tailscale SSH,
subnet routing, or an exit node.

## 4. Optional RustDesk remote desktop

Install the current official **aarch64 Debian package** from
[RustDesk releases](https://github.com/rustdesk/rustdesk/releases), verifying the
release asset's SHA-256 digest before installation. Use `sudo apt install` with
the downloaded package path so dependencies are resolved. Enable its service:

```sh
sudo systemctl enable --now rustdesk
```

For a self-hosted server, set the ID and relay host, API URL, complete server
public key, and WebSocket option. The following placeholders must be replaced
with your own values:

```sh
sudo rustdesk --option custom-rendezvous-server relay.example.com
sudo rustdesk --option relay-server relay.example.com
sudo rustdesk --option api-server https://relay.example.com
sudo rustdesk --option key '<complete-server-public-key>'
sudo rustdesk --option allow-websocket Y
```

The RustDesk server public key is separate from the Pi's SSH public key. Copy it
in full from a trusted server console or existing configuration. The HTTPS API
URL is important: the tested client uses it to select secure WebSockets (`wss`).
The server must expose working `/ws/id` and `/ws/relay` reverse-proxy endpoints.
See [RustDesk WebSocket configuration](https://rustdesk.com/docs/en/self-host/rustdesk-server-pro/faq/#8-add-websocket-secure-wss-support-for-the-id-server-and-relay-server-to-enable-secure-communication-for-all-platforms).

Set a unique permanent password privately in RustDesk's Security settings. For
unattended password access, configure the corresponding options:

```sh
sudo rustdesk --option verification-method use-permanent-password
sudo rustdesk --option approve-mode password
sudo rustdesk --option allow-logon-screen-password Y
sudo rustdesk --get-id
```

Keep the password and the machine's RustDesk ID private. Verify the client has
connected to the intended server, then test an actual remote desktop session.

### Headless display: fixing "no displays"

The tested Wayland desktop was switched to X11 for unattended remote desktop:

```sh
sudo raspi-config nonint do_wayland W1
```

X11 alone was insufficient on a Pi with no HDMI monitor. The desktop session
existed, but both outputs were disconnected and RustDesk reported **no displays**.
With the normal KMS driver enabled, back up `/boot/firmware/cmdline.txt` and append
this argument to its existing **single line**, preserving every existing boot and
root-filesystem argument:

```text
video=HDMI-A-1:1920x1080@60D
```

Do not substitute legacy `hdmi_force_hotplug` settings for the KMS configuration.
After reboot, the forced connector was present but X11 initially chose 1024x768.
The following monitor preference made 1080p persistent. Save it as
`/etc/X11/xorg.conf.d/10-headless-monitor.conf` (create the directory if needed):

```text
Section "Monitor"
    Identifier "HDMI-1"
    Option "PreferredMode" "1920x1080"
EndSection
```

Reboot, then check from an SSH session as the desktop user:

```sh
DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" xrandr --listmonitors
```

Expect one connected 1920x1080 monitor. The kernel name `HDMI-A-1` and the X11
name `HDMI-1` differ. Adapt the display number and authority path if your desktop
session uses different ones. See [Raspberry Pi display configuration](https://www.raspberrypi.com/documentation/computers/configuration.html)
and [RustDesk Linux limitations](https://rustdesk.com/docs/en/client/linux/).

## 5. Optional unattended OS updates and reboots

Install Debian's maintenance service:

```sh
sudo apt install unattended-upgrades
```

Review `apt-cache policy` for the actual repository origins. On the tested Debian
13 installation, a local file such as `/etc/apt/apt.conf.d/99-pi-maintenance`
contained the following policy. Adjust release names for a different OS:

```text
APT::Periodic::Enable "1";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
#clear Unattended-Upgrade::Origins-Pattern;
Unattended-Upgrade::Origins-Pattern {
    "origin=Debian,codename=trixie,label=Debian";
    "origin=Debian,codename=trixie-updates,label=Debian";
    "origin=Debian,codename=trixie-security,label=Debian-Security";
    "origin=Raspberry Pi Foundation,codename=trixie";
    "origin=Tailscale,codename=trixie";
};
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
Unattended-Upgrade::SyslogEnable "true";
```

The Tailscale origin is needed only when that optional repository is installed.
Unlisted third-party repositories are not automatically included. The pinned
Wyze Bridge container image is also not updated by APT.

Use `sudo systemctl edit apt-daily.timer` to set a package-list/download window:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 02:30:00
RandomizedDelaySec=300
Persistent=true
```

Use `sudo systemctl edit apt-daily-upgrade.timer` for the installation window:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=300
Persistent=true
```

These are host-local times with up to five minutes of jitter. Persistent timers
can catch up after the machine was off. Required reboots are scheduled for 04:00,
including when users are logged in; a delayed run can schedule the next 04:00.
Choose a timezone and maintenance window that suit the broadcast. Rebooting
interrupts streaming, and YouTube's event auto-stop settings can affect recovery.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service
sudo systemctl restart apt-daily.timer apt-daily-upgrade.timer
sudo unattended-upgrade --dry-run --debug
systemctl list-timers 'apt-*' --no-pager
```

The installed unattended-upgrades package provides a kernel hook that marks a
required reboot. An optional `needrestart` check reported the alternative Pi
kernel flavor as newer on the tested system, despite the correct Pi 5 kernel
already running; do not infer a pending reboot solely from that diagnostic.

### Optional RustDesk release updates

RustDesk was installed from a downloaded `.deb`, so APT cannot discover its next
upstream release. The optional helper in `tools/pi-baseline/` checks the official
GitHub stable release, selects ARM64, rejects missing/mismatched checksums or
package metadata, and installs only newer versions. It backs up private
configuration before an update, waits for the APT lock, prevents package removals,
and restarts RustDesk. It does not automatically roll back a failed upgrade.

Install it from this repository as your normal deployment user:

```sh
sudo install -o root -g root -m 755 tools/pi-baseline/rustdesk-auto-update.py \
  /usr/local/sbin/rustdesk-auto-update
python3 - <<'PY' | sudo tee /etc/systemd/system/rustdesk-auto-update.service >/dev/null
from pathlib import Path
from wyze_stream import unit_quote
template = Path('tools/pi-baseline/rustdesk-auto-update.service.in').read_text()
print(template.replace('@CONFIG_HOME@', unit_quote(Path.home() / '.config/rustdesk')), end='')
PY
sudo install -o root -g root -m 644 tools/pi-baseline/rustdesk-auto-update.timer \
  /etc/systemd/system/rustdesk-auto-update.timer
sudo /usr/local/sbin/rustdesk-auto-update --check
sudo systemd-analyze verify /etc/systemd/system/rustdesk-auto-update.service \
  /etc/systemd/system/rustdesk-auto-update.timer
sudo systemctl daemon-reload
sudo systemctl start rustdesk-auto-update.service
sudo systemctl enable --now rustdesk-auto-update.timer
```

The timer checks around 03:20 in the host's timezone. Its system service requests
a shutdown inhibitor during the operation and is ordered after the APT upgrade
service. A real update briefly disconnects RustDesk sessions. `--check` downloads
and verifies a release without installing it. Configuration backups are private
under `/var/backups/rustdesk-updater`; the root user's config and the explicitly
configured desktop user's directory are included. Treat these backups as secrets.

```sh
systemctl list-timers rustdesk-auto-update.timer --no-pager
sudo journalctl -u rustdesk-auto-update.service --no-pager
```

## 6. Add the streaming workload and verify recovery

Follow the [main setup](../README.md#set-up-one-host) and
[deployment guide](deployment.md), using rootless Podman and `pasta`. This baseline
does not require Docker. APT may suggest Docker-related packages; a suggestion
is not an instruction to install another container engine.

Gotchas observed during setup:

- A cached Wyze login can work on an existing host even when its original login
  and API credentials are no longer stored there. A fresh Pi needs the complete
  login and API ID/key; a key suffix is insufficient. Keep independent state on
  each host. `configure` writes supported, mode-600 private JSON files. A local
  `.env` is Git-ignored and compared by the public audit, but the application does
  not load it automatically or execute its contents.
- The hardware MAC and the cloud camera identifier can differ. Discover the exact
  bridge camera ID and normalized RTSP name, then pin the camera's LAN IP when
  needed. Do not guess the ID from the physical MAC.
- Upstream model and camera-ID allow selectors are ORed. A model selector admitted
  both same-model cameras; the wrapper now sends the camera ID alone. Verify the
  admitted camera list on every host. Upstream also falls back to all cameras if
  none match, so a stale ID is unsafe to assume harmless.
- A Gwell camera can report `streaming` before it supplies video. Initial RTSP
  requests returned 404, then media appeared after the handshake/retry interval.
  Allow several minutes for a cold start and verify received bytes, local remuxing,
  and decoded frames. Avoid opening a competing native Wyze live view.
- Some source H.264/NAL warnings continued while video decoded and uploads
  advanced. Passthrough does not repair the camera bitstream; assess the actual
  picture, freezes, and YouTube Stream health rather than logs alone.
- The dashboard and RTSP listeners should remain on `127.0.0.1`. Use SSH forwarding
  for administration rather than exposing them directly to the LAN or Internet.
- If `journalctl --user -u ...` shows no entries, try
  `journalctl _SYSTEMD_USER_UNIT=wyze-stream-relay.service`; add sudo if journal
  permissions require it. Review/redact logs before sharing them.

Check idle resources before streaming, then compare under sustained load:

```sh
uptime
free -h
df -h /
vmstat 1 6
vcgencmd measure_temp
vcgencmd get_throttled
```

The tested Pi had ample memory and no throttling during a single 1080p passthrough
upload. This is a smoke test, not a capacity guarantee for other cameras or extra
workloads. Multiple independent hosts on one Wyze account were exercised with
different camera selections and YouTube keys.

Finish with the [full reboot acceptance test](deployment.md#reboot-acceptance-test).
Verify enabled user units and `Linger=yes`, record the boot ID, reboot, and confirm
new-boot service startup, exactly one assigned camera, local media, and advancing
acknowledged upload bytes. Also check Tailscale authorization, the RustDesk display,
and maintenance timers if installed. YouTube Studio and viewer playback remain
the final external checks. Keep the acceptance record private; do not publish
device identifiers, network details, or raw journals with the documentation.
