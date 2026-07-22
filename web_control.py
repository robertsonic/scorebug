from flask import Flask, request, jsonify, render_template, Response
import queue
import json
import os
import tempfile
import requests
from bs4 import BeautifulSoup
import re
import subprocess
import threading

app = Flask(__name__)
GAME_FILE = "game.json"
EVENT_CLIENTS = []
EVENT_CLIENTS_LOCK = threading.Lock()

SRT_RUNTIME_STATE = {
    "live": False,
    "status": "OFFLINE",
    "message": "SRT is OFFLINE",
    "pid": None,
}


def send_event(event, data):
    message = {
        "event": event,
        "data": data,
    }

    with EVENT_CLIENTS_LOCK:
        clients = list(EVENT_CLIENTS)

    for client_queue in clients:
        try:
            client_queue.put_nowait(message)
        except queue.Full:
            try:
                client_queue.get_nowait()
                client_queue.put_nowait(message)
            except (queue.Empty, queue.Full):
                pass


def run_cmd(cmd):
    try:
        return subprocess.check_output(
            cmd, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


def load_game():
    with open(GAME_FILE) as f:
        return json.load(f)


def save_game(data):
    fd, tmp = tempfile.mkstemp(dir=".", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, GAME_FILE)


def competition_buttons():
    competitions = [
        ("BBF NBL", "bbf_div_1"),
        ("BBF Div 2", "bbf_div_2"),
        ("BBF Div 3", "bbf_div_3"),
        ("BBF Div 4", "bbf_div_4"),
        ("BBF Div 5", "bbf_div_5"),
    ]

    html = ""

    for label, value in competitions:
        html += f"""
            <button
                type="button"
                onclick="setCompetition('{value}')"
            >
                {label}
            </button>
        """

    return html


def colour_buttons(field_id):
    colours = [
        ("Black", "000000", "black"),
        ("White", "FFFFFF", "white"),
        ("Red", "FF0000", "red"),
        ("Dark Red", "8B0000", "darkred"),
        ("Blue", "0066FF", "blue"),
        ("Dark Blue", "00008B", "darkblue"),
        ("Orange", "FF8800", "orange"),
        ("Green", "00AA44", "green"),
        ("Dark Green", "006400", "darkgreen"),
        ("Yellow", "FFFF00", "yellow"),
    ]

    html = ""

    for label, value, css_colour in colours:
        extra_class = "white" if value == "FFFFFF" else ""
        html += f"""
            <button
                type="button"
                class="swatch {extra_class}"
                style="background: #{value};"
                onclick="setColour('{field_id}', '{value}')"
            >
                {label}
            </button>
        """

    return html


def fetch_games(competition):
    # TODO: replace this with the real WBSC endpoint once known
    urls = {
        "bbf_div_3": "https://stats.britishbaseball.org.uk/en/events/2026-d3/home",
        "bbf_div_2": "https://stats.britishbaseball.org.uk/en/events/2026-d2/home",
        "bbf_div_4": "https://stats.britishbaseball.org.uk/en/events/2026-d4/home",
        "bbf_div_5": "https://stats.britishbaseball.org.uk/en/events/2026-d5/home",
    }
    print(f"Fetching games for {competition}")

    url = urls.get(competition)
    if not url:
        return []

    response = requests.get(
        url,
        timeout=10,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        },
        allow_redirects=True,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    games = []

    for row in soup.select(".homepage-game-row"):
        score_span = row.select_one(".game-score span[class^='away']")
        if not score_span:
            continue

        match = re.search(r"away(\d+)", " ".join(score_span.get("class", [])))
        if not match:
            continue

        game_id = int(match.group(1))

        teams = [t.get_text(strip=True) for t in row.select(".team-name")]
        if len(teams) != 2:
            continue

        away, home = teams

        game_time_text = row.select_one(".game-time").get_text(" ", strip=True)

        games.append(
            {
                "id": game_id,
                "date": game_time_text,
                "time": "",
                "away": away,
                "home": home,
            }
        )

    return games


@app.route("/events")
def events():
    client_queue = queue.Queue(maxsize=20)

    with EVENT_CLIENTS_LOCK:
        EVENT_CLIENTS.append(client_queue)

    def stream():
        try:
            while True:
                msg = client_queue.get()

                yield f"event: {msg['event']}\n"
                yield f"data: {json.dumps(msg['data'])}\n\n"
        finally:
            with EVENT_CLIENTS_LOCK:
                if client_queue in EVENT_CLIENTS:
                    EVENT_CLIENTS.remove(client_queue)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/network-status")
def api_network_status():
    return jsonify(
        {
            "default_route": run_cmd(["ip", "route", "get", "8.8.8.8"]),
            "routes": run_cmd(["ip", "route"]),
            "addresses": run_cmd(["hostname", "-I"]),
            "clients": run_cmd(["cat", "/var/lib/misc/dnsmasq.leases"]),
            "ip_forward": run_cmd(["sysctl", "-n", "net.ipv4.ip_forward"]),
        }
    )


@app.route("/api/upcoming-games")
def api_upcoming_games():
    competition = request.args.get("competition", "").strip()

    if not competition:
        return jsonify([])

    return jsonify(fetch_games(competition))


@app.route("/")
def index():
    game = load_game()

    home_colour = game.get("home", {}).get("colour", "FFFFFF")
    away_colour = game.get("away", {}).get("colour", "000000")

    return render_template(
        "index.html",
        game=game,
        home_colour=home_colour,
        away_colour=away_colour,
        competition_buttons=competition_buttons(),
        away_buttons=colour_buttons("away_colour"),
        home_buttons=colour_buttons("home_colour"),
    )


@app.route("/style.css")
def css():
    with open("static/style.css") as f:
        return Response(f.read(), mimetype="text/css")


@app.route("/save", methods=["POST"])
def save():

    play_lock_raw = request.form.get("play_lock", "").strip()
    play_lock = 0

    if play_lock_raw:
        play_lock = int(play_lock_raw)

    data = load_game()

    data.update(
        {
            "id": int(request.form["id"]),
            "competition": request.form["competition"],
            "home": {"colour": request.form["home_colour"].replace("#", "")},
            "away": {"colour": request.form["away_colour"].replace("#", "")},
            "play_lock": play_lock,
        }
    )

    save_game(data)
    return jsonify({"ok": True})
    # return """
    # <html>
    # <head>
    #     <title>Saved</title>
    #     <style>
    #         body {
    #             font-family: Arial, sans-serif;
    #             text-align: center;
    #             margin-top: 100px;
    #         }

    #         button {
    #             font-size: 24px;
    #             padding: 20px 40px;
    #             margin-top: 40px;
    #         }
    #     </style>
    # </head>
    # <body>
    #     <h1>✅ Settings saved</h1>

    #     <button onclick="window.history.back()">
    #         ← Back
    #     </button>
    # </body>
    # </html>
    # """


@app.get("/api/srt")
def get_srt():
    game = load_game()

    srt = game.get(
        "srt",
        {
            "live_requested": False,
            "url": "",
            "width": 1920,
            "height": 1080,
            "fps": 25,
            "bitrate": "4M",
        },
    )

    return {
        "srt": srt,
        "live": SRT_RUNTIME_STATE["live"],
        "status": SRT_RUNTIME_STATE["status"],
        "message": SRT_RUNTIME_STATE["message"],
        "pid": SRT_RUNTIME_STATE["pid"],
    }


@app.post("/api/srt")
def save_srt():
    data = request.json or {}
    game = load_game()

    existing = game.get("srt", {})

    game["srt"] = {
        "live_requested": bool(existing.get("live_requested", False)),
        "url": str(data.get("url", "")).strip(),
        "width": int(data.get("width", 1920)),
        "height": int(data.get("height", 1080)),
        "fps": int(data.get("fps", 25)),
        "bitrate": str(data.get("bitrate", "4M")),
    }

    save_game(game)

    return {
        "ok": True,
        "srt": game["srt"],
    }


@app.post("/api/srt/state")
def update_srt_state():
    data = request.json or {}

    SRT_RUNTIME_STATE["live"] = bool(data.get("live", False))
    SRT_RUNTIME_STATE["status"] = str(
        data.get(
            "status",
            "LIVE" if SRT_RUNTIME_STATE["live"] else "OFFLINE",
        )
    )
    SRT_RUNTIME_STATE["message"] = str(data.get("message", ""))
    SRT_RUNTIME_STATE["pid"] = data.get("pid")

    send_event("srt", dict(SRT_RUNTIME_STATE))

    return {
        "ok": True,
        "srt_state": SRT_RUNTIME_STATE,
    }


@app.post("/api/srt/live")
def request_srt_live():
    data = request.json or {}
    live_requested = bool(data.get("live", False))

    game = load_game()
    srt = game.setdefault("srt", {})

    if live_requested and not str(srt.get("url", "")).strip():
        return {
            "ok": False,
            "message": "An SRT URL is required",
        }, 400

    srt["live_requested"] = live_requested
    save_game(game)

    send_event(
        "srt",
        {
            "live": SRT_RUNTIME_STATE["live"],
            "status": "starting" if live_requested else "stopping",
            "message": ("Starting SRT..." if live_requested else "Stopping SRT..."),
            "pid": SRT_RUNTIME_STATE["pid"],
        },
    )

    return {
        "ok": True,
        "live_requested": live_requested,
    }


app.run(host="0.0.0.0", port=8080)
