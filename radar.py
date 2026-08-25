import socket
import struct
import time
import math

from network_scan import get_interface_ipv4

GROUP = "239.255.19.92"
PORT = 1992


def parse_radar(data):
    try:
        value = data.decode().strip()

        # V = velocity, - = incoming, M = MPH
        if not (value.startswith("V-") and value.endswith("M")):
            return None

        value = math.floor(float(value[2:-1]))
        
        if value > 999 or value < 20:
            return None
        
        return value

    except (UnicodeDecodeError, ValueError):
        return None
    
def run_radar(updates = None, stop_event = None, config = { "port" : PORT, "group": GROUP}):

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

    #sock.settimeout(1.0)

    try:
        while stop_event is None or not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(1024)

                speed = parse_radar(data)

                
                if updates is not None and speed is not None:
                    updates.put(
                        speed
                    )
                elif speed is not None:
                    print(addr, speed)

            except:
                continue

    except KeyboardInterrupt:
        print("Stopping listener")

    finally:
        sock.close()
        
if __name__ == "__main__":

    print("hi")
    run_radar()