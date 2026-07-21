"""Dedicated 25 fps output process for the scorebug.

The main scorebug process only sends a new PIL image when the graphic changes.
This process owns the timing loop and continuously writes the newest frame to:

* a Linux framebuffer such as /dev/fb0;
* an FFmpeg subprocess through raw-video stdin; or
* an atomic raw-frame file, if configured.

Keeping output here means HTTP polling, JSON parsing, image downloads, and other
main-process work cannot interrupt the video cadence.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image


@dataclass(slots=True)
class FrameEngineConfig:
    fps: int = 25
    width: int = 1920
    height: int = 1080

    framebuffer_enabled: bool = True
    framebuffer_path: str = "/dev/fb0"
    framebuffer_width: int | None = None
    framebuffer_height: int | None = None

    ffmpeg_enabled: bool = False
    ffmpeg_command: list[str] = field(default_factory=list)

    raw_frame_file: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "FrameEngineConfig":
        value = value or {}
        framebuffer = value.get("framebuffer", {}) or {}
        ffmpeg = value.get("ffmpeg", {}) or {}

        command = ffmpeg.get("command", [])
        if isinstance(command, str):
            raise TypeError("ffmpeg.command must be a JSON array, not a shell string")

        return cls(
            fps=max(1, int(value.get("fps", 25))),
            width=max(1, int(value.get("width", 1920))),
            height=max(1, int(value.get("height", 1080))),
            framebuffer_enabled=bool(framebuffer.get("enabled", True)),
            framebuffer_path=str(framebuffer.get("path", "/dev/fb0")),
            framebuffer_width=_optional_int(framebuffer.get("width")),
            framebuffer_height=_optional_int(framebuffer.get("height")),
            ffmpeg_enabled=bool(ffmpeg.get("enabled", False)),
            ffmpeg_command=[str(item) for item in command],
            raw_frame_file=(
                str(value["raw_frame_file"])
                if value.get("raw_frame_file")
                else None
            ),
        )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return max(1, int(value))


class FrameEngine:
    """Parent-process controller for the dedicated frame output process."""

    def __init__(self, config: Mapping[str, Any] | FrameEngineConfig | None = None):
        self._config = (
            config
            if isinstance(config, FrameEngineConfig)
            else FrameEngineConfig.from_mapping(config)
        )
        self._queue: mp.Queue = mp.Queue(maxsize=1)
        self._stop_event = mp.Event()
        self._process = mp.Process(
            target=_frame_worker,
            args=(self._queue, self._stop_event, self._config),
            name="scorebug-frame-engine",
            daemon=True,
        )

    def start(self) -> None:
        if not self._process.is_alive():
            self._process.start()

    def submit(self, image: Image.Image) -> None:
        """Replace the pending frame with the newest image.

        The queue only holds one item, so stale graphics are discarded rather
        than displayed later.
        """
        rgba = image.convert("RGBA")
        packet = (rgba.width, rgba.height, rgba.tobytes("raw", "RGBA"))

        try:
            self._queue.put_nowait(packet)
            return
        except queue.Full:
            pass

        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass

        try:
            self._queue.put_nowait(packet)
        except queue.Full:
            # The worker won the race and another submit arrived. The next
            # update will replace it; blocking the main process is worse.
            pass

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        self._process.join(timeout)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1.0)

    def __enter__(self) -> "FrameEngine":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


def _frame_worker(
    frame_queue: mp.Queue,
    stop_event: mp.Event,
    config: FrameEngineConfig,
) -> None:
    frame_interval_ns = 1_000_000_000 // config.fps
    current = Image.new("RGBA", (config.width, config.height), "#FF00FF")

    framebuffer = None
    ffmpeg_process: subprocess.Popen[bytes] | None = None

    try:
        if config.framebuffer_enabled:
            try:
                framebuffer = open(config.framebuffer_path, "wb", buffering=0)
                print(f"Frame engine: framebuffer {config.framebuffer_path}")
            except OSError as exc:
                print(f"Frame engine: cannot open framebuffer: {exc}")

        if config.ffmpeg_enabled:
            ffmpeg_process = _start_ffmpeg(config.ffmpeg_command)

        next_frame_ns = time.perf_counter_ns()

        while not stop_event.is_set():
            newest = None
            try:
                while True:
                    newest = frame_queue.get_nowait()
            except queue.Empty:
                pass

            if newest is not None:
                width, height, rgba_bytes = newest
                current = Image.frombytes("RGBA", (width, height), rgba_bytes)

            now_ns = time.perf_counter_ns()
            if now_ns < next_frame_ns:
                time.sleep(min((next_frame_ns - now_ns) / 1_000_000_000, 0.005))
                continue

            if framebuffer is not None:
                try:
                    _write_framebuffer(framebuffer, current, config)
                except (OSError, ValueError) as exc:
                    print(f"Frame engine: framebuffer write failed: {exc}")
                    try:
                        framebuffer.close()
                    finally:
                        framebuffer = None

            if config.raw_frame_file:
                try:
                    _write_atomic_raw_frame(current, config)
                except OSError as exc:
                    print(f"Frame engine: raw frame file write failed: {exc}")

            if ffmpeg_process is not None:
                try:
                    _write_ffmpeg(ffmpeg_process, current, config)
                except (BrokenPipeError, OSError) as exc:
                    print(f"Frame engine: FFmpeg pipe failed: {exc}")
                    _stop_ffmpeg(ffmpeg_process)
                    ffmpeg_process = None

            next_frame_ns += frame_interval_ns

            # Do not burst through a backlog after a stall.
            if time.perf_counter_ns() - next_frame_ns > frame_interval_ns:
                next_frame_ns = time.perf_counter_ns() + frame_interval_ns

    finally:
        if framebuffer is not None:
            framebuffer.close()
        if ffmpeg_process is not None:
            _stop_ffmpeg(ffmpeg_process)


def _output_image(image: Image.Image, width: int, height: int) -> Image.Image:
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return image


def _write_framebuffer(handle, image: Image.Image, config: FrameEngineConfig) -> None:
    width = config.framebuffer_width or config.width
    height = config.framebuffer_height or config.height
    output = _output_image(image, width, height)
    handle.seek(0)
    handle.write(output.tobytes("raw", "BGRA"))


def _write_ffmpeg(
    process: subprocess.Popen[bytes], image: Image.Image, config: FrameEngineConfig
) -> None:
    if process.stdin is None:
        raise BrokenPipeError("FFmpeg stdin is unavailable")
    output = _output_image(image, config.width, config.height)
    process.stdin.write(output.tobytes("raw", "BGRA"))


def _write_atomic_raw_frame(image: Image.Image, config: FrameEngineConfig) -> None:
    output = _output_image(image, config.width, config.height)
    destination = Path(config.raw_frame_file)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(output.tobytes("raw", "BGRA"))
    os.replace(temporary, destination)


def _start_ffmpeg(command: Sequence[str]) -> subprocess.Popen[bytes] | None:
    if not command:
        print("Frame engine: FFmpeg enabled but ffmpeg.command is empty")
        return None

    print("Frame engine: starting FFmpeg")
    try:
        return subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as exc:
        print(f"Frame engine: could not start FFmpeg: {exc}")
        return None


def _stop_ffmpeg(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass

    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
