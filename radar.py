import socket
import struct
import time

GROUP = "239.255.19.92"
PORT = 1992


def run_radar(updates, stop_event, config):

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM,
        socket.IPPROTO_UDP,
    )

    sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1,
    )

    sock.bind(("", config["port"]))

    mreq = struct.pack(
        "4s4s",
        socket.inet_aton(config["group"]),
        socket.inet_aton("0.0.0.0"),
    )

    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        mreq,
    )

    sock.settimeout(1.0)

    try:
        while not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(1024)
                print(addr, data.decode())
                updates.put(int(data.decode()))

            except socket.timeout:
                continue

    except KeyboardInterrupt:
        print("Stopping listener")

    finally:
        sock.close()
