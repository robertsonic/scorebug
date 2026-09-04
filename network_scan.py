import json
import os
import platform
import random
import re
import socket
import subprocess
import threading
import time

import psutil
import requests

from tplinkrouterc6u import TPLinkMRClient, TplinkRouterProvider
from dotenv import load_dotenv

load_dotenv()

TP_LINK_USERNAME = os.getenv("TPLINK_USERNAME", "admin")
TP_LINK_PASSWORD = os.getenv("TPLINK_PASSWORD")

SYSTEM = platform.system().lower()

HTTP_TIMEOUT = 0.05

# TP-Link clients are cached for the lifetime of this module/process so that
# repeated calls to scan_networks() do not log in to the router every time.
_TPLINK_ROUTERS = {}
_TPLINK_LOCK = threading.Lock()


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


def get_interface_ipv4(safe=False):
    result = {}

    for name, addresses in psutil.net_if_addrs().items():

        for address in addresses:

            if address.family == socket.AF_INET:

                if not safe:
                    result[name] = address.address
                else:
                    if not name.lower().startswith(
                        ("eth", "wifi")
                    ) or address.address.startswith("169.254."):
                        continue

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

    useful_fields = [
        "signalbar",
        "network_type",
        "lte_rssi",
        "lte_rsrp",
        "lte_rsrq",
        "lte_snr",
    ]

    if not any(data.get(field) not in (None, "") for field in useful_fields):
        return None

    return {
        "manufacturer": "ZTE",
        "network_type": data.get("network_type") or None,
        "network_provider": data.get("network_provider") or None,
        "signal_bars": to_int(data.get("signalbar")) or None,
        "rssi_dbm": to_float(data.get("lte_rssi") or data.get("rssi")),
        "rsrp_dbm": to_float(data.get("lte_rsrp")),
        "rsrq_db": to_float(data.get("lte_rsrq")),
        "snr_db": to_float(data.get("lte_snr")),
        "cell_id": data.get("cell_id") or None,
        "raw": data
    }

def looks_like_tplink(gateway):
    try:
        response = requests.get(
            f"http://{gateway}/",
            timeout=HTTP_TIMEOUT,
        )

        text = response.text.lower()

        return (
            "tp-link" in text
            or "tplink" in text
        )

    except requests.RequestException:
        return False

def get_tplink_router(gateway):
    """
    Return a cached, authorised TP-Link client for this gateway.

    Because network.py/network_scan.py is imported once by the web-control
    process, the cached client persists between calls to scan_networks().
    """

    with _TPLINK_LOCK:
        router = _TPLINK_ROUTERS.get(gateway)

        if router is not None:
            return router

        # router = TplinkRouterProvider.get_client(
        #     f"http://{gateway}",
        #     TP_LINK_PASSWORD,
        #     TP_LINK_USERNAME,
        #     timeout=HTTP_TIMEOUT * 60
        # )

        router = TPLinkMRClient(
            f"http://{gateway}",
            TP_LINK_PASSWORD,
            TP_LINK_USERNAME,
            timeout=HTTP_TIMEOUT
        )

        router.authorize()
        _TPLINK_ROUTERS[gateway] = router

        return router


def discard_tplink_router(gateway):
    """
    Remove a cached TP-Link client.

    Used when the router session has expired or the router has rebooted.
    The next request will create and authorise a fresh client.
    """

    with _TPLINK_LOCK:
        router = _TPLINK_ROUTERS.pop(gateway, None)

    if router is not None:
        try:
            router.logout()
        except Exception:
            pass


def probe_tplink(gateway):

    if not TP_LINK_PASSWORD:
        return None

    if gateway not in _TPLINK_ROUTERS:
        if not looks_like_tplink(gateway):
            return None

    # First attempt reuses the cached session. If that session has expired,
    # discard it and retry once with a fresh login.
    for attempt in range(2):
        try:
            router = get_tplink_router(gateway)
            lte = router.get_lte_status()

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
            discard_tplink_router(gateway)

            if attempt == 1:
                return None

    return None


def probe_gateway(gateway):

    for probe in (
        probe_zte,
        probe_tplink,
    ):
        start = time.monotonic()
        result = probe(gateway)

       # print(f"{gateway} {probe} took {(time.monotonic() - start) * 1000:.1f} ms")
        if result:
            return result

    return None


def scan_networks(safe=False):

    interface_ips = get_interface_ipv4(safe)

    results = []
    seen = set()

    for route in get_gateways():

        gateway = route["gateway"]

        if gateway in seen:
            continue

        seen.add(gateway)

        info = probe_gateway(gateway)

        # if not info:
        #    continue

        info = { "signal_bars" : random.random() * 5}

        ip = interface_ips.get(route["interface"])

        if ip is None:
            continue

        results.append(
            {
                "interface": route["interface"],
                "interface_ip": ip,
                "gateway": gateway,
                "default": route["default"],
                "info": info,
            }
        )

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
