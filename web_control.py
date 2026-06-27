from flask import Flask, request, jsonify, render_template_string
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

RTMP_PID = 0

def run_cmd(cmd):
    try:
        return subprocess.check_output(
            cmd, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


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
        ]
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
        ("BBF NBL", "bbf_nbl"),
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

    return f"""
    <html>
    <head>
        <title>Richmond Baseball Video Control</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: Arial, sans-serif;
                font-size: 22px;
                padding: 20px;
                background: #111;
                color: white;
            }}

            h1 {{
                font-size: 34px;
            }}

            label {{
                display: block;
                margin-top: 24px;
                margin-bottom: 8px;
                font-weight: bold;
            }}

            input {{
                width: 100%;
                font-size: 24px;
                padding: 14px;
                box-sizing: border-box;
                border-radius: 8px;
                border: 1px solid #555;
            }}

            button {{
                font-size: 24px;
                padding: 16px 22px;
                margin: 6px 4px;
                border-radius: 10px;
                border: 2px solid #fff;
                cursor: pointer;
            }}

            .save {{
                width: 100%;
                margin-top: 32px;
                padding: 22px;
                background: #00aa44;
                color: white;
                font-weight: bold;
            }}

            .swatches {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                margin-top: 10px;
            }}

            .swatch {{
                min-width: 120px;
                color: white;
                text-shadow: 1px 1px 2px black;
            }}

            .white {{
                color: black;
                text-shadow: none;
            }}
        </style>


    </head>

    <body>
        <h1>Richmond Baseball Video Control</h1>

        <form method="post" action="/save">
            <label>Competition</label>
            
            <input
                id="competition"
                name="competition"
                value="{game.get('competition', '')}"
            >
            
            <div class="swatches">
                {competition_buttons()}
            </div>

            <label>Game ID</label>
            <input name="id" value="{game.get('id', '')}">
            
            <!-- <label>Upcoming games</label>
            <select
                id="upcoming_games"
                onchange="selectUpcomingGame()"
                style="width: 100%; font-size: 24px; padding: 14px; border-radius: 8px;"
            >
                <option value="">Select a competition</option>
            </select> -->
            
            <label>Lock to play #</label>
            <input
                id="play_lock"
                name="play_lock"
                type="number"
                min="0"
                value="{game.get('play_lock', 0)}"
                placeholder="Leave at 0 or blank for live playback"
            >            

            <label>Away colour</label>
            <input id="away_colour" name="away_colour" value="{away_colour}">

            <div class="swatches">
                {colour_buttons("away_colour")}
            </div>            

            <label>Home colour</label>
            <input id="home_colour" name="home_colour" value="{home_colour}">

            <div class="swatches">
                {colour_buttons("home_colour")}
            </div>



            <button id="general-save" class="save" type="submit">Save Settings</button>

            <details style="margin-top: 30px;">
                <summary style="font-size: 26px; font-weight: bold; cursor: pointer;">
                    Advanced / Network
                </summary>
            
                <button type="button" onclick="loadNetworkStatus()">
                    Refresh network status
                </button>
            
                <pre
                    id="network_status"
                    style="
                        background: #000;
                        color: #0f0;
                        padding: 16px;
                        border-radius: 8px;
                        white-space: pre-wrap;
                        font-size: 16px;
                    "
                >Press refresh...</pre>

                <fieldset>
                    <legend>RTMP Stream</legend>

                    <label>
                      RTMP URL
                      <input id="rtmp-url" type="text" placeholder="rtmp://localhost/live/scorebug">
                    </label>

                    <label>
                      Width
                      <input id="rtmp-width" type="number" value="1920">
                    </label>

                    <label>
                      Height
                      <input id="rtmp-height" type="number" value="1080">
                    </label>

                    <label>
                      FPS
                      <input id="rtmp-fps" type="number" value="25">
                    </label>

                    <label>
                      Pixel Format
                      <input id="rtmp-pixfmt" type="text" value="bgra">
                    </label>

                    <div class="rtmp-actions">
                      <button type="button" id="rtmp-load">Load RTMP Settings</button>
                      <button type="button" id="rtmp-save">Save RTMP Settings</button>
                      <button type="button" id="rtmp-live">Go LIVE</button>
                    </div>

                    <p id="rtmp-status">RTMP: unknown</p>
                  </fieldset>
                </div>
            </details>

        </form>

        <script>

            function setColour(fieldId, colour) {{
                document.getElementById(fieldId).value = colour;
            }}

            async function setCompetition(comp) {{
                document.getElementById("competition").value = comp;
                /*
                const select = document.getElementById("upcoming_games");

                select.innerHTML = '<option value="">Loading...</option>';

                if (!comp) {{
                    select.innerHTML = '<option value="">Select a competition first</option>';
                    return;
                }}

                try {{
                    const response = await fetch(`/api/upcoming-games?competition=${{encodeURIComponent(comp)}}`);
                    const games = await response.json();

                    select.innerHTML = '<option value="">Choose a game...</option>';

                    if (!games.length) {{
                        select.innerHTML = '<option value="">No upcoming games found</option>';
                        return;
                    }}
                    
                    for (const game of games) {{
                        const option = document.createElement("option");
                        option.value = game.id;
                        option.textContent = `${{game.date}} ${{game.time}} - ${{game.away}} @ ${{game.home}} (#${{game.id}})`;
                        select.appendChild(option);
                    }}
                }} catch (err) {{
                    select.innerHTML = '<option value="">Error loading games</option>';
                    console.error(err);
                }}*/
            }}

            function clearPlayLock() {{
                document.getElementById("play_lock").value = "";
            }}

            function selectUpcomingGame() {{
                const select = document.getElementById("upcoming_games");
                const gameId = select.value;

                if (gameId) {{
                    document.querySelector('input[name="id"]').value = gameId;
                }}
            }}
        </script>
        <script>
            async function loadNetworkStatus() {{
                const box = document.getElementById("network_status");
                box.textContent = "Loading...";

                try {{
                    const response = await fetch("/api/network-status");
                    const data = await response.json();

                    box.textContent =
            `Default route:
            ${{data.default_route}}

            IP addresses:
            ${{data.addresses}}

            IP forwarding:
            ${{data.ip_forward === "1" ? "enabled" : "disabled"}}

            Routes:
            ${{data.routes}}

            DHCP clients:
            ${{data.clients || "No DHCP leases yet"}}`;
                }} catch (err) {{
                    box.textContent = "Error loading network status";
                    console.error(err);
                }}
            }}
        </script>
        <script>
        async function loadRtmp() {{
          const res = await fetch("/api/rtmp");
          const data = await res.json();

          const rtmp = data.rtmp || {{}};

          document.getElementById("rtmp-url").value = rtmp.url || "";
          document.getElementById("rtmp-width").value = rtmp.width || 1920;
          document.getElementById("rtmp-height").value = rtmp.height || 1080;
          document.getElementById("rtmp-fps").value = rtmp.fps || 25;
          document.getElementById("rtmp-pixfmt").value = rtmp.pixfmt || "bgra";

          
        }}

        async function checkLiveRtmp() {{
          const res = await fetch("/api/rtmp");
          const data = await res.json();

          document.getElementById("rtmp-status").textContent =
            data.live ? "RTMP: LIVE" : "RTMP: offline";

          document.getElementById("rtmp-live").textContent =
            data.live ? "Stop LIVE" : "Go LIVE";
        }}

        async function saveRtmp() {{
          const btn = document.getElementById("rtmp-save");

          btn.disabled = true;
          btn.textContent = "Saving...";

          try {{
            await fetch("/api/rtmp", {{
              method: "POST",
              headers: {{"Content-Type": "application/json"}},
              body: JSON.stringify({{
                url: document.getElementById("rtmp-url").value || "rtmp://localhost/live/scorebug",
                width: document.getElementById("rtmp-width").value || 1920,
                height: document.getElementById("rtmp-height").value || 1080,
                fps: document.getElementById("rtmp-fps").value || 25,
                pixfmt: document.getElementById("rtmp-pixfmt").value || "bgra",
                }})
            }});

            await loadRtmp();
          }} finally {{
            btn.disabled = false;
            btn.textContent = "Save RTMP Settings";
          }}
        }}

        async function toggleRtmpLive() {{
          const current = await fetch("/api/rtmp").then(r => r.json());

          await fetch("/api/rtmp/live", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
              live: !current.live,
              rtmp: current.rtmp
            }})
          }});

          await loadRtmp();
        }}

        document.getElementById("rtmp-load").addEventListener("click", loadRtmp);
        document.getElementById("rtmp-save").addEventListener("click", saveRtmp);
        document.getElementById("rtmp-live").addEventListener("click", toggleRtmpLive);

        loadRtmp();
        checkLiveRtmp();
        setInterval(checkLiveRtmp, 2000);
        </script>

        <script>
        document.querySelector("form").addEventListener("submit", () => {{
          const btn = document.getElementById("general-save");
          btn.disabled = true;
          btn.textContent = "Saving...";
        }});
        </script>
    </body>
    </html>
    """


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

    return """
    <html>
    <head>
        <title>Saved</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                text-align: center;
                margin-top: 100px;
            }

            button {
                font-size: 24px;
                padding: 20px 40px;
                margin-top: 40px;
            }
        </style>
    </head>
    <body>
        <h1>✅ Settings saved</h1>

        <button onclick="window.history.back()">
            ← Back
        </button>
    </body>
    </html>
    """


def pid_exists(pid):
    return psutil.pid_exists(pid)


@app.get("/api/rtmp")
def get_rtmp():
    global RTMP_PID
    game = load_game()
    rtmp = game.get("rtmp", {})

    if RTMP_PID != 0:
        RTMP_PID = RTMP_PID if pid_exists(RTMP_PID) else 0

    return {"rtmp": rtmp, "live": RTMP_PID != 0}


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
    global RTMP_PID
    data = request.json
    live = bool(data.get("live"))

    if live:
        result = start_rtmp(
            tempfile.gettempdir() + "/scorebug.frame",
            int(data.get("rtmp", {}).get("width", 1920)),
            int(data.get("rtmp", {}).get("height", 1080)),
            int(data.get("rtmp", {}).get("fps", 25)),
            data.get("rtmp", {}).get("url", ""),
        )
        RTMP_PID = result.pid
    else:
        os.kill(RTMP_PID, 9)
        RTMP_PID = 0

    return {
        "ok": True,
        "live": live,
    }


app.run(host="0.0.0.0", port=8080)
