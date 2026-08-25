import socket
import struct
import threading
import time

MODULE_IP = "10.19.92.200"
MULTICAST_IP = "239.255.19.92"
PORT = 1992


# Work out which interface reaches the module
temp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
temp.connect((MODULE_IP, PORT))
LOCAL_IP = temp.getsockname()[0]
temp.close()

print(f"Local IP  : {LOCAL_IP}")
print(f"Module    : {MODULE_IP}:{PORT}")
print(f"Multicast : {MULTICAST_IP}:{PORT}")
print()


# Create UDP socket
sock = socket.socket(
    socket.AF_INET,
    socket.SOCK_DGRAM,
    socket.IPPROTO_UDP
)

sock.setsockopt(
    socket.SOL_SOCKET,
    socket.SO_REUSEADDR,
    1
)

# Bind to UDP 1992
sock.bind(("", PORT))


# Join multicast group on our Ethernet interface
mreq = struct.pack(
    "4s4s",
    socket.inet_aton(MULTICAST_IP),
    socket.inet_aton(LOCAL_IP)
)

sock.setsockopt(
    socket.IPPROTO_IP,
    socket.IP_ADD_MEMBERSHIP,
    mreq
)


def receiver():
    while True:
        data, addr = sock.recvfrom(4096)

        print(
            f"\n<<< RX {addr[0]}:{addr[1]} "
            f"({len(data)} bytes)"
        )
        print(f"    ASCII: {data!r}")
        print(f"    HEX:   {data.hex(' ')}")


threading.Thread(
    target=receiver,
    daemon=True
).start()


counter = 0

while True:

    # counter += 1

    # message = (
    #     f"UART LOOPBACK TEST {counter}\r\n"
    # ).encode("ascii")

    # print(f">>> TX {MULTICAST_IP}:{PORT}: {message!r}")

    # sock.sendto(
    #     message,
    #     (MULTICAST_IP, PORT)
    # )

    time.sleep(1)