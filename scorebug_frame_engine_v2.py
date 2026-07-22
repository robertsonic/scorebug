from __future__ import annotations

import threading
import copy
import os
import queue
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import threading
import requests

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1920
HEIGHT = 1080
FPS = 25
FB = "/dev/fb0"
TEMPLATE_FILE = "templatev3.png"
MAGENTA = "#FF00FF"


def report_srt_state(
    live: bool,
    status: str,
    message: str,
    pid: int | None = None,
) -> None:
    payload = {
        "live": live,
        "status": status,
        "message": message,
        "pid": pid,
    }

    def send() -> None:
        try:
            requests.post(
                "http://127.0.0.1:8080/api/srt/state",
                json=payload,
                timeout=2,
            )
        except requests.RequestException as exc:
            print(f"Could not report SRT state: {exc}")

    threading.Thread(target=send, daemon=True).start()


@dataclass
class FrameEngineConfig:
    width: int = WIDTH
    height: int = HEIGHT
    fps: int = FPS
    framebuffer_path: str | None = FB
    ffmpeg_command: list[str] | None = None
    template_file: str = TEMPLATE_FILE


class FrameEngine:
    """Owns all PIL rendering, animation timing and video output."""

    def __init__(self, updates: Queue, stop_event: Any, config: FrameEngineConfig):
        self.updates = updates
        self.stop_event = stop_event
        self.config = config

        self.template = self._load_template(config.template_file)
        self.font_large = self._load_font("Gotham-Bold.otf", 72)
        self.font_medium = self._load_font("Gotham-Book.otf", 42)
        self.font_small = self._load_font("Gotham-Bold.otf", 17)
        self.font_smaller = self._load_font("Gotham-Bold.otf", 16)
        self.font_status = self._load_font("source-code-pro.bold.ttf", 16)
        self.lineup_medium = self._load_font("Gotham-Book.otf", 56)
        self.lineup_small = self._load_font("Gotham-Book.otf", 24)

        self.scene = "blank"
        self.state: dict[str, Any] = {}
        self.state_changed_ns = time.perf_counter_ns()
        self.ffmpeg: subprocess.Popen[bytes] | None = None
        self.last_srt_report_ns = 0
        self.srt_report_interval_ns = 5_000_000_000
        # The most recently completed raw video frame.
        #
        # bytes is immutable, so the writer can safely keep using an older frame
        # while the renderer replaces this reference with a newer one.
        self.latest_raw_frame: bytes | None = None
        self.latest_frame_lock = threading.Lock()

        # Stops only the FFmpeg writer thread.
        self.ffmpeg_writer_stop = threading.Event()
        self.ffmpeg_writer_thread: threading.Thread | None = None

        self.competition_logo_name: str | None = None
        self.competition_logo: Image.Image | None = None

        self.debug: bool = True
        self.debug_frame_count = 0
        self.debug_window_start_ns = time.perf_counter_ns()

        self.debug_render_ns = 0
        self.debug_convert_ns = 0
        self.debug_framebuffer_ns = 0
        self.debug_ffmpeg_ns = 0

    @staticmethod
    def _load_font(path: str, size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            return ImageFont.load_default()

    def _load_template(self, path: str) -> Image.Image:
        try:
            image = Image.open(path).convert("RGBA")
            if image.size != (self.config.width, self.config.height):
                image = image.resize((self.config.width, self.config.height))
            return image
        except OSError:
            return Image.new(
                "RGBA", (self.config.width, self.config.height), (0, 0, 0, 0)
            )

    def _consume_updates(self) -> None:
        latest = None
        try:
            while True:
                latest = self.updates.get_nowait()
        except queue.Empty:
            pass

        if latest is None:
            return

        stream = latest.get("stream")

        if stream is not None:
            live = bool(stream.get("live", False))
            command = stream.get("ffmpeg_command")

            if live and command:
                if command != self.config.ffmpeg_command:
                    self._close_ffmpeg()
                    self.config.ffmpeg_command = command

                self._ensure_ffmpeg()

            else:
                self._close_ffmpeg()
                self.config.ffmpeg_command = None

        command = latest.get("command", "update")

        if command == "blank":
            self.scene = "blank"
            self.state = {}

        elif command == "update":
            self.scene = latest.get("scene", "scorebug")
            self.state = copy.deepcopy(latest.get("state", {}))

        elif command == "reload_assets":
            self.template = self._load_template(self.config.template_file)

        elif command == "stream":
            # The stream section above has already been handled.
            pass

        self.state_changed_ns = time.perf_counter_ns()

    def render_scorebug(self, now_ns: int) -> Image.Image:
        """Build one complete scorebug frame from the current semantic state."""
        elements = self.state.get("elements", {})
        overlay = Image.new("RGBA", self.template.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        away_colour = elements.get("away_score", {}).get("colour", "FFFFFF")
        home_colour = elements.get("home_score", {}).get("colour", "000000")

        # TEAM COLOURS
        draw.rectangle((810, 934, 1200, 1013), fill=f"#{away_colour}BF")  # x-42
        draw.polygon(
            [(1200, 934), (1528, 934), (1607, 1013), (1200, 1013)],
            fill=f"#{home_colour}BF",
        )

        draw.text(
            (828, 950),
            str(elements.get("away_name", {}).get("text", "AWAY")),
            fill="white",
            font=self.font_large,
            anchor="lt",
            stroke_fill="black",
            stroke_width=1,
        )
        draw.text(
            (1138, 950),
            str(elements.get("away_score", {}).get("text", "0")),
            fill="white",
            font=self.font_large,
            anchor="rt",
            stroke_fill="black",
            stroke_width=1,
        )
        draw.text(
            (1218, 950),
            str(elements.get("home_name", {}).get("text", "HOME")),
            fill="white",
            font=self.font_large,
            anchor="lt",
            stroke_fill="black",
            stroke_width=1,
        )
        draw.text(
            (1528, 950),
            str(elements.get("home_score", {}).get("text", "0")),
            fill="white",
            font=self.font_large,
            anchor="rt",
            stroke_fill="black",
            stroke_width=1,
        )

        inning = elements.get("inning", {})
        draw.text(
            (1770, 885),
            str(inning.get("text", "PRE")),
            fill="white",
            font=self.font_medium,
            anchor="lm",
            align="center",
        )
        points = inning.get("points")
        if points:
            draw.polygon(points, fill="white")

        draw.text(
            (1755, 837),
            str(elements.get("outs", {}).get("text", "0 OUT")),
            fill="white",
            font=self.font_small,
            anchor="mb",
            align="center",
        )
        draw.text(
            (1780, 1000),
            str(elements.get("count", {}).get("text", "0-0")),
            fill="white",
            font=self.font_medium,
            anchor="mb",
            align="center",
        )
        draw.text(
            (835, 909),
            str(elements.get("away_player", {}).get("text", "")),
            fill="white",
            font=self.font_small,
            anchor="lm",
        )
        draw.text(
            (1230, 909),
            str(elements.get("home_player", {}).get("text", "")),
            fill="white",
            font=self.font_small,
            anchor="lm",
        )

        for base in elements.get("bases", {}).get("points", []):
            draw.polygon(base, fill="yellow")

        # Example frame-driven fade. The main process only supplies text and timing.
        status = elements.get("status", {})
        status_text = str(status.get("text", ""))

        status_started_ns = int(status.get("started_ns", self.state_changed_ns))
        hold_ns = int(status.get("hold_ns", 5_000_000_000))
        fade_ns = max(1, int(status.get("fade_ns", 1_000_000_000)))
        age_ns = max(0, now_ns - status_started_ns)
        if age_ns <= hold_ns:
            alpha = 255
        else:
            alpha = max(0, round(255 * (1 - ((age_ns - hold_ns) / fade_ns))))

        if alpha <= 0 and age_ns >= (fade_ns + hold_ns + fade_ns):
            status_text = str(status.get("fixed_text", ""))
            alpha = 255

        draw.text(
            (835, 1031),
            status_text,
            fill=(255, 255, 255, alpha),
            font=self.font_status,
            anchor="lm",
        )

        image = Image.alpha_composite(self.template, overlay)
        self._draw_common_overlays(image)
        return image

    def render_lineup_sheet(self, now_ns: int) -> Image.Image:
        state = self.state
        image = Image.new("RGBA", (self.config.width, self.config.height), MAGENTA)
        draw = ImageDraw.Draw(image)

        away_colour = state.get("away_colour", "FFFFFF")
        home_colour = state.get("home_colour", "000000")
        away = state.get("away_lineup", [])
        home = state.get("home_lineup", [])

        draw.rounded_rectangle(
            (270, 180, 1650, 950), radius=25, fill="#111", outline="#FFF", width=2
        )
        draw.rectangle((272, 200, 960, 260), fill=f"#{away_colour}")
        draw.rectangle((960, 200, 1648, 260), fill=f"#{home_colour}")
        draw.text(
            (605, 230),
            state.get("away_name", "AWAY"),
            fill="white",
            font=self.lineup_medium,
            anchor="mm",
            stroke_fill="#000",
            stroke_width=1,
        )
        draw.text(
            (1340, 230),
            state.get("home_name", "HOME"),
            fill="white",
            font=self.lineup_medium,
            anchor="mm",
            stroke_fill="#000",
            stroke_width=1,
        )

        for x in (290, 980):
            draw.text(
                (x, 285), "#", fill="#CCCCCC", font=self.lineup_small, anchor="lm"
            )
            draw.text(
                (x + 50, 285),
                "POS",
                fill="#CCCCCC",
                font=self.lineup_small,
                anchor="lm",
            )
            draw.text(
                (x + 130, 285),
                "PLAYER",
                fill="#CCCCCC",
                font=self.lineup_small,
                anchor="lm",
            )

        self._draw_lineup_rows(draw, away, 280, 290, 340, 420, away_colour)
        self._draw_lineup_rows(draw, home, 970, 980, 1030, 1100, home_colour)
        self._draw_common_overlays(image)
        return image

    def _draw_lineup_rows(
        self,
        draw: ImageDraw.ImageDraw,
        players: list[dict[str, Any]],
        box_x: int,
        number_x: int,
        pos_x: int,
        name_x: int,
        colour: str,
    ) -> None:
        start_y = 330
        row_gap = 55
        for i, player in enumerate(players[:11]):
            if not player:
                continue
            y = start_y + i * row_gap
            draw.rounded_rectangle(
                (box_x, y - 20, box_x + 670, y + 20),
                fill=f"#{colour}CC",
                outline=f"#{colour}",
                width=1,
                radius=5,
            )
            if player.get("is_pitcher"):
                draw.text(
                    (number_x, y),
                    "Pitcher:",
                    fill="white",
                    font=self.lineup_small,
                    anchor="lm",
                    stroke_fill="#000",
                    stroke_width=1,
                )
                draw.text(
                    (name_x, y),
                    player.get("display", ""),
                    fill="white",
                    font=self.lineup_small,
                    anchor="lm",
                    stroke_fill="#000",
                    stroke_width=1,
                )
            else:
                draw.text(
                    (number_x, y),
                    str(player.get("order", "")),
                    fill="white",
                    font=self.lineup_small,
                    anchor="lm",
                    stroke_fill="#000",
                    stroke_width=1,
                )
                draw.text(
                    (pos_x, y),
                    str(player.get("pos", "")),
                    fill="white",
                    font=self.lineup_small,
                    anchor="lm",
                    stroke_fill="#000",
                    stroke_width=1,
                )
                draw.text(
                    (name_x, y),
                    player.get("display", ""),
                    fill="white",
                    font=self.lineup_small,
                    anchor="lm",
                    stroke_fill="#000",
                    stroke_width=1,
                )

    def render_blank(self, now_ns: int) -> Image.Image:
        return Image.new("RGBA", (self.config.width, self.config.height), MAGENTA)

    def _draw_common_overlays(self, image: Image.Image) -> None:
        competition = self.state.get("competition")

        if competition:
            if competition != self.competition_logo_name:
                self.competition_logo_name = competition
                self.competition_logo = None

                logo_path = Path("images") / f"{competition}.png"

                try:
                    with Image.open(logo_path) as source:
                        logo = source.convert("RGBA")

                    logo.thumbnail((120, 120))
                    self.competition_logo = logo

                except OSError:
                    self.competition_logo = None

            if self.competition_logo is not None:
                image.alpha_composite(self.competition_logo, (15, 25))

        else:
            self.competition_logo_name = None
            self.competition_logo = None

        draw = ImageDraw.Draw(image)

        clock = datetime.now(ZoneInfo("Europe/London")).strftime("%H:%M %Z")

        draw.rounded_rectangle(
            (1800, 20, 1900, 80),
            fill="#000000",
            radius=15,
            outline="#FFF",
            width=2,
        )

        draw.text(
            (1850, 50),
            clock,
            fill="#FFF",
            anchor="mm",
            font=self.font_small,
            align="center",
        )

    def _render(self, now_ns: int) -> Image.Image:
        if self.scene == "scorebug":
            return self.render_scorebug(now_ns)
        if self.scene == "lineup":
            return self.render_lineup_sheet(now_ns)
        return self.render_blank(now_ns)

    def _report_srt_health(self, force: bool = False) -> None:
        now_ns = time.perf_counter_ns()

        if not force and now_ns - self.last_srt_report_ns < self.srt_report_interval_ns:
            return

        self.last_srt_report_ns = now_ns

        process = self.ffmpeg

        if process is not None and process.poll() is None:
            report_srt_state(
                live=True,
                status="live",
                message="SRT output running",
                pid=process.pid,
            )
        else:
            report_srt_state(
                live=False,
                status="offline",
                message="SRT output stopped",
                pid=None,
            )

    def _ensure_ffmpeg(self) -> None:
        if not self.config.ffmpeg_command or self.ffmpeg is not None:
            return
        try:
            self.ffmpeg = subprocess.Popen(
                self.config.ffmpeg_command,
                stdin=subprocess.PIPE,
                # stderr=subprocess.DEVNULL,
            )

            time.sleep(0.1)

            if self.ffmpeg.poll() is not None:
                raise RuntimeError(f"FFmpeg exited with code {self.ffmpeg.returncode}")

            report_srt_state(
                live=True,
                status="live",
                message="SRT output started",
                pid=self.ffmpeg.pid,
            )

        except Exception as exc:
            self.ffmpeg = None

            report_srt_state(
                live=False,
                status="error",
                message=f"Unable to START SRT: {exc}",
            )

    def _write_outputs(self, image: Image.Image) -> tuple[int, int, int]:
        convert_start_ns = time.perf_counter_ns()

        if image.size != (self.config.width, self.config.height):
            image = image.resize((self.config.width, self.config.height))

        if image.mode != "RGBA":
            image = image.convert("RGBA")

        raw = image.tobytes("raw", "BGRA")
        convert_end_ns = time.perf_counter_ns()

        framebuffer_start_ns = convert_end_ns

        if self.config.framebuffer_path:
            try:
                with open(
                    self.config.framebuffer_path, "wb", buffering=0
                ) as framebuffer:
                    framebuffer.write(raw)
            except OSError as exc:
                print(f"Framebuffer output failed: {exc}")
                self.config.framebuffer_path = None

        framebuffer_end_ns = time.perf_counter_ns()

        ffmpeg_start_ns = framebuffer_end_ns

        if self.config.ffmpeg_command:
            try:
                self._ensure_ffmpeg()
                if self.ffmpeg is not None and self.ffmpeg.stdin is not None:
                    self.ffmpeg.stdin.write(raw)
            except (BrokenPipeError, OSError) as exc:
                print(f"FFmpeg output failed: {exc}")
                self._close_ffmpeg()

        ffmpeg_end_ns = time.perf_counter_ns()

        return (
            convert_end_ns - convert_start_ns,
            framebuffer_end_ns - framebuffer_start_ns,
            ffmpeg_end_ns - ffmpeg_start_ns,
        )

    def _close_ffmpeg(self) -> None:
        if self.ffmpeg is None:
            return
        try:
            if self.ffmpeg.stdin:
                self.ffmpeg.stdin.close()
                self.ffmpeg.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            self.ffmpeg.terminate()
        finally:
            self.ffmpeg = None

        report_srt_state(
            live=False,
            status="offline",
            message="SRT output stopped",
        )

    def run(self) -> None:
        interval_ns = 1_000_000_000 // self.config.fps
        next_frame_ns = time.perf_counter_ns()

        try:
            while not self.stop_event.is_set():
                self._consume_updates()
                self._report_srt_health()
                now_ns = time.perf_counter_ns()

                if now_ns >= next_frame_ns:
                    frame_start_ns = time.perf_counter_ns()

                    image = self._render(now_ns)

                    render_end_ns = time.perf_counter_ns()

                    (
                        convert_ns,
                        framebuffer_ns,
                        ffmpeg_ns,
                    ) = self._write_outputs(image)

                    frame_end_ns = time.perf_counter_ns()

                    if self.debug:

                        self.debug_frame_count += 1
                        self.debug_render_ns += render_end_ns - frame_start_ns
                        self.debug_convert_ns += convert_ns
                        self.debug_framebuffer_ns += framebuffer_ns
                        self.debug_ffmpeg_ns += ffmpeg_ns

                        debug_elapsed_ns = frame_end_ns - self.debug_window_start_ns

                        if debug_elapsed_ns >= 1_000_000_000:
                            elapsed_seconds = debug_elapsed_ns / 1_000_000_000
                            frame_count = self.debug_frame_count

                            python_fps = frame_count / elapsed_seconds

                            render_ms = self.debug_render_ns / frame_count / 1_000_000
                            convert_ms = self.debug_convert_ns / frame_count / 1_000_000
                            framebuffer_ms = (
                                self.debug_framebuffer_ns / frame_count / 1_000_000
                            )
                            ffmpeg_ms = self.debug_ffmpeg_ns / frame_count / 1_000_000

                            total_ms = (
                                render_ms + convert_ms + framebuffer_ms + ffmpeg_ms
                            )

                            print(
                                f"Python FPS={python_fps:.1f} | "
                                f"render={render_ms:.1f}ms | "
                                f"convert={convert_ms:.1f}ms | "
                                f"framebuffer={framebuffer_ms:.1f}ms | "
                                f"ffmpeg_write={ffmpeg_ms:.1f}ms | "
                                f"total={total_ms:.1f}ms"
                            )

                            self.debug_frame_count = 0
                            self.debug_window_start_ns = frame_end_ns
                            self.debug_render_ns = 0
                            self.debug_convert_ns = 0
                            self.debug_framebuffer_ns = 0
                            self.debug_ffmpeg_ns = 0

                    next_frame_ns += interval_ns

                    if frame_end_ns - next_frame_ns > interval_ns:
                        next_frame_ns = frame_end_ns + interval_ns

                remaining_ns = next_frame_ns - time.perf_counter_ns()

                # if remaining_ns > 0:
                # time.sleep(min(remaining_ns / 1_000_000_000, 0.002))

        finally:
            self._close_ffmpeg()


def run_frame_engine(
    updates: Queue, stop_event: Any, config_dict: dict[str, Any] | None = None
) -> None:
    config = FrameEngineConfig(**(config_dict or {}))
    FrameEngine(updates, stop_event, config).run()
