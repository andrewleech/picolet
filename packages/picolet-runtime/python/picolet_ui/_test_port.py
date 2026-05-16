# picolet_ui._test_port — pick a free 127.0.0.1 port for PICOLET_TEST_MODE.
#
# PH17.  Binds a TCP socket to 127.0.0.1:0, reads back the kernel-assigned
# port via getsockname(2), then closes the socket.  The caller immediately
# uses the port number as --remote-debugging-port or WEBKIT_INSPECTOR_SERVER.
#
# The race window between close and the engine re-binding that port is
# microseconds (no TIME_WAIT on a never-accepted listen socket).  The
# AppHarness wait-for-ready loop retries on connect failure for up to 10 s
# as the documented mitigation (Open question O2 / D4).
#
# Platform: MicroPython unix port.  We can't `import socket` (the runtime
# disables MICROPY_PY_SOCKET) so we call the POSIX socket/bind/getsockname/
# close syscalls directly through the libffi-openable libc.so.6.
#
# uctypes layout for struct sockaddr_in (Linux x86-64):
#   offset 0:  uint16  sin_family   (AF_INET = 2)
#   offset 2:  uint16  sin_port     (big-endian)
#   offset 4:  uint32  sin_addr     (big-endian 127.0.0.1 = 0x0100007f)
#   offset 8:  8 bytes padding
#   total: 16 bytes

import uctypes

AF_INET    = 2
SOCK_STREAM = 1

# In network byte order: 127.0.0.1 = 0x7f000001; stored big-endian in memory.
_LOOPBACK_ADDR = 0x7f000001  # big-endian uint32


def _open_libc():
    import ffi
    for name in ("libc.so.6", "libc.so", "libc.so.0"):
        try:
            return ffi.open(name)
        except OSError:
            pass
    raise ImportError("picolet_ui._test_port: cannot open libc")


def _make_funcs():
    libc = _open_libc()
    # int socket(int domain, int type, int protocol)
    sock_f = libc.func("i", "socket", "iii")
    # int bind(int sockfd, const struct sockaddr *addr, socklen_t addrlen)
    bind_f = libc.func("i", "bind", "ipi")
    # int listen(int sockfd, int backlog)
    listen_f = libc.func("i", "listen", "ii")
    # int getsockname(int sockfd, struct sockaddr *addr, socklen_t *addrlen)
    getsock_f = libc.func("i", "getsockname", "ipp")
    # int close(int fd)
    close_f = libc.func("i", "close", "i")
    return sock_f, bind_f, listen_f, getsock_f, close_f


def pick_test_port():
    """Return a free 127.0.0.1 TCP port as an int, or raise RuntimeError.

    The port is chosen by the OS (bind port 0) and is valid for a brief
    window after this function returns.  The caller should use it
    immediately to configure the engine's debug listener.
    """
    sock_f, bind_f, listen_f, getsock_f, close_f = _make_funcs()

    fd = sock_f(AF_INET, SOCK_STREAM, 0)
    if fd < 0:
        raise RuntimeError("picolet_ui._test_port: socket() failed")

    # Build a struct sockaddr_in (16 bytes) in a bytearray.
    addr_buf = bytearray(16)
    # sin_family = AF_INET = 2 (little-endian uint16 at offset 0)
    addr_buf[0] = 2
    addr_buf[1] = 0
    # sin_port = 0 (network byte order; 0 asks the OS to assign)
    addr_buf[2] = 0
    addr_buf[3] = 0
    # sin_addr = 127.0.0.1 in network byte order = 0x7f000001
    addr_buf[4] = 0x7f
    addr_buf[5] = 0x00
    addr_buf[6] = 0x00
    addr_buf[7] = 0x01

    addr_ptr = uctypes.addressof(addr_buf)

    if bind_f(fd, addr_ptr, 16) != 0:
        close_f(fd)
        raise RuntimeError("picolet_ui._test_port: bind() failed")

    if listen_f(fd, 1) != 0:
        close_f(fd)
        raise RuntimeError("picolet_ui._test_port: listen() failed")

    # getsockname needs a socklen_t * — put the value 16 in a uint32 array.
    addrlen_buf = bytearray(4)
    addrlen_buf[0] = 16
    addrlen_buf[1] = 0
    addrlen_buf[2] = 0
    addrlen_buf[3] = 0

    result_buf = bytearray(16)
    result_ptr = uctypes.addressof(result_buf)
    addrlen_ptr = uctypes.addressof(addrlen_buf)

    if getsock_f(fd, result_ptr, addrlen_ptr) != 0:
        close_f(fd)
        raise RuntimeError("picolet_ui._test_port: getsockname() failed")

    close_f(fd)

    # sin_port is at offset 2, big-endian uint16.
    port = (result_buf[2] << 8) | result_buf[3]
    if port == 0:
        raise RuntimeError("picolet_ui._test_port: getsockname returned port 0")
    return port
