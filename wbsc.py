from datetime import datetime, timedelta
import html
import json
from typing import Any

from bs4 import BeautifulSoup
import requests

comp_map = {
    "bbf_div_1": "2026-nbl",
    "bbf_div_2": "2026-d2",
    "bbf_div_3": "2026-d3",
    "bbf_div_4": "2026-d4",
    "bbf_div_5": "2026-d5",
}


def get_box_score(game_id: str | int, competition: str) -> dict[str, Any]:

    game: dict[str, Any] = {}

    try:
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
        found = True
    except:
        print("Failure to get Box Score with:", competition, game_id)
        found = False

    return {
        "home_name": game.get("homelabel", "Home"),
        "home_short": game.get("homeioc", "HME"),
        "away_name": game.get("awaylabel", "Away"),
        "away_short": game.get("awayioc", "AWY"),
        "location": game.get("stadium", "Ballpark"),
        "start_time": game.get("start", "Soon"),
        "found": found,
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
        new_latest_play = get_latest_play(game_id, latest_play)
        if not new_latest_play > latest_play:
            return {}, latest_play
        try:
            return get_play(game_id, new_latest_play), new_latest_play
        except:
            return {}, new_latest_play

    except Exception as e:
        return {}, latest_play


def get_schedule(competition):
    comp = comp_map[competition]
    url = f"https://stats.britishbaseball.org.uk/en/events/{comp}/schedule-and-results"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": "https://stats.britishbaseball.org.uk/",
        "Connection": "keep-alive",
    }

    session = requests.Session()
    session.headers.update(headers)

    r = session.get(url)

    soup = BeautifulSoup(r.text, "html.parser")

    app = soup.find(id="app")

    if not app:
        raise RuntimeError("Couldn't find #app")

    page = json.loads(app["data-page"])

    print(page.keys())
    print(page["props"].keys())

    games = page["props"]["games"]

    now = datetime.now()
    cutoff = now + timedelta(days=7)

    return [
        game
        for game in games
        if now.replace(hour=0, minute=0, second=0, microsecond=0)
        <= datetime.strptime(
            game.get("start_date", "1970-01-01 00:00:00"), "%Y-%m-%d %H:%M:%S"
        )
        <= cutoff
    ]
