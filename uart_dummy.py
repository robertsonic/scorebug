import socket

from network_scan import get_interface_ipv4


GROUP = "239.255.19.92"
PORT = 1992
TARGET = (GROUP, PORT)

interfaces = get_interface_ipv4()

sockets = []
print(interfaces)
for interface, ip in interfaces.items():

    if not str(interface).lower().startswith("eth"):
        continue

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


while True:
    value = input("Speed: ").encode()

    for sock in sockets:
        sock.sendto(value, TARGET)