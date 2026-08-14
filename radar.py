import socket
import struct
import time

from network_scan import get_interface_ipv4

GROUP = "239.255.19.92"
PORT = 1992


def run_radar(updates, stop_event, config):

    interfaces = get_interface_ipv4()

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

    for interface, ip in interfaces.items():

        if not str(interface).lower().startswith("eth"):
            continue

        mreq = struct.pack(
            "4s4s",
            socket.inet_aton(config["group"]),
            socket.inet_aton(ip),
        )

        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            mreq,
        )

        print(
            f"Joined {config['group']} on "
            f"{interface} ({ip})"
        )

    sock.settimeout(1.0)

    try:
        while not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(1024)

                decoded = data.decode()
                value = int(decoded)
             
                print(addr, value, decoded)
                
                if value > 999 or value < 20:
                    continue

                updates.put(
                    value
                )

            except:
                continue

    except KeyboardInterrupt:
        print("Stopping listener")

    finally:
        sock.close()