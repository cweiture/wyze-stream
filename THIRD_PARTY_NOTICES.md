# Third-party software

wyze-stream provides deployment and relay scripts. It depends on, and does not
bundle or relicense, these separately distributed projects:

- [Docker Wyze Bridge](https://github.com/IDisposable/docker-wyze-bridge),
  published by IDisposable; upstream identifies its license as AGPL-3.0.
- [FFmpeg](https://ffmpeg.org/), subject to the license of the installed build.
- [Podman](https://podman.io/) and [passt/pasta](https://passt.top/).
- [systemd](https://systemd.io/) and Linux iproute2 (`ss`).

The MIT license in this repository covers its original scripts and documentation.
It does not change the terms of any dependency. Wyze, Raspberry Pi, and YouTube
are trademarks of their respective owners; this project is not affiliated with them.
