"""Run against a built Windows runtime: python test_socket_wait_runtime.py PATH_TO_EXE.

The host drives loopback peers; all poll calls and assertions run in the runtime.
On Windows the CPU checks also catch a busy loop on an overflowing WSAPoll timeout.
"""

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

RUNTIME = str(Path(sys.argv.pop(1)).resolve()) if len(sys.argv) > 1 else None

SOCKET_PROBE = r'''
import select, socket, time
port, count, timeout = PORT, COUNT, TIMEOUT
sockets = []
p = select.poll()
for i in range(count):
    s = socket.socket()
    s.connect(socket.getaddrinfo('127.0.0.1', port)[0][-1])
    sockets.append(s)
    p.register(s, select.POLLIN)
print('READY')
sys.stdout.flush()
seen = set()
start = time.ticks_ms()
while len(seen) < count:
    events = p.poll(timeout)
    assert events, 'unexpected timeout'
    for s, event in events:
        assert event & select.POLLIN, event
        assert s.recv(1) == b'x'
        seen.add(sockets.index(s))
        p.unregister(s)
assert time.ticks_diff(time.ticks_ms(), start) < 4000
print('OK', len(seen))
'''

IDLE_PROBE = r'''
import select, socket, time
s = socket.socket()
s.connect(socket.getaddrinfo('127.0.0.1', PORT)[0][-1])
p = select.poll()
p.register(s, select.POLLIN)
print('READY')
sys.stdout.flush()
start = time.ticks_ms()
assert p.poll(5000) == []
elapsed = time.ticks_diff(time.ticks_ms(), start)
assert 4700 <= elapsed < 7000, elapsed
assert p.poll(0) == []
s.close()
events = p.poll(0)
assert events and events[0][1] & 0x20, events  # MP_STREAM_POLL_NVAL
print('OK', elapsed)
'''

BACKPRESSURE_PROBE = r'''
import socket
s = socket.socket()
s.connect(socket.getaddrinfo('127.0.0.1', PORT)[0][-1])
s.setblocking(False)
print('READY')
sys.stdout.flush()
# Stream methods return None rather than exposing WSAEWOULDBLOCK.
assert s.read(1) is None
assert s.readinto(bytearray(1)) is None
chunk = b'x' * 4096
for i in range(32768):
    if s.write(chunk) is None:
        print('OK backpressure')
        break
else:
    raise AssertionError('socket never applied backpressure')
s.close()
'''


def process_cpu_seconds(process):
    if os.name != 'nt':
        return None
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(int(process._handle), *(ctypes.byref(v) for v in values)):
        raise ctypes.WinError(ctypes.get_last_error())
    def ticks(v):
        return (v.dwHighDateTime << 32) | v.dwLowDateTime
    return (ticks(values[2]) + ticks(values[3])) / 10_000_000


class SocketWaitRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not RUNTIME:
            raise unittest.SkipTest('pass the runtime executable as the first argument')

    def exercise(self, script, count, timeout, idle=False, check_cpu=True):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(64)
        listener.settimeout(8)
        port = listener.getsockname()[1]
        source = 'import sys\n' + script.replace('PORT', str(port)).replace('COUNT', str(count)).replace('TIMEOUT', str(timeout))
        peers = []
        errors = []
        ready = threading.Event()
        release = threading.Event()

        def serve():
            try:
                for _ in range(count):
                    peer, _ = listener.accept()
                    peers.append(peer)
                ready.set()
                if not release.wait(8):
                    raise TimeoutError('runtime did not reach poll')
                if not idle:
                    for peer in peers:
                        peer.sendall(b'x')
                        time.sleep(0.01)
            except Exception as exc:
                errors.append(exc)
                ready.set()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        process = subprocess.Popen([RUNTIME, '-c', source], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 8
            while not ready.wait(0.05):
                if process.poll() is not None:
                    out, err = process.communicate()
                    self.fail('runtime exited before connecting: stdout=%r stderr=%r' % (out, err))
                if time.monotonic() >= deadline:
                    process.kill()
                    out, err = process.communicate()
                    self.fail('runtime did not connect: stdout=%r stderr=%r' % (out, err))
            if errors:
                if process.poll() is not None:
                    out, err = process.communicate()
                    self.fail('runtime exited before connecting: stdout=%r stderr=%r' % (out, err))
                raise errors[0]
            time.sleep(0.8)
            cpu = process_cpu_seconds(process)
            release.set()
            out, err = process.communicate(timeout=9)
            self.assertEqual(process.returncode, 0, (out, err))
            self.assertIn('READY', out)
            self.assertIn('OK', out)
            if check_cpu and cpu is not None and count <= 32:
                self.assertLess(cpu, 0.5, 'long poll spun instead of sleeping')
        finally:
            release.set()
            if process.poll() is None:
                process.kill()
                process.communicate()
            listener.close()
            thread.join(2)
            for peer in peers:
                peer.close()
        if errors:
            raise errors[0]

    def test_one_socket_long_wait(self):
        self.exercise(SOCKET_PROBE, 1, 60000)

    def test_eight_sockets(self):
        self.exercise(SOCKET_PROBE, 8, 60000)

    def test_thirty_three_sockets(self):
        self.exercise(SOCKET_PROBE, 33, 60000)

    def test_timeout_above_int_max(self):
        self.exercise(SOCKET_PROBE, 1, 2147483648)

    def test_timeout_and_closed_socket(self):
        self.exercise(IDLE_PROBE, 1, 5000, idle=True)

    def test_nonblocking_stream_backpressure(self):
        self.exercise(BACKPRESSURE_PROBE, 1, 0, idle=True, check_cpu=False)

    def test_asyncio_refused_connect(self):
        # Keep the port reserved but not listening so the connection is refused.
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
            source = '''
import asyncio
async def main():
    try:
        await asyncio.wait_for_ms(asyncio.open_connection('127.0.0.1', PORT), 3000)
    except OSError as error:
        assert error.errno == 10061, error
        print('OK refused')
    else:
        raise AssertionError('refused connection succeeded')
asyncio.run(main())
'''.replace('PORT', str(port))
            result = subprocess.run([RUNTIME, '-c', source], capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
            self.assertIn('OK refused', result.stdout)


if __name__ == '__main__':
    unittest.main()
