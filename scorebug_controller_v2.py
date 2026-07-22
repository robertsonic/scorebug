from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import queue
import random
import time
from typing import Any

import requests

from scorebug_frame_engine_v2 import run_frame_engine

POLL_INTERVAL = 3
STATUS_TIMEOUT = 120
FPS = 25
INNINGS = ["PRE", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th"]

STATUS_MSGS = [
    "richmondbaseball.co.uk",
    "Please Donate @ richmondbaseball.co.uk/projects",
    "Youth Programme: richmondbaseball.co.uk/youth",
]

status_msg_index = math.floor(random.random() * len(STATUS_MSGS))

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
    payload: dict[str, Any], home_colour: str, away_colour: str
) -> dict[str, Any]:

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
    bases = []
    if occupied(situation.get("runner1")):
        bases.append(calculate_base(1670, 932))
    if occupied(situation.get("runner2")):
        bases.append(calculate_base(1629, 890))
    if occupied(situation.get("runner3")):
        bases.append(calculate_base(1588, 932))

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
        "away_score": {"text": away_totals.get("R", 0), "colour": away_colour},
        "home_score": {"text": home_totals.get("R", 0), "colour": home_colour},
        "away_name": {"text": payload.get("eventaway", "AWAY")},
        "home_name": {"text": payload.get("eventhome", "HOME")},
        "inning": {"text": get_inning(inning_number)},
        "outs": {"text": f"{situation.get('outs', 0)} OUT"},
        "count": {"text": f"{situation.get('balls', 0)}-{situation.get('strikes', 0)}"},
        "bases": {"points": bases},
        "away_player": {"text": ""},
        "home_player": {"text": ""},
        "status": {
            "text": status_text,
            "started_ns": time.perf_counter_ns(),
            "hold_ns": 5_000_000_000,
            "fade_ns": 1_000_000_000,
            "fixed_text": random.choice(statuses),
        },
    }

    if half == "0":
        elements["away_player"]["text"] = batter_text
        elements["home_player"]["text"] = pitcher_text
        elements["inning"]["points"] = calculate_up_arrow(1740, 885)
    else:
        elements["home_player"]["text"] = batter_text
        elements["away_player"]["text"] = pitcher_text
        elements["inning"]["points"] = calculate_down_arrow(1740, 887)

    return elements


def build_lineup_state(
    payload: dict[str, Any], home_colour: str, away_colour: str
) -> dict[str, Any]:
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
        "away_colour": away_colour,
        "home_colour": home_colour,
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


def main() -> None:
    updates: mp.Queue = mp.Queue(maxsize=1)
    stop_event = mp.Event()

    frame_process = mp.Process(
        target=run_frame_engine,
        args=(
            updates,
            stop_event,
            {
                "fps": FPS,
                "ffmpeg_command": [
                    "ffmpeg",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "bgra",
                    "-video_size",
                    "1920x1080",
                    "-framerate",
                    "25",
                    "-i",
                    "pipe:0",
                    "-stream_loop",
                    "-1",
                    "-i",
                    "sonican-blues-rock-victory-inspirational-loop-465097.mp3",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-c:a",
                    "aac",
                    "-f",
                    "mpegts",
                    # "srt://localhost:8890?mode=caller&streamid=publish:scorebug",
                    "srt://13.60.22.249:8890?mode=caller&streamid=publish:scorebug",
                ],
            },
        ),
        name="frame-engine",
    )
    frame_process.start()

    game: dict[str, Any] | None = None
    last_game_mtime = 0.0
    last_play = 0
    status_timer = STATUS_TIMEOUT

    try:
        while True:
            new_game, last_game_mtime = load_game_if_changed(last_game_mtime)
            if new_game is not None:
                game = new_game
                last_play = 1
                status_timer = STATUS_TIMEOUT

            if game is None:
                time.sleep(POLL_INTERVAL)
                continue

            game_id = game["id"]
            home = game.get("home", {})
            away = game.get("away", {})
            home_colour = get_team_colour(home, "FFFFFF")
            away_colour = get_team_colour(away, "000000")
            play_lock = int(game.get("play_lock", 0) or 0)

            try:
                latest_play = 1
                if play_lock < 0:
                    if random.random() < 1 / 3:
                        latest_play = (last_play + 1) if last_play > 1 else 2
                    else:
                        latest_play = last_play
                elif play_lock < 1:
                    latest_play = get_latest_play(game_id)

                if latest_play > last_play or status_timer >= STATUS_TIMEOUT:
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

                    common = {"competition": game.get("competition")}

                    if latest_play == 1:
                        state = build_lineup_state(payload, home_colour, away_colour)
                        state.update(common)
                        message = {
                            "command": "update",
                            "scene": "lineup",
                            "state": state,
                        }
                    else:
                        state = {
                            "elements": calculate_elements(
                                payload, home_colour, away_colour
                            ),
                            **common,
                        }
                        message = {
                            "command": "update",
                            "scene": "scorebug",
                            "state": state,
                        }

                    send_latest(updates, message)
                    print(f"Sent graphic state for play {latest_play}")
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
