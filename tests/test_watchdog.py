import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import unittest

spec = importlib.util.spec_from_file_location('watchdog', Path(__file__).resolve().parents[1] / 'youtube-watchdog.py')
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ss') and hasattr(os, 'pidfd_open'),
                     'Linux, FFmpeg and iproute2 are required')
class UploadWatchdogTests(unittest.TestCase):
    def test_real_encoder_survives_traffic_then_exits_when_receiver_stalls(self):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16384)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(8)
        port = listener.getsockname()[1]
        encoder = subprocess.Popen([
            'ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-re',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30', '-c:v', 'rawvideo',
            '-f', 'rawvideo', f'tcp://127.0.0.1:{port}',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        parent_exit = os.pidfd_open(os.getpid())
        worker = None
        receiver = None
        try:
            receiver, _ = listener.accept()
            receiver.settimeout(1)
            worker = threading.Thread(target=watchdog.watch_encoder,
                                      args=(os.getpid(), encoder.pid, parent_exit),
                                      kwargs={'port': port, 'interval': 1, 'stall': 2, 'startup': 6}, daemon=True)
            worker.start()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                self.assertTrue(receiver.recv(65536))
            self.assertIsNone(encoder.poll(), 'healthy upload must not be killed')
            self.assertIsNotNone(watchdog.upload_progress(encoder.pid, port))
            # Keep the socket open but stop reading; this is the original failure mode.
            encoder.wait(timeout=12)
            self.assertLess(encoder.returncode, 0, 'stalled encoder should receive a signal')
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        finally:
            if encoder.poll() is None:
                encoder.kill()
            encoder.wait()
            if worker is not None:
                worker.join(timeout=3)
            os.close(parent_exit)
            if receiver is not None:
                receiver.close()
            listener.close()


if __name__ == '__main__':
    unittest.main()
