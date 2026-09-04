import socket
import struct
import time
import math
import os

from network_scan import get_interface_ipv4
from dotenv import load_dotenv

load_dotenv()

GROUP = "239.255.19.92"
PORT = 1992

READ_PARAMETERS_COMMAND = bytes([0x77, 0xAA, 0x01, 0x0A, 0xD4, 0x7A])
READ_HEADER = bytes([0x77, 0xAA, 0x08, 0x0A])
WRITE_HEADER = bytes([0x77, 0xAA, 0x07, 0x0B])

STARTS_WITH = os.getenv("STARTS_WITH", "V-")

SENSITIVITY = int(os.getenv("RADAR_SENSITIVITY", 10))
LOW_SPEED_LIMIT = int(os.getenv("RADAR_LOW_SPEED_LIMIT", 40))
DIRECTION = int(os.getenv("RADAR_DIRECTION", 0))
UNIT = int(os.getenv("RADAR_UNIT", 1))
ANTI_INTERFERENCE = int(os.getenv("RADAR_ANTI_INTERFERENCE", 7))

value_buffer: list[int] = []
last_value: int | None = None


class ParameterError(Exception):
    pass


def parse_radar(data: bytes):

    global last_value, value_buffer

    value = ""
    try:
        value = data.decode().strip()
    except:
        pass

    # V = velocity, - = incoming, M = MPH

    if not value.isnumeric():
        if not (value.startswith(STARTS_WITH) and value.endswith("M")):

            if value.startswith("#"):
                data = bytes.fromhex(value[1:])

            if data.startswith(READ_HEADER):  # PARAMETER READ

                sensitivity = data[4]
                low_speed_limit = data[5]
                direction = data[6]
                unit = data[7]
                anti_interference = data[8]

                if (
                    sensitivity != SENSITIVITY
                    or low_speed_limit != LOW_SPEED_LIMIT
                    or direction != DIRECTION
                    or unit != UNIT
                    or anti_interference != ANTI_INTERFERENCE
                ):

                    raise ParameterError(
                        f"Sensitivity: {sensitivity} Expecting: {SENSITIVITY}\n"
                        f"Low Speed Limit: {low_speed_limit} Expecting: {LOW_SPEED_LIMIT}\n"
                        f"Direction: {direction} Expecting: {DIRECTION}\n"
                        f"Unit: {unit} Expecting: {UNIT}\n"
                        f"Anti-Interference: {anti_interference} Expecting: {ANTI_INTERFERENCE}"
                    )

            raise ValueError(
                f"Invalid radar data or configuration. String is: {data.hex(' ')} ({value})"
            )
        value = math.floor(float(value[2:-1]))
    else:
        value = int(value)

    # At this point the value is 100% a integer, should fail earlier

    if value <= 999 and value >= 20:
        value_buffer.append(value)
    elif value == 0:
        if last_value == 0 and value_buffer:
            max_value = max(value_buffer)
            value_buffer = []
            return max_value or None

    last_value = value


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

            except ParameterError as e:

                print(e)

                command = WRITE_HEADER + bytes(
                    [
                        SENSITIVITY,
                        LOW_SPEED_LIMIT,
                        DIRECTION,
                        UNIT,
                        ANTI_INTERFERENCE,
                        0x00,
                        0x05,
                    ]
                )

                crc = bytes([(-sum(command)) & 0xFF])

                try:
                    sock.sendto(bytes(command + crc + bytes([0x7A])), radar_address)
                except:
                    print("Couldn't send parameter request to radar")

            # Send read-parameter command every 60 seconds
            if radar_address is not None and time.monotonic() - last_keepalive >= 60.0:
                sock.sendto(
                    READ_PARAMETERS_COMMAND,
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
