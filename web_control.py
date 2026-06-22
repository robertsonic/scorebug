from flask import Flask, request, jsonify, render_template_string
import json
import os
import tempfile

app = Flask(__name__)
GAME_FILE = "game.json"

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

@app.route("/")
def index():
    with open(GAME_FILE) as f:
        game = json.load(f)

    home_colour = game.get("home", {}).get("colour", "FFFFFF")
    away_colour = game.get("away", {}).get("colour", "000000")

    return f"""
    <html>
    <head>
        <title>Richmond Baseball Video Graphics Control</title>
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

        <script>
            function setColour(fieldId, colour) {{
                document.getElementById(fieldId).value = colour;
            }}

            function setCompetition(comp) {{
                document.getElementById("competition").value = comp;
            }}

            function clearPlayLock() {{
                document.getElementById("play_lock").value = "";
            }}
        </script>
    </head>

    <body>
        <h1>Richmond Baseball Video Graphics Control</h1>

        <form method="post" action="/save">
            <label>Game ID</label>
            <input name="id" value="{game.get('id', '')}">
            
            <label>Lock to play #</label>
            <input
                id="play_lock"
                name="play_lock"
                type="number"
                min="0"
                value="{game.get('play_lock', 0)}"
                placeholder="Leave at 0 for live playback"
            >

            <div class="button-row">
                <button type="button" onclick="clearPlayLock()">Live / unlock</button>
            </div>

            <label>Competition</label>
            
            <input
                id="competition"
                name="competition"
                value="{game.get('competition', '')}"
            >
            
            <div class="swatches">
                {competition_buttons()}
            </div>

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



            <button class="save" type="submit">Save settings</button>
        </form>
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

app.run(host="0.0.0.0", port=8080)
