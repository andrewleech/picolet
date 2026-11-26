"""Run with host Python: python test_stdio_wait_runtime.py PATH_TO_WINDOWS_EXE.

The host supplies real redirected pipes and loopback sockets; polling and stream
operations are performed inside the built MicroPython runtime.
"""

import queue
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

RUNTIME = str(Path(sys.argv.pop(1)).resolve()) if len(sys.argv) > 1 else None
TIMEOUT = 15


class Runtime:
    def __init__(self, source):
        self.process = subprocess.Popen(
            [RUNTIME, '-c', source], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )
        self.messages = queue.Queue()
        self.stderr = bytearray()

        def read_stderr():
            for line in iter(self.process.stderr.readline, b''):
                self.stderr.extend(line)
                self.messages.put(line.rstrip(b'\r\n'))

        self.reader = threading.Thread(target=read_stderr, daemon=True)
        self.reader.start()

    def expect(self, message):
        try:
            line = self.messages.get(timeout=TIMEOUT)
        except queue.Empty:
            raise AssertionError('runtime did not report %r; stderr=%r' %
                                 (message, bytes(self.stderr))) from None
        if line != message:
            raise AssertionError('expected %r, got %r; stderr=%r' %
                                 (message, line, bytes(self.stderr)))

    def finish(self):
        self.process.wait(timeout=TIMEOUT)
        self.reader.join(TIMEOUT)
        if self.reader.is_alive():
            raise AssertionError('stderr reader did not finish')
        if self.process.returncode:
            raise AssertionError('runtime exited %s: %r' %
                                 (self.process.returncode, bytes(self.stderr)))

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=TIMEOUT)
        for stream in (self.process.stdin, self.process.stdout):
            if not stream.closed:
                stream.close()
        self.reader.join(TIMEOUT)
        self.process.stderr.close()


class StdioWaitRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not RUNTIME:
            raise unittest.SkipTest('pass the runtime executable as the first argument')

    def start(self, source):
        runtime = Runtime(source)
        self.addCleanup(runtime.close)
        return runtime

    def test_stdin_wakes_long_poll_promptly(self):
        runtime = self.start(r'''
import select, sys
p = select.poll()
p.register(sys.stdin, select.POLLIN)
sys.stderr.write('WAITING\n')
events = p.poll(10000)
assert events and events[0][1] & select.POLLIN, events
assert sys.stdin.buffer.read(1) == b'x'
sys.stderr.write('WOKEN\n')
''')
        runtime.expect(b'WAITING')
        # Give the runtime an opportunity to enter the long blocking poll before
        # writing; there are no periodic timers or short polls in the runtime.
        time.sleep(0.2)
        started = time.monotonic()
        runtime.process.stdin.write(b'x')
        try:
            message = runtime.messages.get(timeout=2)
        except queue.Empty:
            elapsed = time.monotonic() - started
            self.fail('stdin poll did not wake within %.3fs; stderr=%r' %
                      (elapsed, bytes(runtime.stderr)))
        elapsed = time.monotonic() - started
        self.assertEqual(message, b'WOKEN', bytes(runtime.stderr))
        self.assertLess(elapsed, 2, 'stdin poll wake took %.3fs' % elapsed)
        runtime.finish()

    def test_partial_input_and_eof(self):
        runtime = self.start(r'''
import select, sys
p = select.poll()
p.register(sys.stdin, select.POLLIN)
assert not p.poll(100), 'empty pipe was ready'
sys.stderr.write('EMPTY\n')
events = p.poll(10000)
assert events and events[0][1] & select.POLLIN, events
assert sys.stdin.buffer.read(1) == b'a'
assert p.poll(10000), 'remaining byte not ready'
assert sys.stdin.buffer.read(1) == b'b'
sys.stderr.write('PARTIAL\n')
events = p.poll(10000)
assert events and events[0][1] & (select.POLLIN | select.POLLHUP), events
assert sys.stdin.buffer.read(1) == b''
sys.stderr.write('EOF\n')
''')
        runtime.expect(b'EMPTY')
        runtime.process.stdin.write(b'ab')
        runtime.expect(b'PARTIAL')
        runtime.process.stdin.close()
        runtime.expect(b'EOF')
        runtime.finish()

    def test_one_megabyte_input(self):
        runtime = self.start(r'''
import select, sys
p = select.poll()
p.register(sys.stdin, select.POLLIN)
sys.stderr.write('READY\n')
expected = b'0123456789abcdef'
count = 0
while True:
    events = p.poll(10000)
    assert events and events[0][1] & (select.POLLIN | select.POLLHUP), events
    chunk = sys.stdin.buffer.read(4096)
    if chunk is None:
        continue
    if not chunk:
        break
    for b in chunk:
        assert b == expected[count % 16], count
        count += 1
assert count == 1048576, count
sys.stderr.write('OK\n')
''')
        runtime.expect(b'READY')
        errors = []

        def send():
            try:
                payload = b'0123456789abcdef' * 65536
                view = memoryview(payload)
                while view:
                    n = runtime.process.stdin.write(view)
                    if not n:
                        raise OSError('stdin pipe made no progress')
                    view = view[n:]
                runtime.process.stdin.close()
            except Exception as exc:
                errors.append(exc)

        writer = threading.Thread(target=send, daemon=True)
        writer.start()
        runtime.expect(b'OK')
        writer.join(TIMEOUT)
        self.assertFalse(writer.is_alive(), 'stdin writer stalled')
        self.assertFalse(errors, errors)
        runtime.finish()

    def test_socket_and_stdin_share_poll(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(TIMEOUT)
        runtime = self.start(r'''
import select, socket, sys
s = socket.socket()
s.connect(socket.getaddrinfo('127.0.0.1', %d)[0][-1])
p = select.poll()
p.register(sys.stdin, select.POLLIN)
p.register(s, select.POLLIN)
assert not p.poll(100), 'unexpected initial readiness'
sys.stderr.write('READY\n')
events = p.poll(10000)
assert any(obj is sys.stdin and flags & select.POLLIN for obj, flags in events), events
assert sys.stdin.buffer.read(1) == b'i'
sys.stderr.write('INPUT\n')
events = p.poll(10000)
assert any(obj is s and flags & select.POLLIN for obj, flags in events), events
assert s.recv(1) == b's'
sys.stderr.write('SOCKET\n')
''' % listener.getsockname()[1])
        peer, _ = listener.accept()
        self.addCleanup(peer.close)
        peer.settimeout(TIMEOUT)
        runtime.expect(b'READY')
        runtime.process.stdin.write(b'i')
        runtime.expect(b'INPUT')
        peer.sendall(b's')
        runtime.expect(b'SOCKET')
        runtime.finish()

    def test_stdout_backpressure_order_and_exit_flush(self):
        runtime = self.start(r'''
import select, sys
out = sys.stdout.buffer
p = select.poll()
p.register(sys.stdout, select.POLLOUT)
block = b'0123456789abcdef' * 256
sys.stderr.write('READY\n')
for i in range(256):
    offset = 0
    while offset < len(block):
        n = out.write(block[offset:])
        if n:
            offset += n
        else:
            sys.stderr.write('STALLED\n')
            events = p.poll(10000)
            assert events and events[0][1] & select.POLLOUT, events
sys.stderr.write('WRITTEN\n')
''')
        runtime.expect(b'READY')
        runtime.expect(b'STALLED')
        self.assertIsNone(runtime.process.poll(), 'runtime exited before reader resumed')
        output = bytearray()
        errors = []

        def drain():
            try:
                while True:
                    chunk = runtime.process.stdout.read(65536)
                    if not chunk:
                        break
                    output.extend(chunk)
            except Exception as exc:
                errors.append(exc)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        # Additional STALLED notifications are possible before the reader catches up.
        while True:
            try:
                message = runtime.messages.get(timeout=TIMEOUT)
            except queue.Empty:
                self.fail('runtime did not finish writing: %r' % bytes(runtime.stderr))
            if message == b'WRITTEN':
                break
            self.assertEqual(message, b'STALLED')
        runtime.finish()
        reader.join(TIMEOUT)
        self.assertFalse(reader.is_alive(), 'stdout reader stalled')
        self.assertFalse(errors, errors)
        self.assertEqual(output, b'0123456789abcdef' * 65536)


if __name__ == '__main__':
    unittest.main()
