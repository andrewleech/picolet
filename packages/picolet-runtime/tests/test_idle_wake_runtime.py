"""Measure a native Windows runtime's idle poll CPU usage.

Run with Windows Python: python test_idle_wake_runtime.py PATH_TO_RUNTIME_EXE
The runtime is a private child with redirected stdio and a private loopback peer;
no existing runtime, hub, or Desktop process is inspected or signalled.

GetProcessTimes supplies process CPU time, not a count of scheduler wakeups.
The documented GetProcessInformation classes and Process performance counters
do not expose a per-process scheduler wakeup count. Context switches are not
equivalent: a thread can be preempted and rescheduled without a wait ending.
No supported child-attributed wakeup counter was found under this test's
no-admin, no-system-tracing constraints. Low CPU usage alone therefore does
not rule out frequent, inexpensive wakeups.

https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ne-processthreadsapi-process_information_class
https://learn.microsoft.com/en-us/previous-versions/aa394323(v=vs.85)
"""

import ctypes
from ctypes import wintypes
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest


RUNTIME = None
POLL_MS = 30000
TIMEOUT_SECONDS = 45

PROBE = r'''
import select, socket, sys, time
sock = socket.socket()
sock.connect(socket.getaddrinfo('127.0.0.1', PORT)[0][-1])
p = select.poll()
p.register(sock, select.POLLIN)
p.register(sys.stdin, select.POLLIN)
sys.stderr.write('READY\n')
sys.stderr.flush()
events = p.poll(45000)
assert any(obj is sys.stdin and flags & select.POLLIN for obj, flags in events), events
assert sys.stdin.buffer.read(1) == b's'
start = time.ticks_ms()
events = p.poll(30000)
elapsed = time.ticks_diff(time.ticks_ms(), start)
assert not events, events
assert elapsed >= 29500, elapsed
sys.stderr.write('DONE %d\n' % elapsed)
sys.stderr.flush()
events = p.poll(45000)
assert any(obj is sys.stdin and flags & select.POLLIN for obj, flags in events), events
assert sys.stdin.buffer.read(1) == b'q'
sock.close()
'''


def process_cpu_100ns(process):
    """Return kernel + user CPU time in Windows FILETIME 100 ns units."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(int(process._handle), *(ctypes.byref(value) for value in values)):
        raise ctypes.WinError(ctypes.get_last_error())

    def ticks(value):
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    return ticks(values[2]) + ticks(values[3])


class IdleWakeRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt':
            raise unittest.SkipTest('requires native Windows Python and runtime')
        if not RUNTIME:
            raise unittest.SkipTest('pass the Windows runtime executable as the first argument')

    def test_idle_stdio_and_socket_poll_cpu(self):
        with socket.socket() as listener, tempfile.TemporaryFile() as stdout:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(TIMEOUT_SECONDS)
            source = PROBE.replace('PORT', str(listener.getsockname()[1]))
            process = subprocess.Popen(
                [RUNTIME, '-c', source], stdin=subprocess.PIPE,
                stdout=stdout, stderr=subprocess.PIPE, bufsize=0,
            )
            messages = queue.Queue()
            stderr = bytearray()

            def collect():
                for line in iter(process.stderr.readline, b''):
                    stderr.extend(line)
                    messages.put(line.rstrip(b'\r\n'))
                messages.put(None)

            reader = threading.Thread(target=collect, daemon=True)
            reader.start()

            def receive(timeout):
                try:
                    line = messages.get(timeout=timeout)
                    if line is None:
                        process.wait(timeout=TIMEOUT_SECONDS)
                        stdout.seek(0)
                        self.fail('runtime exited %s; stderr=%r; stdout=%r' %
                                  (process.returncode, bytes(stderr), stdout.read()))
                    return line
                except queue.Empty:
                    self.fail('runtime timed out; stderr=%r' % bytes(stderr))

            try:
                with listener.accept()[0]:
                    self.assertEqual(receive(TIMEOUT_SECONDS), b'READY', bytes(stderr))
                    cpu_start = process_cpu_100ns(process)
                    wall_start = time.monotonic()
                    process.stdin.write(b's')
                    done = receive(TIMEOUT_SECONDS)
                    wall_seconds = time.monotonic() - wall_start
                    cpu_ticks = process_cpu_100ns(process) - cpu_start
                    self.assertIsNotNone(done, bytes(stderr))
                    self.assertTrue(done.startswith(b'DONE '), bytes(stderr))
                    poll_ms = int(done.split()[1])
                    print('idle poll: %d ms; wall: %.3f s; process CPU: %d x 100 ns (%.6f s); '
                          'wakeup count: unavailable from GetProcessTimes' %
                          (poll_ms, wall_seconds, cpu_ticks, cpu_ticks / 10_000_000), flush=True)
                    self.assertGreaterEqual(poll_ms, POLL_MS - 500)
                    self.assertLess(cpu_ticks, 10_000_000,
                                    '30-second idle poll used at least 1 CPU second')
                    process.stdin.write(b'q')
                    process.wait(timeout=TIMEOUT_SECONDS)
                    stdout.seek(0)
                    self.assertEqual(process.returncode, 0, (bytes(stderr), stdout.read()))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=TIMEOUT_SECONDS)
                process.stdin.close()
                reader.join(TIMEOUT_SECONDS)
                process.stderr.close()


if __name__ == '__main__':
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        RUNTIME = os.path.abspath(sys.argv.pop(1))
    unittest.main()
