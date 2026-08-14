import json
import os
import platform
import re
import socket
import subprocess

import psutil
import requests

from tplinkrouterc6u import TplinkRouterProvider
from dotenv import load_dotenv

load_dotenv()

TP_LINK_USERNAME = os.getenv("TPLINK_USERNAME", "admin")
TP_LINK_PASSWORD = os.getenv("TPLINK_PASSWORD")

SYSTEM = platform.system().lower()

HTTP_TIMEOUT = 2


def run_command(command):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return None

        return result.stdout

    except Exception:
        return None


def get_interface_ipv4():
    result = {}

    for name, addresses in psutil.net_if_addrs().items():

        for address in addresses:

            if address.family == socket.AF_INET:
                result[name] = address.address
                break

    return result


def get_linux_gateways():
    output = run_command(["ip", "-4", "route"])

    if not output:
        return []

    results = []

    for line in output.splitlines():

        match = re.search(
            r"^(?:default|\S+)\s+via\s+(\d+\.\d+\.\d+\.\d+)\s+dev\s+(\S+)",
            line
        )

        if not match:
            continue

        gateway = match.group(1)
        interface = match.group(2)

        results.append({
            "gateway": gateway,
            "interface": interface,
            "default": line.startswith("default")
        })

    return results


def get_windows_gateways():
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        """
        Get-NetRoute -AddressFamily IPv4 |
        Where-Object {
            $_.NextHop -ne '0.0.0.0' -and
            $_.NextHop -ne ''
        } |
        Select-Object InterfaceAlias,NextHop,DestinationPrefix,RouteMetric |
        ConvertTo-Json -Compress
        """
    ]

    output = run_command(command)

    if not output:
        return []

    try:
        routes = json.loads(output)
    except json.JSONDecodeError:
        return []

    if isinstance(routes, dict):
        routes = [routes]

    results = []

    for route in routes:

        gateway = route.get("NextHop")
        interface = route.get("InterfaceAlias")
        destination = route.get("DestinationPrefix")

        if not gateway or not interface:
            continue

        results.append({
            "gateway": gateway,
            "interface": interface,
            "default": destination == "0.0.0.0/0"
        })

    return results


def get_gateways():

    if SYSTEM == "linux":
        return get_linux_gateways()

    if SYSTEM == "windows":
        return get_windows_gateways()

    return []


def probe_zte(gateway):

    fields = [
        "signalbar",
        "network_type",
        "network_provider",
        "rssi",
        "lte_rssi",
        "lte_rsrp",
        "lte_rsrq",
        "lte_snr",
        "cell_id",
    ]

    url = (
        f"http://{gateway}/goform/goform_get_cmd_process"
        "?isTest=false"
        f"&cmd={','.join(fields)}"
        "&multi_data=1"
    )

    try:
        response = requests.get(
            url,
            headers={
                "Referer": f"http://{gateway}/index.html",
                "User-Agent": "Mozilla/5.0"
            },
            timeout=HTTP_TIMEOUT
        )

        data = response.json()

    except Exception:
        return None

    if not data.get("signalbar"):
        return None

    return {
        "manufacturer": "ZTE",
        "network_type": data.get("network_type") or None,
        "network_provider": data.get("network_provider") or None,
        "signal_bars": to_int(data.get("signalbar")),
        "rssi_dbm": to_float(data.get("lte_rssi") or data.get("rssi")),
        "rsrp_dbm": to_float(data.get("lte_rsrp")),
        "rsrq_db": to_float(data.get("lte_rsrq")),
        "snr_db": to_float(data.get("lte_snr")),
        "cell_id": data.get("cell_id") or None,
        "raw": data
    }


def probe_tplink(gateway):

    if not TP_LINK_PASSWORD:
        return None

    router = None

    try:
        router = TplinkRouterProvider.get_client(
            f"http://{gateway}",
            TP_LINK_PASSWORD,
            TP_LINK_USERNAME
        )

        router.authorize()

        try:
            lte = router.get_lte_status()
        except Exception:
            lte = None

        if not lte:
            return None

        raw = object_to_dict(lte)

        return {
            "manufacturer": "TP-Link",
            "network_type": raw.get("network_type"),
            "network_provider": raw.get("isp_name"),
            "signal_bars": to_int(raw.get("sig_level")),
            "rssi_dbm": None,
            "rsrp_dbm": to_float(raw.get("rsrp")),
            "rsrq_db": to_float(raw.get("rsrq")),
            "snr_db": to_float(raw.get("snr")),
            "cell_id": None,
            "rx_speed": to_int(raw.get("cur_rx_speed")),
            "tx_speed": to_int(raw.get("cur_tx_speed")),
            "raw": raw
        }

    except Exception:
        return None

    finally:
        if router:
            try:
                router.logout()
            except Exception:
                pass


def probe_gateway(gateway):

    for probe in (
        probe_zte,
        probe_tplink,
    ):

        result = probe(gateway)

        if result:
            return result

    return None


def scan_networks():

    interface_ips = get_interface_ipv4()

    results = []
    seen = set()

    for route in get_gateways():

        gateway = route["gateway"]

        if gateway in seen:
            continue

        seen.add(gateway)

        info = probe_gateway(gateway)

        if not info:
            continue

        results.append({
            "interface": route["interface"],
            "interface_ip": interface_ips.get(route["interface"]),
            "gateway": gateway,
            "default": route["default"],
            "info": info
        })

    return results


def object_to_dict(obj):

    if isinstance(obj, dict):
        return obj

    if hasattr(obj, "__dict__"):
        return {
            k: (
                v.value
                if hasattr(v, "value")
                else v
            )
            for k, v in vars(obj).items()
            if not k.startswith("_")
        }

    return {
        "value": str(obj)
    }


def to_float(value):

    try:
        return float(value)

    except (TypeError, ValueError):
        return None


def to_int(value):

    try:
        return int(value)

    except (TypeError, ValueError):
        return None


if __name__ == "__main__":

    print(
        json.dumps(
            scan_networks(),
            indent=4,
            default=str
        )
    )