import socket
import struct
import time
import math

from network_scan import get_interface_ipv4

GROUP = "239.255.19.92"
PORT = 1992
READ_PARAMETERS = bytes.fromhex("77 AA 01 0A D4 7A")


def parse_radar(data):
    value = ''
    try:
      value = data.decode().strip()
    except:
        pass

    # V = velocity, - = incoming, M = MPH
    if not (value.startswith("V-") and value.endswith("M")):
        raise ValueError(f"Invalid radar data or configuration string is: {data.hex(' ')}")

    value = math.floor(float(value[2:-1]))

    if value > 999 or value < 20:
        return None

    return value

    # except (UnicodeDecodeError, ValueError):
    #     return None


def run_radar(updates=None, stop_event=None, config={"port": PORT, "group": GROUP}):
    last_keepalive = 0.0
    radar_address: socket._RetAddress | None = None

    interfaces = get_interface_ipv4(safe=True)

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

        print(f"Joined {config['group']} on " f"{interface} ({ip})")

    try:
        while stop_event is None or not stop_event.is_set():
            try:

                sock.settimeout(1.0)

                data, addr = sock.recvfrom(1024)

                speed = parse_radar(data)

                radar_address = addr

                if updates is not None and speed is not None:
                    updates.put(speed)
                elif speed is not None:
                    print(addr, speed)

            except ValueError as e:
                print(e)
                pass

            except socket.timeout:
                pass

            # Send read-parameter command every 60 seconds
            if radar_address is not None and time.monotonic() - last_keepalive >= 60.0:
                sock.sendto(
                    READ_PARAMETERS,
                    radar_address,
                )

                print(f"Sent radar keepalive to {radar_address}")

                last_keepalive = time.monotonic()

    except KeyboardInterrupt:
        print("Stopping listener")

    finally:
        sock.close()


if __name__ == "__main__":
    run_radar()
