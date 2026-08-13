from __future__ import annotations

import copy
import json
import multiprocessing as mp
import os
import platform
import queue
import random
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ffmpeg_utils import build_stream_state
from scorebug_frame_engine_v4 import run_frame_engine

from utils import (
    get_batter_and_pitcher,
    get_inning,
    batting_avg,
    batter_line,
    occupied,
    get_team_colour,
)
from wbsc import get_box_score, get_play, get_wbsc_data

CONFIG_POLL_INTERVAL = 2
WBSC_POLL_INTERVAL = 3

STATUS_TIMEOUT = 120
FPS = 25

STATUS_MESSAGES = [
    "richmondbaseball.co.uk",
    "Please Donate @ richmondbaseball.co.uk/projects",
    "Youth Programme: richmondbaseball.co.uk/youth",
]


def get_pitch_speed():
    return int(str(random.random() * 100)[:2])


def load_game_if_changed(
    last_mtime: float,
    path: str = "game.json",
) -> tuple[dict[str, Any] | None, float]:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None, last_mtime

    if mtime == last_mtime:
        return None, last_mtime

    with open(path, encoding="utf-8") as file:
        game: dict[str, Any] = json.load(file)

    return game, mtime


def send_latest(updates: mp.Queue, message: dict[str, Any]) -> None:
    # try:
    #     updates.put_nowait(message)
    #     return
    # except queue.Full:
    #     pass
    # try:
    #     updates.get_nowait()
    # except queue.Empty:
    #     pass
    # try:
    #     updates.put_nowait(message)
    # except queue.Full:
    #     pass
    updates.put_nowait(message)


def extract_config_data(game: Any):
    mode = game.get("mode", "game")
    play_lock = int(game.get("play_lock", 0) or 0)

    game_id = int(game.get("id", 0) or 0)
    competition = game.get("competition", "unknown")

    away_colour = game.get("away", {}).get("colour", "FFFFFF")
    home_colour = game.get("home", {}).get("colour", "000000")

    radar = game.get("radar", {})

    srt = game.get("srt", {})

    debug = bool(game.get("debug", False))
    render_debug = bool(game.get("render_debug", False))

    return (
        mode,
        game_id,
        competition,
        away_colour,
        home_colour,
        play_lock,
        radar,
        srt,
        debug,
        render_debug,
    )


def update_game(mode: str, latest_play: int) -> None:
    if mode != "game":
        return


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
            "name": f"{player.get('name', '')} ",
            "stats": f"({batting_avg(season.get('AB', 0), season.get('H', 0))} PA: {season.get('PA', 0)})",
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
            "name": f"{player.get('name', '')}",
            "stats": f"(ER: {season.get('PITCHER', 0)} - BB: {season.get('PITCHBB', 0)} - K: {season.get('PITCHSO', 0)})",
        }
        if key.startswith("1"):
            pitchers["away"] = row
        elif key.startswith("2"):
            pitchers["home"] = row

    lineups["away"].append(pitchers["away"] or {})
    lineups["home"].append(pitchers["home"] or {})

    return {
        "away_lineup": lineups["away"],
        "home_lineup": lineups["home"],
    }


def calculate_elements(
    payload: dict[str, Any], elements: dict[str, Any], new_game: bool
) -> tuple[dict[str, Any], dict[str, Any], set[str]]:

    old_elements = copy.deepcopy(elements)

    now = datetime.now(ZoneInfo("Europe/London"))

    situation = payload.get("situation", {})
    linescore = payload.get("linescore", {})

    away_totals = linescore.get("awaytotals", {})
    home_totals = linescore.get("hometotals", {})

    inning_value = str(situation.get("inning", "0.0"))
    inning_number, _, half = inning_value.partition(".")

    batter, pitcher = get_batter_and_pitcher(payload)

    pitcher_balls = int(pitcher.get("PITCHES", 0) or 0) - int(
        pitcher.get("STRIKES", 0) or 0
    )

    batter_text = (
        f"{batter.get('order', '')}: {str(batter.get('POS', '')).split('/')[-1]} - "
        f"{batter.get('lastname', '')} - ({batter.get('H', 0)}-{batter.get('AB', 0)}) "
    ).strip()

    pitcher_text = (
        f"P: {pitcher.get('lastname', '')} - {pitcher.get('PITCHIP', '')} "
        f"({pitcher_balls}-{pitcher.get('STRIKES', 0)})"
    ).strip()

    status_text = payload.get("status", {}).get("text", "")

    if len(status_text) > 70:
        status_text = status_text[: 70 - 3].rstrip() + "..."

    elements: dict[str, Any] = {
        "away_score": {"text": away_totals.get("R", 0)},
        "home_score": {"text": home_totals.get("R", 0)},
        "away_name": {"text": payload.get("away_name", "AWAY")},
        "home_name": {"text": payload.get("home_name", "HOME")},
        "away_short": {"text": payload.get("away_short", "AWY")},
        "home_short": {"text": payload.get("home_short", "HME")},
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
        "location": {"text": payload.get("location", "Ballpark")},
        "start_time": {"text": payload.get("start_time", "Soon")},
        # "inning_top": {"data": False},
        # "inning_bottom": {"data": False},
        "away_player": {"text": ""},
        "home_player": {"text": ""},
        "status": {
            "text": status_text,
        },
        "clock": {"text": now.strftime("%H:%M %Z")},
    }

    ps = payload.get("pitch_speed", 0)
    if ps:
        elements["pitch_speed"] = {"text": f"{ps} MPH", "fade": True}

    elements = {**elements, **build_lineup_state(payload)}

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

    if situation.get("currentinning", "") == "FINAL":
        payload["status"]["fixed_text"] = status_text
        payload["status"]["fade"] = False
        elements["status"]["fixed_text"] = status_text
        elements["status"]["fade"] = False
        elements["inning_top"] = {"data": False}
        elements["inning_bottom"] = {"data": False}
        elements["outs"]["text"] = "FINAL"

    changed_elements: set[str] = set()

    for k, v in elements.items():
        if v != old_elements.get(k) or new_game:
            # if k == "status":
            #     print(elements[k], old_elements.get(k), elements.get("count", {}))
            changed_elements.add(k)

    elements_to_render = copy.deepcopy(elements)

    if "status" in changed_elements:

        elements_to_render["status"]["fixed_text"] = payload.get("status", {}).get(
            "fixed_text", ""
        )
        elements_to_render["status"]["fade"] = payload.get("status", {}).get(
            "fade", True
        )

    return elements_to_render, elements, changed_elements


def main() -> None:
    # Controller state
    game: dict[str, Any] | None = None
    last_game_mtime = 0.0

    latest_play: int = 0
    game_details: dict[str, Any] | None = None

    radar_process: mp.Process | None = None
    radar_updates: mp.Queue | None = None
    radar_stop_event = mp.Event()

    # Frame engine
    frame_updates: mp.Queue = mp.Queue()
    frame_stop_event = mp.Event()

    frame_process = mp.Process(
        target=run_frame_engine,
        args=(
            frame_updates,
            frame_stop_event,
            {
                "fps": FPS,
                "ffmpeg_command": None,
            },
        ),
        name="frame-engine",
    )

    frame_process.start()

    prev_frame_process_message: dict[str, Any] = {}

    next_config_poll: float = 0.0
    next_wbsc_poll: float = 0.0

    mode: str = "game"
    play_lock: int = 0
    game_id: int = 0
    competition: str

    radar: dict[str, Any] = {}
    srt: dict[str, Any] = {}

    debug = False
    render_debug = False

    render_elements: dict[str, Any] = {}
    elements: dict[str, Any] = {}
    scene: str = "starting"

    wbsc_data: dict[str, Any] = {}
    pitch_speed: int = 0
    status = {}

    try:
        while True:
            now = time.monotonic()
            new_game = None
            message = {}
            latest_scene: str = scene

            if now >= next_config_poll:
                new_game, last_game_mtime = load_game_if_changed(last_game_mtime)
                next_config_poll = now + CONFIG_POLL_INTERVAL

                if new_game is not None:
                    game = new_game
                    message["command"] = "reload"
                    latest_play = 0
                    (
                        mode,
                        game_id,
                        competition,
                        away_colour,
                        home_colour,
                        play_lock,
                        radar,
                        srt,
                        debug,
                        render_debug,
                    ) = extract_config_data(game)

            if mode == "game":

                if new_game is not None:
                    wbsc_data = get_box_score(game_id, competition)

                if now >= next_wbsc_poll or new_game is not None:
                    next_wbsc_poll = now + WBSC_POLL_INTERVAL

                    if play_lock == 0:
                        new_wbsc_data, latest_play = get_wbsc_data(game_id, latest_play)
                        wbsc_data = {**wbsc_data, **new_wbsc_data}
                    elif play_lock == -1:

                        latest_play = latest_play if latest_play > 0 else 1

                        divisor = 3 if latest_play > 1 else 5

                        random_play = (
                            latest_play + 1
                            if random.random() < 1 / divisor
                            else latest_play
                        )
                        if random_play != latest_play or new_game is not None:
                            latest_play = random_play
                            wbsc_data = {**wbsc_data, **get_play(game_id, latest_play)}
                    elif latest_play != play_lock:
                        latest_play = play_lock
                        wbsc_data = {**wbsc_data, **get_play(game_id, latest_play)}

                    statuses = STATUS_MESSAGES.copy()
                    platecount = wbsc_data.get("platecount", False)
                    status_text: str = ""
                    if platecount:
                        status_text = " ".join(
                            str(platecount[0].get("label", "")).split("<br>")
                        )

                    if not latest_play:
                        latest_scene = "starting"
                    elif latest_play == 1:
                        latest_scene = "lineup"
                        statuses = [status_text]
                    else:
                        latest_scene = "scorebug"
                        if radar.get("active", False):
                            pitch_speed = random.choice(
                                [45, 77, 90, 100, 32, 61]
                            )  # get_pitch_speed()

                        batter, pitcher = get_batter_and_pitcher(wbsc_data)
                        b_line = batter_line(batter).strip()
                        if b_line:
                            statuses.extend([f"Previous At Bats: {b_line}"] * 3)

                    status = {
                        "text": status_text,
                        "fixed_text": random.choice(statuses),
                    }

            if scene != latest_scene:
                message["command"] = "reload"
                elements = {}  # Refresh all elements

            render_elements, elements, changed_elements = calculate_elements(
                {**wbsc_data, "pitch_speed": pitch_speed, "status": status},
                elements,
                new_game,
            )

            if len(changed_elements) > 0:
                message = {
                    **message,
                    "elements": {key: render_elements[key] for key in changed_elements},
                }

            scene = latest_scene
            print(latest_scene)
            message = {
                **message,
                "scene": latest_scene,
                "stream": build_stream_state(srt),
                "debug": game.get("debug", False),
                "render_debug": game.get("render_debug", False),
                "state": {
                    "competition": competition,
                    "away_colour": away_colour,
                    "home_colour": home_colour,
                },
            }

            if message != prev_frame_process_message:
                print("Changes with play", latest_play, ":", changed_elements)
                send_latest(frame_updates, message)
                prev_frame_process_message = copy.deepcopy(message)
            else:
                ...

            time.sleep(0.75)

    except KeyboardInterrupt:
        pass

    except Exception as e:
        print(e)
        pass

    finally:
        frame_stop_event.set()
        frame_process.join(timeout=3)

        if frame_process.is_alive():
            frame_process.terminate()
            frame_process.join()


if __name__ == "__main__":
    mp.freeze_support()
    main()
