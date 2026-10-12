import os
import pty
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from sentinel import Dashboard, palette

class SentinelTests(unittest.TestCase):
    def test_layout_and_animation_across_terminal_sizes(self):
        for w,h in ((1,1),(40,10),(74,24),(80,24),(120,32),(164,46),(240,65)):
            d=Dashboard()
            for t in (0,8,60,300):
                d.advance(t);c=d.render(w,h,t)
                self.assertEqual(len(c.cells),h)
                self.assertTrue(all(len(row)==w for row in c.cells))
                c.ansi(palette())
    def test_packets_use_documentation_destination_ranges(self):
        d=Dashboard()
        for packet in d.packets:
            self.assertTrue(packet[2].startswith(('192.0.2.','198.51.100.','203.0.113.')))
    def test_managed_mode_restores_terminal_on_stop(self):
        master,slave=pty.openpty()
        p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('sentinel.py'))],stdin=subprocess.DEVNULL,stdout=slave,stderr=slave)
        os.close(slave)
        try:
            time.sleep(.3)
            # Drain the frame so the terminal writer can process termination.
            os.set_blocking(master,False)
            chunks=[]
            try:
                while True:chunks.append(os.read(master,65536))
            except BlockingIOError:pass
            p.send_signal(signal.SIGTERM)
            p.wait(timeout=3)
            try:
                while True:chunks.append(os.read(master,65536))
            except (OSError,BlockingIOError):pass
            output=b''.join(chunks)
            self.assertIn(b'\x1b[?1049h',output)
            self.assertIn(b'\x1b[?1049l',output)
            self.assertEqual(p.returncode,0)
        finally:
            if p.poll() is None:p.kill();p.wait()
            os.close(master)

if __name__=='__main__':unittest.main()
