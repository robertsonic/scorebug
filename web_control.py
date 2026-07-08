from flask import Flask, request, jsonify, render_template, Response
import queue
import threading
import json
import os
import tempfile
import requests
from bs4 import BeautifulSoup
import re
import subprocess
import sys
import psutil

app = Flask(__name__)
GAME_FILE = "game.json"
EVENTS = queue.Queue()
RTMP_PROCESS = None


def send_event(event, data):
    EVENTS.put({"event": event, "data": data})


def run_cmd(cmd):
    try:
        return subprocess.check_output(
            cmd, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


def watch_process_output(proc):
    for line in proc.stdout:
        send_event("rtmp_log", {"line": line.rstrip()})

    send_event(
        "rtmp",
        {
            "live": False,
            "message": "RTMP process exited",
        },
    )


def start_rtmp(ff, w, h, f, url):
    return subprocess.Popen(
        [
            sys.executable,
            "rtmp_stream.py",
            "--input",
            str(ff),
            "--width",
            str(w),
            "--height",
            str(h),
            "--fps",
            str(f),
            "--url",
            url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


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
    def stream():
        while True:
            msg = EVENTS.get()
            yield f"event: {msg['event']}\n"
            yield f"data: {json.dumps(msg['data'])}\n\n"

    return Response(stream(), mimetype="text/event-stream")


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

    data = {
        "id": int(request.form["id"]),
        "competition": request.form["competition"],
        "home": {"colour": request.form["home_colour"].replace("#", "")},
        "away": {"colour": request.form["away_colour"].replace("#", "")},
        "play_lock": play_lock,
    }

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


def pid_exists(pid):
    return psutil.pid_exists(pid)


@app.get("/api/rtmp")
def get_rtmp():
    global RTMP_PROCESS
    game = load_game()
    rtmp = game.get("rtmp", {})

    pid = (
        0
        if RTMP_PROCESS is None or RTMP_PROCESS.poll() is not None
        else RTMP_PROCESS.pid
    )
    process_output = [""]

    if RTMP_PROCESS is not None and pid == 0:
        try:
            # pid = RTMP_PROCESS.pid
            process_output = RTMP_PROCESS.stdout.read().split("\n")[-10:]
        except:
            1

    # if pid != 0:
    #     RTMP_PROCESS.get("pid",0) = RTMP_PROCESS.get("pid",0) if pid_exists(RTMP_PROCESS.get("pid",0)) else 0

    return {"rtmp": rtmp, "live": pid != 0, "process_output": "\n".join(process_output)}


@app.post("/api/rtmp")
def save_rtmp():
    data = request.json

    game = load_game()

    game["rtmp"] = {
        "url": data.get("url", ""),
        "width": int(data.get("width", 1920)),
        "height": int(data.get("height", 1080)),
        "fps": int(data.get("fps", 25)),
        "pixfmt": data.get("pixfmt", "bgra"),
    }

    save_game(game)
    return {"ok": True, "rtmp": game["rtmp"]}


@app.post("/api/rtmp/live")
def toggle_rtmp_live():
    global RTMP_PROCESS
    data = request.json
    live = bool(data.get("live"))

    if live:
        RTMP_PROCESS = start_rtmp(
            tempfile.gettempdir() + "/scorebug.frame",
            int(data.get("rtmp", {}).get("width", 1920)),
            int(data.get("rtmp", {}).get("height", 1080)),
            int(data.get("rtmp", {}).get("fps", 25)),
            data.get("rtmp", {}).get("url", ""),
        )

        threading.Thread(
            target=watch_process_output,
            args=(RTMP_PROCESS,),
            daemon=True,
        ).start()

        send_event("rtmp", {"live": True, "message": "RTMP Started"})

    else:
        try:
            os.kill(RTMP_PROCESS.pid, 9)
        except:
            print(f"Cannot kill RTMP process")

        RTMP_PROCESS = None
        send_event("rtmp", {"live": False, "message": "RTMP Stopped"})

    return {
        "ok": True,
        "live": live,
    }


app.run(host="0.0.0.0", port=8080)
