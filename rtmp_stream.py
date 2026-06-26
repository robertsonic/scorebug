#!/usr/bin/env python3

import argparse
import os
import subprocess
import time


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--input", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument("--pixfmt", default="bgra")
    parser.add_argument("--url", required=True)

    return parser.parse_args()


def start_ffmpeg(args):
    return subprocess.Popen(
        [
            "ffmpeg",
            "-loglevel",
            "warning",
            "-f",
            "rawvideo",
            "-pix_fmt",
            args.pixfmt,
            "-video_size",
            f"{args.width}x{args.height}",
            "-framerate",
            str(args.fps),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "flv",
            args.url,
        ],
        stdin=subprocess.PIPE,
    )


def read_frame(path, expected_size):
    try:
        with open(path, "rb") as f:
            frame = f.read()
    except Exception as e:
        print(e)
        return None

    if len(frame) != expected_size:
        print(
            f"Skipping incomplete frame: {len(frame)} bytes, expected {expected_size}"
        )
        return None

    return frame


def main():
    args = parse_args()

    expected_size = args.width * args.height * 4
    frame_interval = 1 / args.fps

    ffmpeg = start_ffmpeg(args)

    latest_frame = None
    last_mtime = None

    print(f"Streaming {args.input} to {args.url}")
    print(f"Expected frame size: {expected_size} bytes")

    while True:
        if ffmpeg.poll() is not None:
            print(f"FFmpeg exited with code {ffmpeg.returncode}")
            break

        try:
            mtime = os.path.getmtime(args.input)
        except FileNotFoundError:
            mtime = None

        if mtime is not None and mtime != last_mtime:
            frame = read_frame(args.input, expected_size)

        if frame is not None:
            latest_frame = frame
            last_mtime = mtime

        if latest_frame is not None:
            try:
                ffmpeg.stdin.write(latest_frame)
            except (BrokenPipeError, OSError) as e:
                print(f"Failed writing to FFmpeg: {e}")
                break

        time.sleep(frame_interval)


if __name__ == "__main__":
    main()
