from typing import Any
import multiprocessing as mp

INNINGS = [
    "PRE",
    "1st",
    "2nd",
    "3rd",
    "4th",
    "5th",
    "6th",
    "7th",
    "8th",
    "9th",
]


def get_inning(inning: Any) -> str:
    try:
        return INNINGS[int(inning)]
    except (TypeError, ValueError, IndexError):
        return str(inning)


def batting_avg(ab: Any, hits: Any) -> str:
    ab_int = int(ab or 0)
    hits_int = int(hits or 0)
    return ".000" if ab_int == 0 else f"{hits_int / ab_int:.3f}".lstrip("0")


def occupied(value: Any) -> bool:
    return value not in (0, "0", None, "")


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


def get_batter_and_pitcher(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    batter: dict[str, Any] = {}
    pitcher: dict[str, Any] = {}

    situation = payload.get("situation", {})

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

    return batter, pitcher
