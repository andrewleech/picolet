import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", int(sys.argv[1]))); s.listen(1)
c, _ = s.accept(); c.recv(1024)
for i in range(5):
    c.sendall(("data: %d\n\n" % i).encode()); time.sleep(0.5)
c.close()
