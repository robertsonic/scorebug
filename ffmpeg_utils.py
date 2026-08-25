import platform
import os
from typing import Any
from dotenv import load_dotenv

load_dotenv()

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "")

def build_stream_state(srt: dict[str, Any]) -> dict[str, Any]:

    live = bool(srt.get("live_requested", False))
    url = str(srt.get("url", "")).strip()

    return {
        "live": live and bool(url),
        "ffmpeg_command": build_srt_command(srt) if live and url else None,
    }


def build_srt_command(srt: dict[str, Any]) -> list[str]:
    width = int(srt.get("width", 1920))
    height = int(srt.get("height", 1080))
    fps = int(srt.get("fps", 25))
    bitrate = str(srt.get("bitrate", "4M"))
    url = str(srt.get("url", "")).strip()

    video_preset = [
        "libx264",
        "-preset",
        "ultrafast",
        "-g",
        "50",
        "-keyint_min",
        "50",
        "-sc_threshold",
        "0",
    ]

    if platform.system() == "Linux":
        video_preset = ["h264_v4l2m2m", "-g", "50"]

    return (
        [
            f"{FFMPEG_PATH}ffmpeg",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgra",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(fps),
            "-i",
            "pipe:0",
            "-stream_loop",
            "-1",
            "-i",
            "sonican-blues-rock-victory-inspirational-loop-465097.mp3",
            "-c:v",
        ]
        + video_preset
        + [
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            "8M",
            "-c:a",
            "aac",
            "-f",
            "mpegts",
            url,
        ]
    )
    # return [
    #     "ffmpeg",
    #     "-f",
    #     "rawvideo",
    #     "-pix_fmt",
    #     "bgra",
    #     "-video_size",
    #     f"{width}x{height}",
    #     "-framerate",
    #     str(fps),
    #     "-i",
    #     "pipe:0",
    #     "-stream_loop",
    #     "-1",
    #     "-i",
    #     "sonican-blues-rock-victory-inspirational-loop-465097.mp3",
    #     "-map",
    #     "0:v:0",
    #     "-map",
    #     "1:a:0",
    #     "-c:v",
    #     "libx264",
    #     "-preset",
    #     "ultrafast",
    #     "-pix_fmt",
    #     "yuv420p",
    #     "-profile:v",
    #     "main",
    #     "-level:v",
    #     "4.0",
    #     "-b:v",
    #     bitrate,
    #     "-maxrate",
    #     bitrate,
    #     "-bufsize",
    #     "8M",
    #     "-g",
    #     str(fps * 2),
    #     "-keyint_min",
    #     str(fps * 2),
    #     "-sc_threshold",
    #     "0",
    #     "-c:a",
    #     "aac",
    #     "-profile:a",
    #     "aac_low",
    #     "-b:a",
    #     "128k",
    #     "-ar",
    #     "48000",
    #     "-ac",
    #     "2",
    #     "-f",
    #     "mpegts",
    #     "-mpegts_flags",
    #     "+resend_headers",
    #     url,
    # ]
