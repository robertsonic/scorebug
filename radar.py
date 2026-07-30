from __future__ import annotations

import json
import math
import os
import re
import socket
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RADAR_HOST = os.getenv("RADAR_HOST", "192.168.55.103")
RADAR_PORT = int(os.getenv("RADAR_PORT", "23"))

API_HOST = os.getenv("RADAR_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("RADAR_API_PORT", "1992"))

# A sample must be at least this fast to be considered part of a pitch.
PITCH_MIN_KMH = float(os.getenv("PITCH_MIN_KMH", "10"))

# End an event after this long without another pitch-like sample.
EVENT_GAP_SECONDS = float(os.getenv("EVENT_GAP_SECONDS", "0.5"))
EVENT_COOLDOWN_SECONDS = 1.0
# Minimum number of valid radar samples required.
MIN_EVENT_SAMPLES = int(os.getenv("MIN_EVENT_SAMPLES", "3"))

# Optional amplitude filter. Set to zero to disable.
MIN_SIGNAL = int(os.getenv("MIN_SIGNAL", "0"))
SENSITIVITY = 40
# Number of completed events retained in RAM.
HISTORY_SIZE = int(os.getenv("RADAR_HISTORY_SIZE", "50"))


RADAR_PATTERN = re.compile(
    r"N=(?P<speed>[+-]?\d+)\s+S=(?P<signal>\d+)"
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class RadarSample:
    timestamp: float
    speed_kmh: float
    signal: int


@dataclass
class RadarEvent:
    event_id: int
    timestamp: float
    speed_kmh: float
    speed_mph: float
    direction: str
    percentile: float
    sample_count: int
    peak_kmh: float
    signal_peak: int
    signal_average: float


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

class RadarState:
    def __init__(self) -> None:
        self.lock = threading.Lock()

        self.connected = False
        self.last_line: Optional[str] = None
        self.last_sample: Optional[RadarSample] = None
        self.latest_event: Optional[RadarEvent] = None

        self.history: deque[RadarEvent] = deque(maxlen=HISTORY_SIZE)

        self.active_samples: list[RadarSample] = []
        self.last_active_sample_at: Optional[float] = None
        self.last_event_finished_at: Optional[float] = None

        self.next_event_id = 1

    def set_connected(self, connected: bool) -> None:
        with self.lock:
            self.connected = connected

    def process_line(self, line: str) -> None:
        match = RADAR_PATTERN.search(line)
        if not match:
            return

        signed_speed = float(match.group("speed"))
        signal = int(match.group("signal"))
        now = time.time()

        sample = RadarSample(
            timestamp=now,
            speed_kmh=signed_speed,
            signal=signal,
        )

        now_mono = time.monotonic()

        with self.lock:
            self.last_line = line
            self.last_sample = sample

            # Ignore new detections briefly after an event completes
            if (
                self.last_event_finished_at is not None
                and now_mono - self.last_event_finished_at < EVENT_COOLDOWN_SECONDS
            ):
                return

            is_pitch_like = (
                abs(signed_speed) >= PITCH_MIN_KMH
                and signal >= MIN_SIGNAL
            )

            if is_pitch_like:
                self.active_samples.append(sample)
                self.last_active_sample_at = now_mono

    def check_event_timeout(self) -> None:
        with self.lock:
            if not self.active_samples:
                return

            if self.last_active_sample_at is None:
                return

            elapsed = time.monotonic() - self.last_active_sample_at

            if elapsed < EVENT_GAP_SECONDS:
                return

            samples = self.active_samples
            self.active_samples = []
            self.last_active_sample_at = None

            if len(samples) < MIN_EVENT_SAMPLES:
                return

            event = self._build_event(samples)

            self.latest_event = event
            self.history.append(event)
            self.next_event_id += 1
            
            self.last_event_finished_at = time.monotonic()

            print(
                f"Pitch event {event.event_id}: "
                f"{event.speed_kmh:.1f} km/h / "
                f"{event.speed_mph:.1f} mph "
                f"({event.sample_count} samples) "
                " - "
                f"{time.monotonic()}"
                
            )

    def _build_event(self, samples: list[RadarSample]) -> RadarEvent:
        absolute_speeds = sorted(abs(sample.speed_kmh) for sample in samples)

        speed_kmh = percentile(absolute_speeds, 90)

        positive_count = sum(sample.speed_kmh > 0 for sample in samples)
        negative_count = sum(sample.speed_kmh < 0 for sample in samples)

        if positive_count > negative_count:
            direction = "approaching"
        elif negative_count > positive_count:
            direction = "receding"
        else:
            direction = "unknown"

        signals = [sample.signal for sample in samples]

        return RadarEvent(
            event_id=self.next_event_id,
            timestamp=samples[-1].timestamp,
            speed_kmh=round(speed_kmh, 1),
            speed_mph=round(speed_kmh * 0.621371, 1),
            direction=direction,
            percentile=90.0,
            sample_count=len(samples),
            peak_kmh=max(absolute_speeds),
            signal_peak=max(signals),
            signal_average=round(sum(signals) / len(signals), 1),
        )

    def get_status(self) -> dict:
        with self.lock:
            return {
                "connected": self.connected,
                "radar_host": RADAR_HOST,
                "radar_port": RADAR_PORT,
                "pitch_min_kmh": PITCH_MIN_KMH,
                "event_active": bool(self.active_samples),
                "active_sample_count": len(self.active_samples),
                "last_line": self.last_line,
                "last_sample": (
                    asdict(self.last_sample)
                    if self.last_sample
                    else None
                ),
                "latest_event": (
                    asdict(self.latest_event)
                    if self.latest_event
                    else None
                ),
            }

    def get_latest(self) -> Optional[dict]:
        with self.lock:
            if self.latest_event is None:
                return None

            return asdict(self.latest_event)

    def get_history(self) -> list[dict]:
        with self.lock:
            return [asdict(event) for event in self.history]


def percentile(values: list[float], value: float) -> float:
    """
    Linear-interpolated percentile without NumPy.
    """

    if not values:
        raise ValueError("Cannot calculate percentile of an empty list")

    if len(values) == 1:
        return values[0]

    position = (len(values) - 1) * (value / 100.0)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)

    if lower_index == upper_index:
        return values[lower_index]

    fraction = position - lower_index

    return (
        values[lower_index]
        + (values[upper_index] - values[lower_index]) * fraction
    )


state = RadarState()


# ---------------------------------------------------------------------------
# Radar TCP reader
# ---------------------------------------------------------------------------

def radar_reader() -> None:
    while True:
        try:
            print(f"Connecting to radar at {RADAR_HOST}:{RADAR_PORT}")

            with socket.create_connection(
                (RADAR_HOST, RADAR_PORT),
                timeout=5,
            ) as sock:
                sock.settimeout(1.0)
                state.set_connected(True)

                print("Radar connected")

                sock.send(b"S999\n")
                time.sleep(6)    
                sock.sendall(f"S{SENSITIVITY}\n".encode("ascii"))
                buffer = b""

                while True:
                    try:
                        chunk = sock.recv(4096)

                        if not chunk:
                            raise ConnectionError(
                                "Radar closed the TCP connection"
                            )

                        buffer += chunk

                        while b"\n" in buffer:
                            raw_line, buffer = buffer.split(b"\n", 1)

                            line = raw_line.decode(
                                "ascii",
                                errors="ignore",
                            ).strip("\r ")

                            if line:
                                #print(line)
                                state.process_line(line)

                    except socket.timeout:
                        continue

        except Exception as exc:
            print(f"Radar connection error: {exc}")

        finally:
            state.set_connected(False)

        time.sleep(2)


def event_monitor() -> None:
    while True:
        state.check_event_timeout()
        time.sleep(0.02)


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

class RadarRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        if path == "/latest":
            latest = state.get_latest()

            if latest is None:
                self.send_json(
                    {
                        "available": False,
                        "event": None,
                    }
                )
            else:
                self.send_json(
                    {
                        "available": True,
                        "event": latest,
                    }
                )

        elif path == "/status":
            self.send_json(state.get_status())

        elif path == "/history":
            self.send_json(
                {
                    "events": state.get_history(),
                }
            )

        elif path == "/health":
            self.send_json(
                {
                    "ok": True,
                    "radar_connected": state.connected,
                }
            )

        else:
            self.send_json(
                {
                    "error": "Not found",
                    "endpoints": [
                        "/latest",
                        "/status",
                        "/history",
                        "/health",
                    ],
                },
                status=404,
            )

    def send_json(self, body: dict, status: int = 200) -> None:
        encoded = json.dumps(body).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        # Suppress the normal HTTP request log.
        return


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    threading.Thread(
        target=radar_reader,
        name="radar-reader",
        daemon=True,
    ).start()

    threading.Thread(
        target=event_monitor,
        name="event-monitor",
        daemon=True,
    ).start()

    server = ThreadingHTTPServer(
        (API_HOST, API_PORT),
        RadarRequestHandler,
    )

    print(f"Radar API listening on http://{API_HOST}:{API_PORT}")
    print(f"Latest pitch: http://{API_HOST}:{API_PORT}/latest")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping radar service")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()