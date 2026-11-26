"""Exercise native Windows poll wakeups with a built runtime.

Run with Windows Python: python test_wake_runtime.py PATH_TO_WINDOWS_EXE
From WSL, the same command delegates to python.exe when Windows interop and a
Windows-accessible copy of this script and the runtime are available.
"""

import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest


RUNTIME = None
WAIT_SECONDS = 8

# A real socket forces the WSAPoll path; an empty set uses the event wait.
POLL_SETUP = r'''
import select, socket, sys
p = select.poll()
if PORT:
    sock = socket.socket()
    sock.connect(socket.getaddrinfo('127.0.0.1', PORT)[0][-1])
    p.register(sock, select.POLLIN)
'''

SCHEDULE_PROBE = POLL_SETUP + r'''
try:
    import _thread, micropython, time
    schedule = micropython.schedule
except (ImportError, AttributeError):
    sys.stderr.write('SKIP: runtime has no thread scheduler\n')
    sys.stderr.flush()
    sys.exit(0)

def callback(arg):
    sys.stderr.write('WOKEN\n')
    sys.stderr.flush()

def worker():
    time.sleep_ms(700)
    schedule(callback, None)

_thread.start_new_thread(worker, ())
sys.stderr.write('READY\n')
sys.stderr.flush()
events = p.poll(60000)
assert PORT and events and events[0][0] is sock and events[0][1] & select.POLLIN, events
assert sock.recv(1) == b'x'
sys.stderr.write('DONE\n')
sys.stderr.flush()
'''

INTERRUPT_PROBE = POLL_SETUP + r'''
sys.stderr.write('READY\n')
sys.stderr.flush()
try:
    p.poll(60000)
except KeyboardInterrupt:
    sys.stderr.write('INTERRUPTED\n')
    sys.stderr.flush()
else:
    raise AssertionError('poll returned without KeyboardInterrupt')
'''

# A separate helper attaches to the runtime's private console, so generating a
# CTRL_C_EVENT never detaches or signals the test runner's own console.
SEND_CTRL_C = r'''
import ctypes, sys
from ctypes import wintypes
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.FreeConsole.restype = wintypes.BOOL
k.AttachConsole.argtypes = [wintypes.DWORD]
k.AttachConsole.restype = wintypes.BOOL
k.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
k.SetConsoleCtrlHandler.restype = wintypes.BOOL
k.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
k.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
k.FreeConsole()
if not k.AttachConsole(int(sys.argv[1])):
    sys.exit('AttachConsole failed: %s' % ctypes.get_last_error())
if not k.SetConsoleCtrlHandler(None, True):
    sys.exit('SetConsoleCtrlHandler failed: %s' % ctypes.get_last_error())
if not k.GenerateConsoleCtrlEvent(0, 0):
    sys.exit('GenerateConsoleCtrlEvent failed: %s' % ctypes.get_last_error())
'''


class Runtime:
    def __init__(self, source, new_console=False):
        flags = subprocess.CREATE_NEW_CONSOLE if new_console else 0
        try:
            self.process = subprocess.Popen(
                [RUNTIME, '-c', source], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
            )
        except OSError as exc:
            if new_console and getattr(exc, 'winerror', None) in (5, 50, 87):
                raise unittest.SkipTest('cannot create a Windows console: %s' % exc)
            raise
        self.messages = queue.Queue()
        self.stderr = bytearray()

        def read_stderr():
            for line in iter(self.process.stderr.readline, b''):
                self.stderr.extend(line)
                self.messages.put(line.rstrip(b'\r\n'))
            self.messages.put(None)

        self.reader = threading.Thread(target=read_stderr, daemon=True)
        self.reader.start()

    def expect(self, expected):
        try:
            line = self.messages.get(timeout=WAIT_SECONDS)
        except queue.Empty:
            raise AssertionError('timed out waiting for %r; stderr=%r' %
                                 (expected, bytes(self.stderr))) from None
        if line and line.startswith(b'SKIP: '):
            raise unittest.SkipTest(line.decode('utf-8', 'replace'))
        if line != expected:
            raise AssertionError('expected %r, got %r; stderr=%r' %
                                 (expected, line, bytes(self.stderr)))

    def finish(self):
        self.process.wait(timeout=WAIT_SECONDS)
        self.reader.join(WAIT_SECONDS)
        if self.reader.is_alive() or self.process.returncode:
            raise AssertionError('runtime exit=%r, stderr=%r' %
                                 (self.process.returncode, bytes(self.stderr)))

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=WAIT_SECONDS)
        self.reader.join(WAIT_SECONDS)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


class WakeRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt':
            raise unittest.SkipTest('requires native Windows Python and runtime')
        if not RUNTIME:
            raise unittest.SkipTest('pass the Windows runtime executable as the first argument')

    def start(self, probe, with_socket, new_console=False):
        port = 0
        if with_socket:
            listener = socket.socket()
            self.addCleanup(listener.close)
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(WAIT_SECONDS)
            port = listener.getsockname()[1]
        runtime = Runtime(probe.replace('PORT', str(port)), new_console)
        self.addCleanup(runtime.close)
        runtime.expect(b'READY')
        if with_socket:
            peer, _ = listener.accept()
            self.addCleanup(peer.close)
            runtime.peer = peer
        return runtime

    def test_scheduler_wakes_long_poll(self):
        for with_socket in (False, True):
            with self.subTest(socket=with_socket):
                runtime = self.start(SCHEDULE_PROBE, with_socket)
                runtime.expect(b'WOKEN')
                self.assertIsNone(runtime.process.poll(), 'poll returned on scheduler wake')
                if with_socket:
                    runtime.peer.sendall(b'x')
                    runtime.expect(b'DONE')
                    runtime.finish()

    def test_ctrl_c_wakes_long_poll(self):
        for with_socket in (False, True):
            with self.subTest(socket=with_socket):
                runtime = self.start(INTERRUPT_PROBE, with_socket, new_console=True)
                # READY precedes poll; give the runtime time to enter its wait.
                time.sleep(0.5)
                try:
                    result = subprocess.run(
                        [sys.executable, '-c', SEND_CTRL_C, str(runtime.process.pid)],
                        capture_output=True, text=True, timeout=WAIT_SECONDS,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except OSError as exc:
                    self.skipTest('cannot start console event helper: %s' % exc)
                if result.returncode:
                    self.skipTest('cannot generate console Ctrl-C: %s' % result.stderr.strip())
                runtime.expect(b'INTERRUPTED')
                runtime.finish()


def run_from_wsl():
    """Run the complete harness under Windows Python, not Linux's signal API."""
    if not os.environ.get('WSL_INTEROP'):
        return None
    python = shutil.which('python.exe')
    wslpath = shutil.which('wslpath')
    if not python or not wslpath or len(sys.argv) < 2:
        return None
    try:
        paths = [subprocess.check_output([wslpath, '-w', path], text=True,
                                         timeout=WAIT_SECONDS).strip()
                 for path in (os.path.abspath(__file__), sys.argv[1])]
    except (OSError, subprocess.SubprocessError):
        return None
    return subprocess.run([python, paths[0], paths[1], *sys.argv[2:]],
                          timeout=120).returncode


if __name__ == '__main__':
    if os.name != 'nt':
        result = run_from_wsl()
        if result is not None:
            sys.exit(result)
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        RUNTIME = os.path.abspath(sys.argv.pop(1))
    unittest.main()
