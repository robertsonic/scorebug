import socket

from network_scan import get_interface_ipv4


GROUP = "239.255.19.92"
PORT = 1992
TARGET = (GROUP, PORT)

interfaces = get_interface_ipv4(safe=True)

sockets = []
print(interfaces)
for interface, ip in interfaces.items():

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM,
        socket.IPPROTO_UDP,
    )

    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_IF,
        socket.inet_aton(ip),
    )

    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_TTL,
        1,
    )

    sockets.append(sock)

    print(f"Multicast output on {interface} ({ip})")


def send(value):
    for sock in sockets:
        sock.sendto(value, TARGET)


send(bytes.fromhex("77 AA 01 0A D4 7A"))

while True:
    value = input("Speed: ").encode()

    send(value)
