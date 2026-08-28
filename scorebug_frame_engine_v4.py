from __future__ import annotations

import math
import threading
import copy
import random
import queue
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any, Literal, TypeAlias
from zoneinfo import ZoneInfo
import threading
import requests

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageChops

WIDTH = 1920
HEIGHT = 1080
FPS = 25
FB = "/dev/fb0"
SCOREBUG_TEMPLATE_FILE = "templatev4.jpeg"
LINEUP_TEMPLATE_FILE = "lineup.png"
STARTING_TEMPLATE_FILE = "startingv2.jpeg"
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
class Dirty:
    image: Image.Image | None
    bbox: tuple[int, int, int, int] | None = (0, 0, WIDTH, HEIGHT)
    opacity: int = 100


Point: TypeAlias = tuple[int, int]
BBox: TypeAlias = tuple[int, int, int, int]
Colour: TypeAlias = str | tuple[int, int, int] | tuple[int, int, int, int]


@dataclass(kw_only=True)
class TextElement:
    type: Literal["text"] = "text"

    pos: Point
    font: ImageFont.ImageFont
    bbox: BBox

    opacity_fade_ns: int | None = None
    opacity_hold_ns: int | None = None

    started_ns: int | None = None

    text: str = ""
    anchor: str = "lt"
    align: Literal["left", "center", "right"] = "left"
    fade: bool = False
    fill: Colour = "white"
    stroke_width: int = 0
    stroke_fill: Colour = "black"
    visible: bool = True
    text_limit: int = 0


@dataclass(kw_only=True)
class PolyElement:
    type: Literal["poly"] = "poly"

    points: list[Point]
    bbox: BBox | None = None
    fade: bool = False
    fill: Colour = "white"
    stroke_width: int = 0
    stroke_fill: Colour = "black"
    visible: bool = True


@dataclass(kw_only=True)
class MultiPolyElement:
    type: Literal["multi_poly"] = "multi_poly"

    points: list[list[Point]]
    bbox: BBox
    fade: bool = False
    fill: Colour = "white"
    stroke_width: int = 0
    stroke_fill: Colour = "black"
    visible: bool = True


@dataclass(kw_only=True)
class RectElement:
    type: Literal["rect"] = "rect"

    pos: BBox

    text_font: ImageFont.ImageFont | None = None
    bbox: BBox | None = None
    fade: bool = False
    fill: Colour = "white"
    stroke_width: int = 0
    stroke_fill: Colour = "black"
    rounded: bool = False
    text: str | None = None
    text_fill: Colour = "black"
    visible: bool = True


Element: TypeAlias = TextElement | PolyElement | MultiPolyElement


@dataclass
class FrameEngineConfig:
    width: int = WIDTH
    height: int = HEIGHT
    fps: int = FPS
    framebuffer_path: str | None = FB
    ffmpeg_command: list[str] | None = None


class FrameEngine:
    """Owns all PIL rendering, animation timing and video output."""

    def __init__(self, updates: Queue, stop_event: Any, config: FrameEngineConfig):
        self.updates = updates
        self.stop_event = stop_event
        self.config = config

        self.template = None
        self.font_large = self._load_font("Gotham-Bold.otf", 72)
        self.font_medium = self._load_font("Gotham-Book.otf", 42)
        self.font_small = self._load_font("Gotham-Bold.otf", 17)
        self.font_smaller = self._load_font("Gotham-Bold.otf", 16)
        self.font_status = self._load_font("source-code-pro.bold.ttf", 16)
        self.lineup_medium = self._load_font("Gotham-Book.otf", 58)
        self.lineup_small = self._load_font("Gotham-Book.otf", 26)

        self.faders: dict[str, Element] = {}
        self.opacity_hold_ns = 5_000_000_000
        self.opacity_fade_ns = 1_000_000_000

        self.scene = "blank"
        self.state: dict[str, Any] | None = None
        self.latest_elements: dict[str, Any] = {}
        self.state_changed_ns = time.perf_counter_ns()
        self.prev_state_change = self.state_changed_ns
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

        self.lineup_render: Image.Image | None = None
        self.static_render: Image.Image | None = None
        self.starting_render: Image.Image | None = None

        self.competition_logo_name: str | None = None
        self.competition_logo: Image.Image | None = None

        self.dirty_images: dict[str, Dirty] = {}

        self.canvas: bytearray = bytearray()
        self.canvas_dimensions = (1920, 1080)
        self.canvas_stride = self.canvas_dimensions[0] * 4

        self.debug: bool = True
        self.debug_frame_count = 0
        self.debug_window_start_ns = time.perf_counter_ns()
        self.render_debug = True

        self.debug_render_ns = 0
        self.debug_convert_ns = 0
        self.debug_framebuffer_ns = 0
        self.debug_ffmpeg_ns = 0

        self.scenes: dict[str, dict[str, Element]] = {
            "scorebug": {
                "away_short": TextElement(
                    pos=(828, 973),
                    font=self.font_large,
                    stroke_width=1,
                    anchor="lm",
                    bbox=(828, 940, 1038, 1013),
                ),
                "away_score": TextElement(
                    pos=(1138, 973),
                    font=self.font_large,
                    stroke_width=1,
                    anchor="rm",
                    bbox=(1038, 940, 1138, 1013),
                ),
                "away_colour": RectElement(
                    pos=(810, 934, 1200, 1013), fill="away_colour"
                ),
                "home_colour": PolyElement(
                    points=[(1200, 934), (1528, 934), (1607, 1013), (1200, 1013)],
                    fill="home_colour",
                ),
                "home_short": TextElement(
                    pos=(1218, 973),
                    font=self.font_large,
                    stroke_width=1,
                    anchor="lm",
                    bbox=(1218, 940, 1428, 1013),
                ),
                "home_score": TextElement(
                    pos=(1528, 973),
                    font=self.font_large,
                    stroke_width=1,
                    anchor="rm",
                    bbox=(1428, 940, 1528, 1013),
                ),
                "outs": TextElement(
                    pos=(1755, 837),
                    font=self.font_small,
                    anchor="ms",
                    align="center",
                    bbox=(1700, 800, 1890, 850),
                ),
                "inning": TextElement(
                    pos=(1790, 885),
                    font=self.font_medium,
                    anchor="mm",
                    align="center",
                    bbox=(1750, 850, 1920, 925),
                ),
                "inning_top": PolyElement(
                    points=[
                        (1735, 875),
                        (1745, 895),
                        (1725, 895),
                    ],
                    bbox=(1725, 875, 1745, 897),
                ),
                "inning_bottom": PolyElement(
                    points=[
                        (1725, 877),
                        (1745, 877),
                        (1735, 897),
                    ],
                    bbox=(1725, 875, 1745, 897),
                ),
                "pitch_speed": TextElement(
                    font=self.font_small,
                    anchor="ma",
                    align="center",
                    bbox=(1750, 930, 1830, 950),
                    pos=(1790, 930),
                    fade=True,
                ),
                "count": TextElement(
                    pos=(1790, 1000),
                    font=self.font_medium,
                    anchor="ms",
                    align="center",
                    bbox=(1750, 950, 1920, 1100),
                ),
                "bases": MultiPolyElement(
                    points=[
                        [
                            (1670, 900),
                            (1702, 932),
                            (1670, 964),
                            (1638, 932),
                        ],
                        [
                            (1629, 858),
                            (1661, 890),
                            (1629, 922),
                            (1597, 890),
                        ],
                        [
                            (1588, 900),
                            (1620, 932),
                            (1588, 964),
                            (1556, 932),
                        ],
                    ],
                    fill="yellow",
                    bbox=(1556, 858, 1702, 964),
                ),
                "away_player": TextElement(
                    pos=(835, 909),
                    font=self.font_small,
                    anchor="lm",
                    bbox=(835, 900, 1115, 930),
                ),
                "home_player": TextElement(
                    pos=(1230, 909),
                    font=self.font_small,
                    anchor="lm",
                    bbox=(1230, 900, 1510, 930),
                ),
                "status": TextElement(
                    pos=(835, 1031),
                    font=self.font_status,
                    bbox=(835, 1021, 1600, 1080),
                    anchor="lm",
                    fade=True,
                    text_limit=70,
                ),
            },
            "lineup": {
                "away_colour": RectElement(
                    pos=(90, 60, 958, 1021),
                    fill="away_colour",
                    rounded=True,
                ),
                "home_colour": RectElement(
                    pos=(958, 60, 1824, 1021),
                    fill="home_colour",
                    rounded=True,
                ),
                "away_name": TextElement(
                    pos=(490, 110),
                    font=self.lineup_medium,
                    anchor="mm",
                    align="center",
                    bbox=(104, 80, 943, 138),
                    stroke_width=1,
                ),
                "home_name": TextElement(
                    pos=(1390, 110),
                    font=self.lineup_medium,
                    anchor="mm",
                    align="center",
                    bbox=(974, 80, 1816, 138),
                    stroke_width=1,
                ),
                "status": TextElement(
                    pos=(960, 995),
                    font=self.font_small,
                    bbox=(500, 965, 1420, 1025),
                    anchor="mm",
                    align="center",
                    fade=True,
                ),
            },
            "starting": {
                "away_name": TextElement(
                    pos=(
                        (self.canvas_dimensions[0] // 2) - 50,
                        (self.canvas_dimensions[1] // 2) + 205,
                    ),
                    font=self.font_large,
                    fill="white",
                    align="right",
                    anchor="rs",
                    stroke_fill="black",
                    stroke_width=1,
                    bbox=(
                        0,
                        ((self.canvas_dimensions[1] // 2) + 205 - 70),
                        (self.canvas_dimensions[0] // 2) - 50,
                        ((self.canvas_dimensions[1] // 2) + 215),
                    ),
                ),
                "home_name": TextElement(
                    pos=(
                        (self.canvas_dimensions[0] // 2) + 50,
                        (self.canvas_dimensions[1] // 2) + 205,
                    ),
                    font=self.font_large,
                    fill="white",
                    align="left",
                    anchor="ls",
                    stroke_fill="black",
                    stroke_width=1,
                    bbox=(
                        (self.canvas_dimensions[0] // 2) + 50,
                        ((self.canvas_dimensions[1] // 2) + 205 - 70),
                        self.canvas_dimensions[0],
                        ((self.canvas_dimensions[1] // 2) + 215),
                    ),
                ),
                "location": TextElement(
                    pos=(
                        (self.canvas_dimensions[0] // 2) - 50,
                        (self.canvas_dimensions[1] // 2) + 215,
                    ),
                    font=self.font_medium,
                    fill="white",
                    align="right",
                    anchor="ra",
                    stroke_fill="black",
                    stroke_width=1,
                    bbox=(
                        0,
                        (self.canvas_dimensions[1] // 2) + 215,
                        (self.canvas_dimensions[0] // 2) - 50,
                        (self.canvas_dimensions[1] // 2) + 215 + 60,
                    ),
                ),
                "start_time": TextElement(
                    pos=(
                        (self.canvas_dimensions[0] // 2) + 50,
                        (self.canvas_dimensions[1] // 2) + 215,
                    ),
                    font=self.font_medium,
                    fill="white",
                    align="right",
                    anchor="la",
                    stroke_fill="black",
                    stroke_width=1,
                    bbox=(
                        (self.canvas_dimensions[0] // 2) + 50,
                        (self.canvas_dimensions[1] // 2) + 215,
                        self.canvas_dimensions[0],
                        (self.canvas_dimensions[1] // 2) + 215 + 60,
                    ),
                ),
            },
            "pitching": {
                "pitch_speed": TextElement(
                    pos=(
                        self.canvas_dimensions[0] - 50 - (300 // 2),
                        self.canvas_dimensions[1] - 50 - (150 // 2),
                    ),
                    fill="black",
                    align="center",
                    anchor="mm",
                    font=self.font_medium,
                    bbox=(
                        self.canvas_dimensions[0] - 50 - 300,
                        self.canvas_dimensions[1] - 50 - 150,
                        self.canvas_dimensions[0] - 50,
                        self.canvas_dimensions[1] - 50,
                    ),
                    fade=True,
                    opacity_hold_ns=8_000_000_000,
                )
            },
            "common": {
                "clock": RectElement(
                    pos=(1800, 20, 1900, 80),
                    fill="black",
                    stroke_fill="white",
                    rounded=True,
                    text_fill="white",
                    text_font=self.font_small,
                    bbox=(1800, 20, 1900, 80),
                )
            },
        }

    @staticmethod
    def _load_font(path: str, size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            return ImageFont.load_default()

    def _load_starting(self, path: str) -> Image.Image:
        try:

            if self.starting_render is not None:
                return self.starting_render

            image = Image.open(path).convert("RGBA")
            if image.size != self.canvas_dimensions:
                image = image.resize(self.canvas_dimensions)

            draw = ImageDraw.ImageDraw(image)

            if self.competition_logo is not None:
                image.alpha_composite(self.competition_logo, (15, 25))

            if self.render_debug:
                image.save("./staring.png")

            self.starting_render = image
            return image
        except Exception as e:
            print(e)
            return bytearray()

    def _load_template(self) -> Image.Image:
        try:
            image = None

            scene = self.scenes[self.scene]
            print(f"Rendering {self.scene}")

            if self.scene == "starting":

                image = self._load_imgfile_and_resize(STARTING_TEMPLATE_FILE)
                draw = ImageDraw.ImageDraw(image)

                draw.text(
                    (
                        (self.canvas_dimensions[0] / 2),
                        (self.canvas_dimensions[1] / 2) + 205,
                    ),
                    font=self.font_large,
                    fill="white",
                    align="center",
                    anchor="ms",
                    stroke_fill="black",
                    stroke_width=1,
                    text=" @ ",
                )

                draw.text(
                    (
                        (self.canvas_dimensions[0] / 2),
                        (self.canvas_dimensions[1] // 2) + 215,
                    ),
                    font=self.font_medium,
                    fill="white",
                    align="center",
                    anchor="ma",
                    stroke_fill="black",
                    stroke_width=1,
                    text=" - ",
                )

            elif self.scene == "scorebug":

                image = self._load_imgfile_and_resize(SCOREBUG_TEMPLATE_FILE)

                team_colours = Image.new("RGBA", image.size, (255, 255, 255, 255))
                draw = ImageDraw.Draw(team_colours)

                for e in ["away_colour", "home_colour"]:

                    elem = scene[e]
                    fill = f"#{self.state[e]}" if e in self.state else elem.fill

                    if isinstance(elem, RectElement):

                        draw.rectangle(
                            elem.pos,
                            fill=fill,
                            width=elem.stroke_width,
                            outline=elem.stroke_fill,
                        )

                    if isinstance(elem, PolyElement):
                        draw.polygon(
                            elem.points,
                            fill=fill,
                            width=elem.stroke_width,
                            outline=elem.stroke_fill,
                        )

                image = ImageChops.multiply(image, team_colours)

            elif self.scene == "lineup":

                image = self._load_imgfile_and_resize(LINEUP_TEMPLATE_FILE)

                team_colours = Image.new("RGBA", image.size, (255, 255, 255, 255))
                draw = ImageDraw.Draw(team_colours)

                for e in ["away_colour", "home_colour"]:

                    elem = scene[e]
                    fill = f"#{self.state[e]}" if e in self.state else elem.fill

                    if isinstance(elem, RectElement):
                        if elem.rounded:
                            draw.rounded_rectangle(
                                elem.pos,
                                radius=10,
                                fill=fill,
                                width=elem.stroke_width,
                                outline=elem.stroke_fill,
                            )
                        else:
                            draw.rectangle(
                                elem.pos,
                                fill=fill,
                                width=elem.stroke_width,
                                outline=elem.stroke_fill,
                            )

                    if isinstance(elem, PolyElement):
                        draw.polygon(
                            elem.pos,
                            fill=fill,
                            width=elem.stroke_width,
                            outline=elem.stroke_fill,
                        )

                image = ImageChops.multiply(image, team_colours)
                draw = ImageDraw.Draw(image)
                # for e in ["away_name", "home_name"]:
                #     elem = scene.get(e)
                #     draw.text(
                #         elem.pos,
                #         font=elem.font,
                #         text=str.upper(self.latest_elements.get(e, {}).get("text", "")),
                #         anchor=elem.anchor,
                #         align=elem.align,
                #     )

                x = 0
                for e in ["away_lineup", "home_lineup"]:

                    lineup = self.latest_elements.get(e, [])
                    for y in range(len(lineup)):

                        left_x = 99
                        right_x = 950
                        top_y = 212
                        bottom_y = 260

                        if y == 9:
                            continue
                        elif y < 9:
                            line = f"{lineup[y].get("order","#")} - {lineup[y].get("pos","XX")} - {lineup[y].get("name","LASTNAME Firstname")} - {lineup[y].get("stats","(.### PA: #)")}"
                        elif y == 10:
                            top_y -= 3
                            bottom_y -= 3
                            line = f"P - {lineup[y].get("name","LASTNAME Firstname")} - {lineup[y].get("stats","(ER: ## - BB: ## - K: ##)")}"

                        draw.rounded_rectangle(
                            [
                                left_x + (873 * x),
                                top_y + (70 * y),
                                right_x + (873 * x),
                                bottom_y + (70 * y),
                            ],
                            fill=None,
                            radius=10,
                            outline="white",
                            width=2,
                        )

                        draw.text(
                            (
                                left_x + 13 + (871 * x),
                                top_y + ((bottom_y - top_y) // 2) + (70 * y),
                            ),
                            font=self.lineup_small,
                            anchor="lm",
                            align="left",
                            text=line,
                            stroke_width=1,
                            stroke_fill="black",
                        )
                    x += 1

            elif self.scene == "pitching":

                image = Image.new("RGBA", self.canvas_dimensions, MAGENTA)

                draw = ImageDraw.Draw(image)

                draw.rounded_rectangle(
                    [
                        self.canvas_dimensions[0] - 50 - 300,
                        self.canvas_dimensions[1] - 50 - 150,
                        self.canvas_dimensions[0] - 50,
                        self.canvas_dimensions[1] - 50,
                    ],
                    radius=10,
                    fill="white",
                    outline="black",
                    width=2,
                )

            if self.competition_logo is not None:
                image.alpha_composite(self.competition_logo, (15, 25))

            image.save("debug/scene_template.png")

            return image
        except Exception as e:
            print(e)
            return Image.new("RGBA", self.canvas_dimensions, MAGENTA)

    def _load_imgfile_and_resize(self, path: str):
        image = Image.open(path).convert("RGBA")
        if image.size != self.canvas_dimensions:
            image = image.resize(self.canvas_dimensions)
        return image

    def _consume_updates(self) -> None:
        latest = None
        try:
            # while True:
            latest = self.updates.get_nowait()
        except queue.Empty:
            pass

        if latest is None:
            return

        stream = latest.get("stream")

        self.debug = latest.get("debug", False)
        self.render_debug = latest.get("render_debug", False)

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
        state = copy.deepcopy(latest.get("state", {}))

        if command == "blank":
            self.scene = "blank"
            self.state = {}

        if command == "reload":
            print("Reloading Scene")
            self.template = None
            command = "update"

            competition = state.get("competition")
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
            else:
                self.competition_logo_name = None
                self.competition_logo = None

        if command == "update":
            self.prev_scene = self.scene
            self.scene = latest.get("scene", "scorebug")
            self.state = state
            print(f"Scene - {self.scene}")
            print("Saving Latest Elements")
            self.latest_elements = {
                **self.latest_elements,
                **latest.get("elements", {}),
            }
            print("Latest Elements", self.latest_elements)

        self.state_changed_ns = time.perf_counter_ns()

    def render_scene(self, now_ns) -> tuple[dict[str, Dirty], bool]:

        save_png = False

        if self.template is None:
            self.template = self._load_template()
            self.canvas = bytearray(self.template.tobytes("raw", "BGRA"))

        elements = copy.deepcopy(self.latest_elements)
        self.latest_elements = {}

        if elements:
            save_png = True

        dirty: dict[str, Dirty] = {}

        t0 = time.perf_counter_ns()
        t1 = 0
        t2 = 0
        t3 = 0
        t4 = 0
        t5 = 0
        t6 = 0

        for k, v in self.faders.items():

            if v["fade"]:
                fade_started_ns = (
                    v["started_ns"]
                    if v.get("started_ns", None) is not None
                    else self.state_changed_ns
                )
                age_ns = max(0, now_ns - fade_started_ns)

                opacity_hold_ns = (
                    v["opacity_hold_ns"]
                    if v.get("opacity_hold_ns") is not None
                    else self.opacity_hold_ns
                )
                opacity_fade_ns = (
                    v["opacity_fade_ns"]
                    if v.get("opacity_fade_ns") is not None
                    else self.opacity_fade_ns
                )

                if age_ns <= opacity_hold_ns:
                    opacity = 100
                else:
                    opacity = max(
                        0,
                        math.floor(
                            100 * (1 - ((age_ns - opacity_hold_ns) / opacity_fade_ns))
                        ),
                    )
                v["started_ns"] = fade_started_ns

                if opacity <= 0 and age_ns >= (
                    opacity_fade_ns + opacity_hold_ns + opacity_fade_ns
                ):
                    v["fade"] = False
                    v["started_ns"] = None
                    opacity = 100
                    v["text"] = v.get("fixed_text", "")

                dirty[k] = Dirty(image=None, bbox=None, opacity=opacity)
                # print(k in elements)
                if not k in elements:
                    elements[k] = v

        t1 = time.perf_counter_ns()  # Fades

        if self.static_render is None or self.state_changed_ns > self.prev_state_change:
            self.prev_state_change = self.state_changed_ns

            for k, v in elements.items():

                if (
                    isinstance(v, dict)
                    and v.get("data", None) is not None
                    and isinstance(v["data"], bool)
                    and v.get("data", False) is not True
                ):
                    continue

                rect: Image.Image | None = None

                try:
                    elem = {
                        **copy.deepcopy(self.scenes[self.scene]),
                        **copy.deepcopy(self.scenes["common"]),
                    }[k]
                except:
                    continue

                if v.get("fade", getattr(elem, "fade", False)):
                    self.faders[k] = {
                        **copy.deepcopy(v),
                        "fade": True,
                        "opacity_hold_ns": getattr(elem, "opacity_hold_ns", None),
                        "opacity_fade_ns": getattr(elem, "opacity_fade_ns", None),
                    }

                fill = getattr(elem, "fill", "white")
                stroke_fill = getattr(elem, "stroke_fill", "black")

                left, top, right, bottom = elem.bbox
                bbox = (
                    left if left >= 0 else 0,
                    top if top >= 0 else 0,
                    (
                        right + 1
                        if right + 1 <= self.canvas_dimensions[0]
                        else self.canvas_dimensions[0]
                    ),
                    (
                        bottom + 1
                        if bottom + 1 <= self.canvas_dimensions[1]
                        else self.canvas_dimensions[1]
                    ),
                )

                if elem.type == "poly" and elem.visible:

                    rect = self.template.crop(bbox)

                    draw = ImageDraw.Draw(rect)

                    if elements[k]["data"]:
                        points = []
                        for j in range(len(elem.points)):
                            jj = elem.points[j]
                            points.append(
                                (
                                    jj[0] - bbox[0],
                                    jj[1] - bbox[1],
                                )
                            )
                        draw.polygon(
                            points,
                            fill=fill,
                        )

                if elem.type == "multi_poly" and elem.visible:

                    rect = self.template.crop(bbox)

                    draw = ImageDraw.Draw(rect)

                    for i in range(len(elements[k]["data"])):

                        if elements[k]["data"][i]:

                            points = []

                            for j in range(len(elem.points[i])):
                                jj = elem.points[i][j]
                                points.append(
                                    (
                                        jj[0] - bbox[0],
                                        jj[1] - bbox[1],
                                    )
                                )

                            draw.polygon(
                                points,
                                fill=fill,
                            )

                if elem.type == "text" and elem.visible:
                    rect = self.template.crop(bbox)

                    draw = ImageDraw.Draw(rect)
                    anchor = getattr(elem, "anchor", None)
                    pos = (0, 0)
                    if anchor is not None:
                        if anchor[0] == "r":
                            pos = (rect.size[0], pos[1])
                        if anchor[0] == "m":
                            pos = (
                                elem.pos[0] - bbox[0],
                                pos[1],
                            )
                        if anchor[1] == "m":
                            pos = (
                                pos[0],
                                elem.pos[1] - bbox[1],
                            )
                        if anchor[1] == "b" or anchor[1] == "s":
                            pos = (
                                pos[0],
                                elem.pos[1] - bbox[1],
                            )
                        if anchor[1] == "t" or anchor[1] == "a":
                            pos = (pos[0], elem.pos[1] - bbox[1])

                    text = str(elements[k]["text"])
                    if elem.text_limit and len(text) > elem.text_limit:
                        text = text[: elem.text_limit - 3].rstrip() + "..."

                    draw.text(
                        pos,
                        text=text,
                        fill=fill,
                        stroke_fill=stroke_fill,
                        anchor=getattr(elem, "anchor", None),
                        align=getattr(elem, "align", None),
                        font=getattr(elem, "font", None),
                        stroke_width=getattr(elem, "stroke_width", 0),
                    )

                if elem.type == "rect" and elem.visible:
                    rect = self.template.crop(bbox)

                    draw = ImageDraw.Draw(rect)

                    pos = (0, 0, bbox[2] - bbox[0] - 1, bbox[3] - bbox[1] - 1)

                    if elem.rounded:

                        draw.rounded_rectangle(
                            pos,
                            fill=fill,
                            radius=15,
                            outline=getattr(elem, "stroke_fill", "black"),
                            width=2,
                        )

                    if str(elements[k]["text"]):
                        draw.text(
                            (pos[2] / 2, pos[3] / 2),
                            text=str(elements[k]["text"]),
                            fill=tuple(255 - c for c in ImageColor.getrgb(fill)[:3]),
                            anchor="mm",
                            font=getattr(elem, "text_font", 0),
                            align="center",
                        )

                if rect is not None:

                    if k not in dirty:
                        dirty[k] = Dirty(bbox=bbox, image=rect, opacity=100)
                    else:
                        dirty[k].bbox = bbox
                        dirty[k].image = rect

                    if k in dirty and dirty[k].opacity < 100:

                        if dirty[k].opacity <= 0:
                            dirty[k].image = self.template.crop(bbox)
                            dirty[k].bbox = bbox
                            elem.visible = False

                        else:
                            dirty[k].image.putalpha(
                                math.floor(255 * dirty[k].opacity / 100)
                            )
                            dirty[k].image = Image.alpha_composite(
                                self.template.crop(bbox), dirty[k].image
                            )

                    t2 = time.perf_counter_ns()  # Dynamic Elements
                    if self.render_debug:
                        rect.save(f"debug/{k}.png")
                    t3 = time.perf_counter_ns()  # Saving PNG

        if t2 == 0:
            t2 = time.perf_counter_ns()
        if t3 == 0:
            t3 = time.perf_counter_ns()

        if self.render_debug:
            print(
                f"fades={(t1-t0)/1e6:.1f} "
                f"dynamic_elements={(t2-t1)/1e6:.1f} "
                f"saving_pngs={(t3-t2)/1e6:.1f} "
            )

        return dirty, save_png

    def render_blank(self, now_ns: int) -> Image.Image:
        return Image.new("RGBA", self.canvas_dimensions, MAGENTA)

    def _draw_common_overlays(self, image: Image.Image) -> None:

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

    # def _render(self, now_ns: int) -> dict[str, Dirty]:
    #     if self.scene == "scorebug":
    #         return self.render_scene(now_ns)
    #     if self.scene == "lineup":
    #         return {"lineup": Dirty(image=self.render_lineup_sheet(now_ns))}
    #     if self.scene == "starting":
    #         return {"starting": Dirty(image=self._load_starting("startingv2.jpeg"))}

    #     return {"blank": Dirty(image=self.render_blank(now_ns))}

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

    def _write_outputs(self, dirty: dict[str, Dirty]) -> tuple[int, int, int]:
        convert_start_ns = time.perf_counter_ns()

        for k, region in dirty.items():
            image = region.image

            if region.image is None or region.bbox is None:
                continue

            left, top, right, bottom = region.bbox
            if image.mode != "RGBA":
                image = image.convert("RGBA")

            width = image.width
            height = image.height
            row_bytes = width * 4

            raw = image.tobytes("raw", "BGRA")
            raw_view = memoryview(raw)

            for row in range(height):
                src_start = row * row_bytes
                src_end = src_start + row_bytes

                dst_start = (top + row) * self.canvas_stride + left * 4
                dst_end = dst_start + row_bytes

                self.canvas[dst_start:dst_end] = raw_view[src_start:src_end]

        # if image.size != self.canvas_dimensions:
        #     image = image.resize(self.canvas_dimensions)

        # if image.mode != "RGBA":
        #     image = image.convert("RGBA")

        # raw = image.tobytes("raw", "BGRA")
        convert_end_ns = time.perf_counter_ns()

        framebuffer_start_ns = convert_end_ns

        if self.config.framebuffer_path:
            try:
                with open(
                    self.config.framebuffer_path, "wb", buffering=0
                ) as framebuffer:
                    framebuffer.write(self.canvas)
            except OSError as exc:
                print(f"Framebuffer output failed: {exc}")
                self.config.framebuffer_path = None

        framebuffer_end_ns = time.perf_counter_ns()

        ffmpeg_start_ns = framebuffer_end_ns

        if self.config.ffmpeg_command:
            try:
                self._ensure_ffmpeg()
                if self.ffmpeg is not None and self.ffmpeg.stdin is not None:
                    self.ffmpeg.stdin.write(self.canvas)
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
        interval_ns = 1_000_000_000 // (self.config.fps)
        next_frame_ns = time.perf_counter_ns()

        try:
            while not self.stop_event.is_set():
                self._consume_updates()
                self._report_srt_health()
                now_ns = time.perf_counter_ns()

                if now_ns >= next_frame_ns:
                    frame_start_ns = time.perf_counter_ns()

                    dirty_images = {}
                    save_png = False

                    if self.state is not None:
                        dirty_images, save_png = self.render_scene(now_ns)
                    render_end_ns = time.perf_counter_ns()

                    (
                        convert_ns,
                        framebuffer_ns,
                        ffmpeg_ns,
                    ) = self._write_outputs(dirty_images)

                    if save_png:
                        preview = Image.frombytes(
                            "RGBA",
                            (self.canvas_dimensions[0], self.canvas_dimensions[1]),
                            bytes(self.canvas),
                            "raw",
                            "BGRA",
                        )
                        preview = preview.convert("RGB")
                        preview.thumbnail((480, 270))
                        preview.save("preview.jpg", quality=75)

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

                    # next_frame_ns += interval_ns
                    next_frame_ns = frame_start_ns + interval_ns
                    # if frame_end_ns - next_frame_ns > interval_ns:
                    #    next_frame_ns = frame_end_ns + interval_ns

                # remaining_ns = next_frame_ns - time.perf_counter_ns()

                # if remaining_ns > 0:
                # time.sleep(min(remaining_ns / 1_000_000_000, 0.002))

        finally:
            self._close_ffmpeg()


def run_frame_engine(
    updates: Queue, stop_event: Any, config_dict: dict[str, Any] | None = None
) -> None:
    config = FrameEngineConfig(**(config_dict or {}))
    FrameEngine(updates, stop_event, config).run()
