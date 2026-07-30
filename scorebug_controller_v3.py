from __future__ import annotations

import copy
import json
import math
import multiprocessing as mp
import os
import queue
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any
import platform
import requests
import html
import json
from bs4 import BeautifulSoup

from scorebug_frame_engine_v3 import run_frame_engine

POLL_INTERVAL = 3
STATUS_TIMEOUT = 120
FPS = 25
INNINGS = ["PRE", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th"]

STATUS_MSGS = [
    "richmondbaseball.co.uk",
    "Please Donate @ richmondbaseball.co.uk/projects",
    "Youth Programme: richmondbaseball.co.uk/youth",
]

reset_elements = {
    "away_score": {"text": ""},
    "home_score": {"text": ""},
    "away_name": {"text": ""},
    "home_name": {"text": ""},
    "inning": {"text": ""},
    "outs": {"text": f""},
    "count": {"text": f""},
    "runner1": False,
    "runner2": False,
    "runner3": False,
    "inning_top": {"data": False},
    "inning_bottom": {"data": False},
    "away_player": {"text": ""},
    "home_player": {"text": ""},
    "status": {"text": "", "fade": False},
    "clock": {"text": ""},
    "pitch_speed": {"text": ""},
}
old_elements = copy.deepcopy(reset_elements)

status_msg_index = math.floor(random.random() * len(STATUS_MSGS))


def get_pitch_speed(now: datetime) -> str:
    return str(random.random() * 100)
    response = requests.get(
        f"http://192.168.55.105:1992/latest",
        timeout=1,
    )
    response.raise_for_status()

    radar_data = response.json()

    if not radar_data.get("available", False):
        return ""

    event = radar_data.get("event", None)

    if event is None:
        return ""

    timestamp: float = event.get("timestamp", 0)

    if now.timestamp() - timestamp > 15:
        return ""

    if event.get("sample_count", 0) < 15:
        return ""

    return str(event["speed_mph"]) if event["direction"] == "approaching" else ""


def get_box_score(game_id: str | int, compeition: str) -> dict[str, Any]:

    comp_map = {
        "bbf_div_1": "2026-d1",
        "bbf_div_2": "2026-d2",
        "bbf_div_3": "2026-d3",
        "bbf_div_4": "2026-d4",
        "bbf_div_5": "2026-d5",
    }

    comp = comp_map[compeition]

    response = requests.get(
        f"https://stats.britishbaseball.org.uk/en/events/{comp}/schedule-and-results/box-score/{game_id}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html",
        },
        timeout=10,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    page = json.loads(html.unescape(soup.find("div", id="app")["data-page"]))

    game = page["props"]["viewData"]["original"]["gameData"]

    return game


def get_latest_play(game_id: str | int) -> int:
    response = requests.get(
        f"https://game.wbsc.org/gamedata/{game_id}/latest.json", timeout=10
    )
    response.raise_for_status()
    return int(response.text.strip())


def get_play(game_id: str | int, play_number: int) -> dict[str, Any]:
    url = f"https://game.wbsc.org/gamedata/{game_id}/play{play_number}.json"
    print(url)
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def batting_avg(ab: Any, hits: Any) -> str:
    ab_int = int(ab or 0)
    hits_int = int(hits or 0)
    return ".000" if ab_int == 0 else f"{hits_int / ab_int:.3f}".lstrip("0")


def occupied(value: Any) -> bool:
    return value not in (0, "0", None, "")


def get_inning(inning: Any) -> str:
    try:
        return INNINGS[int(inning)]
    except (TypeError, ValueError, IndexError):
        return str(inning)


def calculate_up_arrow(x: int, y: int, size: int = 10) -> list[tuple[int, int]]:
    return [(x, y - size), (x + size, y + size), (x - size, y + size)]


def calculate_down_arrow(x: int, y: int, size: int = 10) -> list[tuple[int, int]]:
    return [(x - size, y - size), (x + size, y - size), (x, y + size)]


def calculate_base(cx: int, cy: int) -> list[tuple[int, int]]:
    size = 32
    return [(cx, cy - size), (cx + size, cy), (cx, cy + size), (cx - size, cy)]


def get_team_colour(team: Any, default: str) -> str:
    if not isinstance(team, dict):
        return default
    colour = team.get("colour") or default
    return str(colour).replace("#", "")


def batter_line(batter: dict[str, Any]) -> str:
    mappings = [
        ("2B", "DOUBLE"),
        ("3B", "TRIPLE"),
        ("HR", "HR"),
        ("K", "SO"),
        ("BB", "BB"),
        ("HBP", "HBP"),
        ("SF", "SF"),
        ("SB", "SB"),
    ]
    result = []
    for label, key in mappings:
        value = int(batter.get(key, 0) or 0)
        if value == 1:
            result.append(label)
        elif value > 1:
            result.append(f"{value} {label}")
    return ", ".join(result)


def calculate_elements(
    payload: dict[str, Any], new_game: bool
) -> tuple[dict[str, Any], set[str]]:
    global old_elements
    global clock

    now = datetime.now(ZoneInfo("Europe/London"))
    situation = payload.get("situation", {})
    linescore = payload.get("linescore", {})
    away_totals = linescore.get("awaytotals", {})
    home_totals = linescore.get("hometotals", {})

    inning_value = str(situation.get("inning", "0.0"))
    inning_number, _, half = inning_value.partition(".")

    statuses = STATUS_MSGS.copy()

    batter: dict[str, Any] = {}
    pitcher: dict[str, Any] = {}
    for player_num, player in payload.get("boxscore", {}).items():
        if (
            player.get("playerid") == situation.get("batterid")
            and "PITCHES" not in player
        ):
            batter = dict(player)
            try:
                batter["order"] = str(int(player_num[1:3]))
            except (ValueError, TypeError):
                batter["order"] = ""
        if player.get("playerid") == situation.get("pitcherid") and "PITCHES" in player:
            pitcher = dict(player)

    pitcher_balls = int(pitcher.get("PITCHES", 0) or 0) - int(
        pitcher.get("STRIKES", 0) or 0
    )

    platecount = payload.get("platecount") or []
    status_text = ""

    if platecount:
        status_text = " ".join(str(platecount[0].get("label", "")).split("<br>"))

    batter_text = (
        f"{batter.get('order', '')}: {str(batter.get('POS', '')).split('/')[-1]} - "
        f"{batter.get('lastname', '')} - ({batter.get('H', 0)}-{batter.get('AB', 0)}) "
    ).strip()

    b_line = batter_line(batter).strip()

    if b_line:
        statuses.extend([f"Previous At Bats: {b_line}"] * 6)

    pitcher_text = (
        f"P: {pitcher.get('lastname', '')} - {pitcher.get('PITCHIP', '')} "
        f"({pitcher_balls}-{pitcher.get('STRIKES', 0)})"
    ).strip()

    if len(status_text) > 70:
        status_text = status_text[: 70 - 3].rstrip() + "..."

    elements: dict[str, Any] = {
        "away_score": {"text": away_totals.get("R", 0)},
        "home_score": {"text": home_totals.get("R", 0)},
        "away_name": {"text": payload.get("eventaway", "AWAY")},
        "home_name": {"text": payload.get("eventhome", "HOME")},
        "inning": {"text": get_inning(inning_number)},
        "outs": {"text": f"{situation.get('outs', 0)} OUT"},
        "count": {"text": f"{situation.get('balls', 0)}-{situation.get('strikes', 0)}"},
        "bases": {
            "data": [
                occupied(situation.get("runner1")),
                occupied(situation.get("runner2")),
                occupied(situation.get("runner3")),
            ]
        },
        "inning_top": {"data": False},
        "inning_bottom": {"data": False},
        "away_player": {"text": ""},
        "home_player": {"text": ""},
        "status": {
            "text": status_text,
        },
        "clock": {"text": now.strftime("%H:%M %Z")},
        "pitch_speed": {"text": get_pitch_speed(now)},
    }

    if half == "0":
        elements["away_player"]["text"] = batter_text
        elements["home_player"]["text"] = pitcher_text
        elements["inning_top"] = {"data": True}
        elements["inning_bottom"] = {"data": False}
    else:
        elements["home_player"]["text"] = batter_text
        elements["away_player"]["text"] = pitcher_text
        elements["inning_top"] = {"data": False}
        elements["inning_bottom"] = {"data": True}

    changed_elements: set[str] = set()

    for k, v in elements.items():
        if v != old_elements.get(k) or new_game:

            if k == "status":
                elements[k]["fixed_text"] = random.choice(statuses)
                elements[k]["fade"] = True

            changed_elements.add(k)

    old_elements = copy.deepcopy(elements)

    return elements, changed_elements


def build_lineup_state(payload: dict[str, Any]) -> dict[str, Any]:
    lineups: dict[str, list[dict[str, Any]]] = {"away": [], "home": []}
    seen: set[tuple[str, int]] = set()

    for key, player in reversed(list(payload.get("boxscore", {}).items())):
        if len(key) < 3 or "PITCHES" in player:
            continue
        try:
            team_side = key[0]
            batting_order = int(key[2])
        except (ValueError, IndexError):
            continue
        if not 1 <= batting_order <= 9 or (team_side, batting_order) in seen:
            continue
        seen.add((team_side, batting_order))
        season = player.get("SEASON", {})
        row = {
            "order": batting_order,
            "pos": player.get("POS", ""),
            "display": (
                f"{player.get('name', '')} "
                f"({batting_avg(season.get('AB', 0), season.get('H', 0))} "
                f"PA: {season.get('PA', 0)})"
            ),
        }
        if team_side == "1":
            lineups["away"].append(row)
        elif team_side == "2":
            lineups["home"].append(row)

    for side in ("away", "home"):
        lineups[side].sort(key=lambda player: player["order"])
        lineups[side].append({})

    pitchers: dict[str, dict[str, Any] | None] = {"away": None, "home": None}
    for key, player in payload.get("boxscore", {}).items():
        if "PITCHES" not in player:
            continue
        season = player.get("SEASON", {})
        row = {
            "is_pitcher": True,
            "display": (
                f"{player.get('name', '')} (ER: {season.get('PITCHER', 0)} "
                f"BB: {season.get('PITCHBB', 0)} K: {season.get('PITCHSO', 0)})"
            ),
        }
        if key.startswith("1"):
            pitchers["away"] = row
        elif key.startswith("2"):
            pitchers["home"] = row

    lineups["away"].append(pitchers["away"] or {})
    lineups["home"].append(pitchers["home"] or {})

    return {
        "away_name": payload.get("eventaway", "AWAY"),
        "home_name": payload.get("eventhome", "HOME"),
        "away_lineup": lineups["away"],
        "home_lineup": lineups["home"],
    }


def load_game_if_changed(last_mtime: float) -> tuple[dict[str, Any] | None, float]:
    try:
        mtime = os.path.getmtime("game.json")
    except OSError:
        return None, last_mtime
    if mtime == last_mtime:
        return None, last_mtime
    with open("game.json", encoding="utf-8") as file:
        return json.load(file), mtime


def send_latest(updates: mp.Queue, message: dict[str, Any]) -> None:
    try:
        updates.put_nowait(message)
        return
    except queue.Full:
        pass
    try:
        updates.get_nowait()
    except queue.Empty:
        pass
    try:
        updates.put_nowait(message)
    except queue.Full:
        pass


def build_stream_state(game: dict[str, Any]) -> dict[str, Any]:
    srt = game.get("srt", {})
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

    video_preset = ["libx264", "-preset", "ultrafast"]

    if platform.system() == "Linux":
        video_preset = ["h264_v4l2m2m"]

    return (
        [
            "ffmpeg",
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


def main() -> None:
    global old_elements

    updates: mp.Queue = mp.Queue(maxsize=1)
    stop_event = mp.Event()

    frame_process = mp.Process(
        target=run_frame_engine,
        args=(
            updates,
            stop_event,
            {
                "fps": FPS,
                "ffmpeg_command": None,
            },
        ),
        name="frame-engine",
    )
    frame_process.start()

    game: dict[str, Any] | None = None
    last_game_mtime = 0.0
    last_play = 0
    status_timer = STATUS_TIMEOUT

    game_details: dict = {}
    message: dict = {}

    try:
        while True:
            new_game, last_game_mtime = load_game_if_changed(last_game_mtime)

            if new_game is not None:
                previous_game_id = game.get("id") if game else None
                game = new_game
                message = {}

                if game.get("id") != previous_game_id:
                    last_play = 1
                    status_timer = STATUS_TIMEOUT

                send_latest(
                    updates,
                    {
                        "command": "stream",
                        "stream": build_stream_state(game),
                    },
                )

            if game is None:
                time.sleep(POLL_INTERVAL)
                continue

            game_id = game["id"]
            competition = game.get("competition")

            try:
                game_details = get_box_score(game.get("id"), game.get("competition"))
            except Exception as e:

                send_latest(
                    updates,
                    {"command": "blank", "stream": build_stream_state(game)},
                )
                time.sleep(POLL_INTERVAL)
                continue

            home = game.get("home", {})
            away = game.get("away", {})

            home["name"] = game_details.get("homelabel", "Home Team")
            away["name"] = game_details.get("awaylabel", "Away Team")

            home["short_name"] = game_details.get("homeioc", "HME")
            away["short_name"] = game_details.get("awayioc", "AWY")

            home_colour = get_team_colour(home, "FFFFFF")
            away_colour = get_team_colour(away, "000000")
            play_lock = int(game.get("play_lock", 0) or 0)

            location = game_details.get("stadium", "Ballpark")
            start_time = game_details.get("start", None)

            if start_time is not None:
                start_time = datetime.strptime(
                    start_time, "%Y-%m-%d %H:%M:%S"
                ).strftime("%H:%M:%S")
            else:
                start_time = datetime.now().strftime("%Y-%m-%d")

            debug: bool = game.get("debug", False)
            render_debug: bool = game.get("render_debug", False)

            mode = game.get("mode", "game")

            try:
                latest_play = play_lock

                if play_lock < 0:
                    randy = random.random()
                    if debug:
                        print(
                            f"Random Number: {randy}--------------------------------------------------------------------------------"
                        )
                    if randy < ((1 / 3) if latest_play > 1 else (1 / 10)):
                        latest_play = (last_play + 1) if last_play > 1 else 2
                    else:
                        latest_play = last_play
                elif play_lock < 1:
                    try:
                        latest_play = get_latest_play(game_id)
                    except requests.exceptions.HTTPError as e:
                        # Fall back to basic game_data
                        if message.get("scene", None) != "starting":
                            message = {"command": "update", "scene": "starting"}
                        else:
                            raise

                if mode == "pitching":
                    speed = get_pitch_speed(datetime.now())
                    if not speed:
                        continue
                    print(speed)
                    send_latest(
                        updates,
                        {
                            "command":" update",
                            "scene": "pitching",
                            "stream": build_stream_state(game),
                            "state": {
                                "elements": (
                                    {"pitch_speed": {"text": speed}},
                                    ["pitch_speed"],
                                )
                            },
                        },
                    )
                    time.sleep(POLL_INTERVAL)
                    continue

                if (
                    latest_play > last_play
                    or status_timer >= STATUS_TIMEOUT
                    or new_game is not None
                    or message.get("scene", "") == "starting"
                ):
                    try:
                        payload = get_play(game_id, latest_play)

                    except requests.exceptions.HTTPError as e:
                        error_code = (
                            e.response.status_code if e.response is not None else 500
                        )

                        if error_code == 404:
                            last_play += 1
                        else:
                            raise

                    common = {
                        "competition": competition,
                        "home_colour": home_colour,
                        "away_colour": away_colour,
                        "home_name": home["name"],
                        "away_name": away["name"],
                        "home_short": home["short_name"],
                        "away_short": away["short_name"],
                        "location": location,
                        "start_time": start_time,
                    }

                    if message.get("scene", None) == "starting":
                        message = {
                            **message,
                            "state": common,
                            "stream": build_stream_state(game),
                        }
                    elif latest_play == 1:
                        state = {**build_lineup_state(payload), **common}
                        state.update(common)
                        message = {
                            "command": "update",
                            "scene": "lineup",
                            "state": state,
                            "stream": build_stream_state(game),
                        }
                        old_elements = copy.deepcopy(reset_elements)

                    else:
                        state = {
                            "elements": calculate_elements(payload, new_game),
                            **common,
                        }
                        message = {
                            "command": "update",
                            "scene": "scorebug",
                            "state": state,
                            "stream": build_stream_state(game),
                        }

                    message["reload_assets"] = (new_game is not None) or (
                        last_play == 1 and last_play != latest_play
                    )

                    send_latest(
                        updates,
                        {**message, "debug": debug, "render_debug": render_debug},
                    )
                    print(f"Sent graphic state for play {latest_play}")

                    new_game = None
                    last_play = latest_play
                    status_timer = 0
            except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
                print(f"Data update failed: {exc}")

            time.sleep(POLL_INTERVAL)
            status_timer += POLL_INTERVAL

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        frame_process.join(timeout=3)
        if frame_process.is_alive():
            frame_process.terminate()
            frame_process.join()


if __name__ == "__main__":
    mp.freeze_support()
    main()
