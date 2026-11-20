"""Reconnect a foreground YouTube relay whose upload stops making progress."""

import argparse
import os
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path


def encoder_child(parent):
    children = Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
    for child in children:
        try:
            if Path(f"/proc/{child}/comm").read_text().strip() == "ffmpeg":
                return int(child)
        except FileNotFoundError:
            continue
    return None


def upload_progress(pid, port):
    # Read kernel TCP counters without touching the RTSP feed or the stream key.
    result = subprocess.run(
        ["ss", "-OHintp", "state", "established", "dport", "=", f":{port}"],
        capture_output=True, text=True, check=True, timeout=4,
    )
    for line in result.stdout.splitlines():
        if f"pid={pid}," in line:
            match = re.search(r"\bbytes_acked:(\d+)", line)
            return int(match[1]) if match else 0
    return None


def watch_encoder(parent, pid, parent_exit, port=443, interval=5, stall=45, startup=90):
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        # Verify ownership after opening the process handle, including across PID reuse.
        if encoder_child(parent) != pid:
            return
        exited = select.poll()
        exited.register(fd, select.POLLIN)
        exited.register(parent_exit, select.POLLIN)
        last_change = time.monotonic()
        previous = None
        connected = False
        while not exited.poll(interval * 1000):
            try:
                count = upload_progress(pid, port)
            except (OSError, subprocess.SubprocessError) as error:
                print(f"Upload monitor unavailable: {error}", file=sys.stderr, flush=True)
                last_change = time.monotonic()
                continue
            now = time.monotonic()
            if count is not None and count != previous:
                connected = True
                last_change = now
            previous = count
            limit = stall if connected else startup
            if now - last_change < limit:
                continue
            reason = "no upload progress" if connected else "no upload connection"
            print(f"YouTube relay: {reason} for {limit}s; reconnecting.", file=sys.stderr, flush=True)
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            if not exited.poll(5000):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            return
    except (FileNotFoundError, ProcessLookupError):
        return
    finally:
        os.close(fd)


def main(parent, **settings):
    parent_exit = os.pidfd_open(parent)
    exited = select.poll()
    exited.register(parent_exit, select.POLLIN)
    try:
        while not exited.poll(0):
            try:
                pid = encoder_child(parent)
            except FileNotFoundError:
                break
            if pid is not None:
                watch_encoder(parent, pid, parent_exit, **settings)
            exited.poll(2000)
    finally:
        os.close(parent_exit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('parent', type=int)
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--stall', type=int, default=45)
    parser.add_argument('--startup', type=int, default=90)
    args = parser.parse_args()
    if min(args.interval, args.stall, args.startup) < 1:
        parser.error('timeouts must be positive')
    main(args.parent, interval=args.interval, stall=args.stall, startup=args.startup)
