"""Async one-shot HTTP/1.1 transport for the loopback MCP server."""

import asyncio

CHUNK = 16384


class HttpConn:
    def __init__(self, port, method, path, headers, body=None):
        self.port, self.method, self.path = port, method, path
        self.headers, self.body = headers, body
        self.reader = self.writer = None
        self.status = 0
        self.reason = ""
        self.response_headers = {}
        self.sent = asyncio.Event()

    async def open(self):
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.port)
        lines = [self.method + " " + self.path + " HTTP/1.1", "Host: 127.0.0.1:" + str(self.port), "Connection: close"]
        for key in self.headers:
            lines.append(key + ": " + self.headers[key])
        if self.body is not None:
            lines.append("Content-Length: " + str(len(self.body)))
        self.writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
        await self.writer.drain()
        if self.body is not None:
            view = memoryview(self.body)
            for off in range(0, len(view), CHUNK):
                self.writer.write(view[off:off + CHUNK])
                await self.writer.drain()
        self.sent.set()
        line = await self.reader.readline()
        parts = line.decode("utf-8").strip().split(" ", 2)
        if len(parts) < 2 or not parts[0].startswith("HTTP/"):
            raise ValueError("invalid HTTP status line")
        self.status = int(parts[1])
        self.reason = parts[2] if len(parts) > 2 else ""
        while True:
            line = await self.reader.readline()
            if not line:
                raise OSError("connection closed in HTTP headers")
            if line in (b"\r\n", b"\n"):
                break
            key, sep, value = line.decode("utf-8").partition(":")
            if not sep:
                raise ValueError("invalid HTTP header")
            self.response_headers[key.strip().lower()] = value.strip()

    async def read_body(self):
        if not hasattr(self, "_mode"):
            if self.method == "HEAD" or self.status in (204, 304) or 100 <= self.status < 200:
                self._mode = "done"
            elif "chunked" in self.response_headers.get("transfer-encoding", "").lower():
                self._mode, self._left = "chunked", 0
            elif "content-length" in self.response_headers:
                self._mode = "length"
                self._left = int(self.response_headers["content-length"])
                if self._left < 0:
                    raise ValueError("invalid HTTP content length")
            else:
                self._mode = "eof"
        if self._mode == "done":
            return b""
        if self._mode == "chunked" and not self._left:
            line = await self.reader.readline()
            if not line:
                raise OSError("connection closed mid-chunk")
            self._left = int(line.split(b";", 1)[0].strip(), 16)
            if self._left < 0:
                raise ValueError("invalid HTTP chunk size")
            if not self._left:
                while True:
                    line = await self.reader.readline()
                    if not line:
                        raise OSError("connection closed in HTTP trailers")
                    if line in (b"\r\n", b"\n"):
                        self._mode = "done"
                        return b""
        if self._mode == "length" and not self._left:
            self._mode = "done"
            return b""
        size = CHUNK if self._mode == "eof" else min(self._left, CHUNK)
        data = await self.reader.read(size)
        if not data:
            if self._mode == "eof":
                self._mode = "done"
                return b""
            raise OSError("connection closed mid-body")
        if self._mode != "eof":
            self._left -= len(data)
            if self._mode == "chunked" and not self._left:
                if await self.reader.readline() != b"\r\n":
                    raise ValueError("invalid HTTP chunk terminator")
        return data

    async def close(self):
        self.sent.set()
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
