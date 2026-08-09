import html
import json
from typing import Any

from bs4 import BeautifulSoup
import requests


def get_box_score(game_id: str | int, competition: str) -> dict[str, Any]:

    comp_map = {
        "bbf_div_1": "2026-d1",
        "bbf_div_2": "2026-d2",
        "bbf_div_3": "2026-d3",
        "bbf_div_4": "2026-d4",
        "bbf_div_5": "2026-d5",
    }

    comp = comp_map[competition]

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

    return {
        "home_name": game.get("homelabel", "Home"),
        "home_short": game.get("homeioc", "HME"),
        "away_name": game.get("awaylabel", "Away"),
        "away_short": game.get("awayioc", "AWY"),
        "location": game.get("stadium", "Ballpark"),
        "start_time": game.get("start", None),
    }


def get_latest_play(game_id: str | int, latest_play: int) -> int:
    response = requests.get(
        f"https://game.wbsc.org/gamedata/{game_id}/latest.json", timeout=10
    )
    response.raise_for_status()
    new = int(response.text.strip())
    return new if new > latest_play else latest_play


def get_play(game_id: str | int, play_number: int) -> dict[str, Any]:
    url = f"https://game.wbsc.org/gamedata/{game_id}/play{play_number}.json"
    print(url)
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    play = response.json()
    return {
        "home_short": play.get("eventhome", "HME"),
        "away_short": play.get("eventaway", "AWY"),
        "situation": play.get("situation", {}),
        "boxscore": play.get("boxscore", {}),
        "linescore": play.get("linescore", {}),
        "platecount": play.get("platecount", []),
    }


def get_wbsc_data(game_id: str | int, latest_play: int = 0) -> dict[str, Any]:

    try:
        current_latest_play = get_latest_play(game_id, latest_play)

        try:
            return get_play(game_id, current_latest_play)
        except:
            return {}

    except Exception as e:
        return {}
