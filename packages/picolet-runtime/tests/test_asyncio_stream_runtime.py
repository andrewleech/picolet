"""Exercise MicroPython asyncio with real pipes and loopback TCP.

Run with host Python: python tests/test_asyncio_stream_runtime.py PATH_TO_EXE
The executable may be a Windows build invoked through WSL interop. The host
only drives the pipes and TCP peer; the asyncio operations run in the runtime.
"""

import ctypes
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


RUNTIME = str(Path(sys.argv.pop(1)).resolve()) if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else None
TIMEOUT = 15

# Substitute only the port, leaving all stream and scheduler work in MicroPython.
PROBE = r'''
import asyncio
import sys
import time

async def main():
    stdin = asyncio.StreamReader(sys.stdin.buffer)
    stdout = asyncio.StreamWriter(sys.stdout.buffer)
    reader, writer = await asyncio.open_connection('127.0.0.1', PORT)
    reader2, writer2 = await asyncio.open_connection('127.0.0.1', PORT)
    reader3, writer3 = await asyncio.open_connection('127.0.0.1', PORT)

    async def report(line):
        stdout.write(line + b'\n')
        await stdout.drain()

    async def read_line(stream, completed):
        line = await stream.readline()
        completed.append(line)
        return line

    writer.write(b'first\n')
    await writer.drain()
    writer.write(b'echo-one\n')
    await writer.drain()
    writer2.write(b'echo-two\n')
    await writer2.drain()
    writer3.write(b'echo-three\n')
    await writer3.drain()
    input_done = []
    socket_done = []
    echo2_done = []
    echo3_done = []
    input_task = asyncio.create_task(read_line(stdin, input_done))
    socket_task = asyncio.create_task(read_line(reader, socket_done))
    echo2_task = asyncio.create_task(read_line(reader2, echo2_done))
    echo3_task = asyncio.create_task(read_line(reader3, echo3_done))
    # All socket reads and stdin must be registered while the loop sleeps.
    await asyncio.sleep_ms(30)
    await report(b'WAITING')
    start = time.ticks_ms()
    await asyncio.sleep_ms(1000)
    elapsed = time.ticks_diff(time.ticks_ms(), start)
    assert elapsed >= 900, elapsed
    assert not input_done and not socket_done and not echo2_done and not echo3_done, (
        input_done, socket_done, echo2_done, echo3_done
    )
    await report(b'IDLE')

    assert await input_task == b'input-one\n'
    await report(b'STDIN1')
    # Cancel an outstanding socket read and an outstanding stdin read, then
    # re-register both streams. No data is sent until cancellation completes.
    socket_task.cancel()
    try:
        await socket_task
        assert False, 'socket read was not cancelled'
    except asyncio.CancelledError:
        pass
    cancelled_input = asyncio.create_task(read_line(stdin, []))
    await asyncio.sleep_ms(30)
    cancelled_input.cancel()
    try:
        await cancelled_input
        assert False, 'stdin read was not cancelled'
    except asyncio.CancelledError:
        pass
    socket_task = asyncio.create_task(read_line(reader, socket_done))
    input_task = asyncio.create_task(read_line(stdin, []))
    await asyncio.sleep_ms(30)
    await report(b'CANCELLED')
    assert await socket_task == b'socket-one\n'
    await report(b'SOCKET1')
    assert await reader.readline() == b'echo-one\n'
    assert await echo2_task == b'echo-two\n'
    assert await echo3_task == b'echo-three\n'
    # Register an EOF read before allowing the host to terminate the peer.
    eof_done = []
    eof_task = asyncio.create_task(read_line(reader, eof_done))
    await asyncio.sleep_ms(30)
    assert not eof_done, eof_done
    await report(b'ECHOES')
    assert await eof_task == b''
    await report(b'PEER_CLOSED')
    writer.close()
    await writer.wait_closed()

    # A fresh connection must work after the old stream was cancelled/closed.
    reader, writer = await asyncio.open_connection('127.0.0.1', PORT)
    writer.write(b'second\n')
    await writer.drain()
    socket_task = asyncio.create_task(read_line(reader, []))
    await asyncio.sleep_ms(30)
    await report(b'RECONNECTED')
    assert await input_task == b'input-two\n'
    await report(b'STDIN2')
    assert await socket_task == b'socket-two\n'
    await report(b'SOCKET2')
    writer.close()
    await writer.wait_closed()
    writer2.close()
    await writer2.wait_closed()
    writer3.close()
    await writer3.wait_closed()
    await report(b'OK')

asyncio.run(main())
'''


def process_cpu_seconds(process):
    """Windows process CPU time; WSL interop's Linux PID is only a relay."""
    if os.name != 'nt':
        return None
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(int(process._handle), *(ctypes.byref(v) for v in values)):
        raise ctypes.WinError(ctypes.get_last_error())

    def ticks(value):
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    return (ticks(values[2]) + ticks(values[3])) / 10_000_000


class AsyncioStreamRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not RUNTIME:
            raise unittest.SkipTest('pass the runtime executable as the first argument')

    def test_mixed_streams_cancellation_reconnect_and_idle(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(3)
        listener.settimeout(TIMEOUT)
        port = listener.getsockname()[1]
        first_reply = threading.Event()
        second_reply = threading.Event()
        close_first_peer = threading.Event()
        errors = []
        lines = queue.Queue()
        output = []

        def receive_line(peer):
            data = bytearray()
            while not data.endswith(b'\n'):
                chunk = peer.recv(1)
                if not chunk:
                    raise EOFError('runtime closed the socket before sending a line')
                data.extend(chunk)
                if len(data) > 256:
                    raise ValueError('unexpectedly long socket request')
            return bytes(data)

        def serve():
            try:
                with listener.accept()[0] as peer, listener.accept()[0] as peer2, listener.accept()[0] as peer3:
                    for connection in (peer, peer2, peer3):
                        connection.settimeout(TIMEOUT)
                    request = receive_line(peer)
                    if request != b'first\n':
                        raise AssertionError(('first socket request', request))
                    for connection, expected in (
                        (peer, b'echo-one\n'),
                        (peer2, b'echo-two\n'),
                        (peer3, b'echo-three\n'),
                    ):
                        request = receive_line(connection)
                        if request != expected:
                            raise AssertionError(('echo socket request', expected, request))
                    if not first_reply.wait(TIMEOUT):
                        raise TimeoutError('first socket reply was not released')
                    peer.sendall(b'socket-one\n')
                    # Reply in a different order from registration.
                    peer3.sendall(b'echo-three\n')
                    peer2.sendall(b'echo-two\n')
                    peer.sendall(b'echo-one\n')
                    if not close_first_peer.wait(TIMEOUT):
                        raise TimeoutError('first peer termination was not released')
                    peer.shutdown(socket.SHUT_WR)
                    with listener.accept()[0] as reconnected:
                        reconnected.settimeout(TIMEOUT)
                        request = receive_line(reconnected)
                        if request != b'second\n':
                            raise AssertionError(('second socket request', request))
                        if not second_reply.wait(TIMEOUT):
                            raise TimeoutError('second socket reply was not released')
                        reconnected.sendall(b'socket-two\n')
            except Exception as exc:
                errors.append(exc)

        def collect_stdout(stream):
            try:
                for line in iter(stream.readline, b''):
                    output.append(line)
                    lines.put(line.rstrip(b'\r\n'))
            finally:
                lines.put(None)

        def expect(expected):
            try:
                actual = lines.get(timeout=TIMEOUT)
            except queue.Empty:
                self.fail('timed out waiting for %r; stdout=%r; server errors=%r' % (expected, output, errors))
            self.assertEqual(actual, expected, (output, errors))

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        process = None
        reader_thread = None
        with tempfile.TemporaryFile() as stderr:
            try:
                source = PROBE.replace('PORT', str(port))
                process = subprocess.Popen(
                    [RUNTIME, '-c', source], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=stderr,
                )
                reader_thread = threading.Thread(target=collect_stdout, args=(process.stdout,), daemon=True)
                reader_thread.start()
                expect(b'WAITING')
                cpu_start = process_cpu_seconds(process)
                expect(b'IDLE')
                cpu_end = process_cpu_seconds(process)
                if cpu_start is not None:
                    self.assertLess(cpu_end - cpu_start, 0.5, 'idle event loop spun instead of sleeping')
                process.stdin.write(b'input-one\n')
                process.stdin.flush()
                expect(b'STDIN1')
                expect(b'CANCELLED')
                first_reply.set()
                expect(b'SOCKET1')
                expect(b'ECHOES')
                close_first_peer.set()
                expect(b'PEER_CLOSED')
                expect(b'RECONNECTED')
                process.stdin.write(b'input-two\n')
                process.stdin.flush()
                expect(b'STDIN2')
                second_reply.set()
                expect(b'SOCKET2')
                expect(b'OK')
                process.stdin.close()
                process.wait(timeout=TIMEOUT)
                stderr.seek(0)
                self.assertEqual(process.returncode, 0, (output, stderr.read()))
            finally:
                close_first_peer.set()
                first_reply.set()
                second_reply.set()
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=TIMEOUT)
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                    if reader_thread is not None:
                        reader_thread.join(TIMEOUT)
                    if process.stdout:
                        process.stdout.close()
                listener.close()
                server.join(TIMEOUT)
            self.assertFalse(server.is_alive(), 'host server did not exit')
            self.assertFalse(reader_thread.is_alive(), 'stdout reader did not exit')
            if errors:
                raise errors[0]


if __name__ == '__main__':
    unittest.main()
