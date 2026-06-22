import sys
import time
import requests
import pygame
import json
from PIL import Image, ImageDraw, ImageFont, ImageChops
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
import os
import numpy as np

POLL_INTERVAL = 3
OUTPUT_FILE = "scorebug.png"
TEMPLATE_FILE = "template.png"
FB = "/dev/fb0"

WIDTH = 1920
HEIGHT = 1080
STATUS_TIMEOUT = 120

status_timer = 0

# pygame.init()
# pygame.mouse.set_visible(False)
# print("Displays:", pygame.display.get_num_displays())

# DISPLAY = pygame.display.get_num_displays() - 1
# screen = pygame.display.set_mode((WIDTH,HEIGHT), pygame.FULLSCREEN,display=DISPLAY)

# screen.fill("#FF66C4"),
# pygame.display.flip()


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
    "9th"
]


try:
    font_large = ImageFont.truetype("Gotham-Bold.otf", 72)
    font_medium = ImageFont.truetype("Gotham-Book.otf", 42)
    font_small = ImageFont.truetype("Gotham-Bold.otf", 16)
    lineup_medium = ImageFont.truetype("Gotham-Book.otf", 56)
    lineup_small = ImageFont.truetype("Gotham-Book.otf", 24)
except:
    font_large = ImageFont.load_default(72)
    font_medium = ImageFont.load_default(42)
    font_small = ImageFont.load_default(16)
    lineup_medium = ImageFont.load_default(56)
    lineup_small = ImageFont.load_default(24)

def get_latest_play(game_id):
    url = f"https://game.wbsc.org/gamedata/{game_id}/latest.json"

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    return int(response.text.strip())


def get_play(game_id, play_number):
    url = f"https://game.wbsc.org/gamedata/{game_id}/play{play_number}.json"
    print(url)
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    return response.json()

def batting_avg(ab, h):
    ab = int(ab)
    h = int(h)

    if ab == 0:
        return ".000"

    return f"{h / ab:.3f}".lstrip("0")
def occupied(value):
    return value not in (0, "0", None, "")

def get_inning(inning):
    try:
        return INNINGS[int(inning)]
    except:
        return inning

def image_logic(url):

    if url == "":
        return None

    basename = os.path.basename(urlparse(url).path)

    if basename == "default-player.jpg":
        return None

    image_path = os.path.join("images", basename)

    # Check if already cached
    if os.path.exists(image_path):
        return image_path

    # Ensure images directory exists
    os.makedirs("images", exist_ok=True)

    # Download and save
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    with open(image_path, "wb") as f:
        f.write(response.content)

    return image_path

# def surname(name):
#     parts = name.split()

#     surname_parts = []

#     for part in parts:
#         if part.isupper():
#             surname_parts.append(part)
#         else:
#             break

#     surname = " ".join(surname_parts)
#     print(surname)
#     return surname

def draw_up_arrow(draw, x, y, size=10, fill="white"):
    points = [
        (x, y - size),      # top
        (x + size, y + size),
        (x - size, y + size)
    ]

    draw.polygon(points, fill=fill)

def draw_down_arrow(draw, x, y, size=10, fill="white"):
    points = [
        (x - size, y - size),
        (x + size, y - size),
        (x, y + size)
    ]

    draw.polygon(points, fill=fill)
def get_lineups(payload):
    lineups = {
        "away": [],
        "home": []
    }

    for key, player in payload["boxscore"].items():
        # Skip pitcher stat rows
        

        # WBSC keys: 1010 = away batting 1st, 1090 = away batting 9th
        #            2010 = home batting 1st, 2090 = home batting 9th
        if len(key) < 3:
            continue

        team_side = key[0]
        batting_order = int(key[2])

        if batting_order < 1 or batting_order > 9:
            continue
    
        row = {
            "order": batting_order,
            "name": player.get("name",""),
            "lastname": player.get("lastname", ""),
            "firstname": player.get("firstname", ""),
            "pos": player.get("POS", ""),
            "avg": player.get("AVG", ""),
            "season": player.get("SEASON", {}),
            "image": player.get("image","")
        }

        row['image'] = "" #image_logic(row['image'])

        if team_side == "1":
            lineups["away"].append(row)
        elif team_side == "2":
            lineups["home"].append(row)

    lineups["away"].sort(key=lambda p: p["order"])
    lineups["home"].sort(key=lambda p: p["order"])

    return lineups

def get_pitchers(payload):
    pitchers = {
        "away": None,
        "home": None
    }

    for key, player in payload["boxscore"].items():
        if "PITCHES" not in player:
            continue

        if key.startswith("1"):
            pitchers["away"] = player

        elif key.startswith("2"):
            pitchers["home"] = player

    return pitchers

def render_lineup_sheet(payload, font_title, font_row, home_colour, away_colour):

    img = Image.new("RGBA", (WIDTH, HEIGHT), (255, 0, 255, 255))

    draw = ImageDraw.Draw(img)

    lineups = get_lineups(payload)

    pitchers = get_pitchers(payload)

    lineups['away'].append(None)
    lineups['home'].append(None)

    lineups['away'].append(pitchers['away'])
    lineups['home'].append(pitchers['home'])

    away_name = payload.get("eventaway", "AWAY")
    home_name = payload.get("eventhome", "HOME")

    # Main panel
    draw.rounded_rectangle(
        (270, 180, 1650, 950),
        radius=25,
        fill="#111",
        outline="#FFF",
        width=2
    )

    draw.rectangle((272,200,960,260), fill="#" + away_colour)
    draw.rectangle((960,200,1648,260), fill="#" + home_colour)
    # Titles
    draw.text((605, 230), away_name, fill="white", font=font_title, anchor="mm", stroke_fill="#000", stroke_width=1)
    draw.text((1340, 230), home_name, fill="white", font=font_title, anchor="mm", stroke_fill="#000", stroke_width=1)

    # Column headers
    draw.text((290, 285), "#", fill="#CCCCCC", font=font_row, anchor="lm")
    draw.text((340, 285), "POS", fill="#CCCCCC", font=font_row, anchor="lm")
    draw.text((420, 285), "PLAYER", fill="#CCCCCC", font=font_row, anchor="lm")

    draw.text((980, 285), "#", fill="#CCCCCC", font=font_row, anchor="lm")
    draw.text((1030, 285), "POS", fill="#CCCCCC", font=font_row, anchor="lm")
    draw.text((1100, 285), "PLAYER", fill="#CCCCCC", font=font_row, anchor="lm")

    start_y = 330
    row_gap = 55

    for i, player in enumerate(lineups["away"]):
        if player == None:
            continue
        y = start_y + i * row_gap
        draw.rounded_rectangle((280,y-20,950,y+20), fill="#" + away_colour + "CC",
        outline="#" + away_colour,
        width=1,
        radius=5)
        if 'PITCHES' in player:
            draw.text((290, y), "Pitcher:", fill="white", font=font_row, anchor="lm", stroke_fill="#000", stroke_width=1)
            draw.text(
                (420, y),
                f"{player["name"]} (ER: {player['SEASON']['PITCHER']} BB: {player['SEASON']['PITCHBB']} K: {player['SEASON']['PITCHSO']})",
                fill="white",
                font=font_row,
                anchor="lm", stroke_fill="#000", stroke_width=1)
            
        else:
            draw.text((290, y), str(player["order"]), fill="white", font=font_row, anchor="lm", stroke_fill="#000", stroke_width=1)
            draw.text((340, y), player["pos"], fill="white", font=font_row, anchor="lm", stroke_fill="#000", stroke_width=1)
            draw.text(
                (420, y),
                f"{player["name"]} ({batting_avg(player['season']['AB'],player['season']['H'])} PA: {player['season']['PA']})",
                fill="white",
                font=font_row,
                anchor="lm", stroke_fill="#000", stroke_width=1
            )

    for i, player in enumerate(lineups["home"]):
        if player == None:
            continue
        y = start_y + i * row_gap
        draw.rounded_rectangle((970,y-20,1640,y+20), fill="#" + home_colour + "CC",
        outline="#" + home_colour,
        width=1,
        radius=5)
        if 'PITCHES' in player:
            draw.text((980, y), "Pitcher:", fill="white", font=font_row, anchor="lm", stroke_fill="#000", stroke_width=1)
            draw.text(
                (1110, y),
                f"{player["name"]} (ER: {player['SEASON']['PITCHER']} BB: {player['SEASON']['PITCHBB']} K: {player['SEASON']['PITCHSO']})",
                fill="white",
                font=font_row,
                anchor="lm", stroke_fill="#000", stroke_width=1)
            
        else:
            draw.text((980, y), str(player["order"]), fill="white", font=font_row, anchor="lm", stroke_fill="#000", stroke_width=1)
            draw.text((1030, y), player["pos"], fill="white", font=font_row, anchor="lm", stroke_fill="#000", stroke_width=1)
            draw.text(
                (1100, y),
                f"{player["name"]} ({batting_avg(player['season']['AB'],player['season']['H'])} PA: {player['season']['PA']})",
                fill="white",
                font=font_row,
                anchor="lm", stroke_fill="#000", stroke_width=1)
            

    img.save(OUTPUT_FILE)

def render_scorebug(payload, home_colour="000000", away_colour="FFFFFF"):
    global status_timer

    def draw_base(cx, cy, occupied=False):
        size = 32

        points = [(cx, cy - size), (cx + size, cy), (cx, cy + size), (cx - size, cy)]

        draw.polygon(
            points, fill="yellow" if occupied else "black"
        )

    situation = payload["situation"]
    linescore = payload["linescore"]

    away = linescore["awaytotals"]
    away['colour'] = away_colour
    home = linescore["hometotals"]
    home['colour'] = home_colour

    away["name"] = payload["eventaway"]
    home["name"] = payload["eventhome"]

    inning = situation["inning"]

    inning_number, half = inning.split(".")

    inning_number = get_inning(inning_number)

    batter = {}
    pitcher = {}

    def batter_line(batter):
        line = []

        for type in ["2B","3B","HR","K","BB","SF","SB"]:
            if type == "2B":
                n = batter['DOUBLE']
            elif type == "3B":
                n = batter['TRIPLE']
            elif type == "HR":
                n = batter['HR']
            # elif type == "RBI":
            #    n = batter['RBI']
            elif type == "K":
                n = batter['SO']
            elif type == "BB":
                n = batter['BB']
            elif type == "HBP":
                n = batter['HBP']
            elif type == "SF":
                n = batter['SF']   
            elif type == "SB":
                n = batter['SB']      

            if n < 1:
                continue
            elif n > 1:
                line.append(f"{n} {type}")
            else:
                line.append(type)

        return ", ".join(line)

    for player_num in payload['boxscore']:
        player = payload['boxscore'][player_num]
        if player['playerid'] == situation['batterid'] and 'PITCHES' not in player:
            batter = player
            batter['order'] = player_num[2] if int(player_num[2]) > 0 else player_num[2] * 10

            batter['line'] = batter_line(batter)
            continue
        if player['playerid'] == situation['pitcherid'] and 'PITCHES' in player:
            pitcher = player
            continue

    pitcher['BALLS'] = pitcher['PITCHES'] - pitcher['STRIKES']

    # Transparent image
    # img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    template = Image.open(TEMPLATE_FILE).convert("RGBA")

    overlay = Image.new("RGBA", template.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)    

    # AWAY COLOUR
    draw.rectangle((852, 932, 1242, 1017), fill="#" + away["colour"] + "99")

    # HOME COLOUR
    draw.polygon(
        [(1242, 932), (1570, 932), (1655, 1017), (1242, 1017)],
        # draw.rectangle(
        #    (1242, 932, 1442,1015),
        fill="#" + home["colour"] + "99",
    )

    # Score
    draw.text(
        (867, 950),
        f"{away['name']}",
        fill="white",
        font=font_large,
        anchor="lt",
        align="left",
        stroke_fill="black",
        stroke_width=1
    )
    draw.text(
        (1177, 950),
        f"{away['R']}",
        fill="white",
        font=font_large,
        anchor="rt",
        align="right",
        stroke_fill="black",
        stroke_width=1
    )
    draw.text(
        (1260, 950),
        f"{home['name']}",
        fill="white",
        font=font_large,
        anchor="lt",
        align="left",
        stroke_fill="black",
        stroke_width=1
    )
    draw.text(
        (1570, 950),
        f"{home['R']}",
        fill="white",
        font=font_large,
        anchor="rt",
        align="right",
        stroke_fill="black",
        stroke_width=1
    )

    # {away['R']} - {home['R']} {home['name']}

    # Inning
    # draw.text((50, 130), situation["currentinning"], fill="white", font=font_small)
    if(situation['currentinning'] == "FINAL"):
        draw.text(
            (1787, 845),
            "FINAL",
            fill="white",
            font=font_small,
            align="center",
            anchor="mb",
        )
    else:

        if status_timer < STATUS_TIMEOUT and len(payload['platecount']) > 0 and payload['platecount'][0]['type'] == 0: #Basically if there isn't an AB going on
            status = payload['platecount'][0]['label'].split("<br>")
            away['player'] = status[0]
            home['player'] = status[1] if len(status) > 1 else ""
        elif half == "0":
            away["player"] = f"{batter['order']}: {batter['POS']} - {batter['lastname']} - ({batter['H']}-{batter['AB']}) {batter['line']}"
            home["player"] = f"P: {pitcher['lastname']} - {pitcher['PITCHIP']} ({pitcher['BALLS']}-{pitcher['STRIKES']})"
            draw_up_arrow(draw, 1760, 885)
        else:
            home["player"] = f"{batter['order']}: {batter['POS']} - {batter['lastname']} - ({batter['H']}-{batter['AB']}) {batter['line']}"
            away["player"] = f"P: {pitcher['lastname']} - {pitcher['PITCHIP']} ({pitcher['BALLS']}-{pitcher['STRIKES']})"
            draw_down_arrow(draw, 1760, 887)

        draw.text(
            (1790, 885),
            f"{inning_number}",
            fill="white",
            font=font_medium,
            align="center",
            anchor="lm",
        )

        # Outs
        draw.text(
            (1787, 845),
            f"{situation['outs']} OUTS",
            fill="white",
            font=font_small,
            anchor="mb",
            align="center",
        )

        # Count
        draw.text(
            (1800, 1000),
            f"{situation['balls']}-{situation['strikes']}",
            fill="white",
            font=font_medium,
            anchor="mb",
            align="center",
        )

        # Away Player
        draw.text(
            (890, 1030),
            f"{away['player']}",
            fill="white",
            font=font_small,
            anchor="lm",
            align="left",
        )

        # Home Player
        draw.text(
            (1280, 1030),
            f"{home['player']}",
            fill="white",
            font=font_small,
            anchor="lm",
            align="left",
        )

        # Bases
        first = occupied(situation["runner1"])
        second = occupied(situation["runner2"])
        third = occupied(situation["runner3"])

        # Second base
        draw_base(1673, 890, second)

        # Third base
        draw_base(1632, 931, third)

        # First base
        draw_base(1714, 932, first)
    status_timer = 0    
    img = Image.alpha_composite(template, overlay)

    img.save(OUTPUT_FILE)


last_game_mtime = 0

def load_game_if_changed():
    global last_game_mtime

    mtime = os.path.getmtime("game.json")
    if mtime != last_game_mtime:
        with open("game.json") as f:
            game = json.load(f)
        last_game_mtime = mtime
        return game

    return None

def get_team_colour(team, default):
    if not isinstance(team, dict):
        return default

    colour = team.get("colour", default)

    if not colour:
        return default

    return colour.replace("#", "")

def frame_buffer(img):
    img = img.convert("RGB").resize((WIDTH, HEIGHT))

    # arr = np.asarray(img)

    # r = (arr[:,:,0] >> 3).astype(np.uint16)
    # g = (arr[:,:,1] >> 2).astype(np.uint16)
    # b = (arr[:,:,2] >> 3).astype(np.uint16)

    # rgb565 = (r << 11) | (g << 5) | b

    with open(FB, "wb") as fb:
        fb.write(img.tobytes("raw", "BGRA"))
    # fb.write(rgb565.tobytes())

def main():
    global status_timer

    
    frame_buffer(Image.new("RGB", (WIDTH, HEIGHT), ("#FF66C4")))

    last_play = 0 

    while True:
        now = datetime.now(ZoneInfo("Europe/London"))
        clock = now.strftime("%H:%M %Z")
        
        
        new_game = load_game_if_changed()
        
        if new_game != None:
            game = new_game
            last_play = 0
                    
        game_id = game["id"]
        competition_img = game["competition"]

        home = game["home"]
        away = game["away"]        
        
        try:
            latest_play = get_latest_play(game_id) if len(sys.argv) < 5 else sys.argv[4]

            if int(latest_play) > int(last_play) or status_timer >= STATUS_TIMEOUT:
                payload = get_play(game_id, latest_play)
      
                hc = get_team_colour(home,"FFFFFF")
                ac = get_team_colour(away,"000000")
                  
                if int(latest_play) == 1:
                    render_lineup_sheet(payload, lineup_medium, lineup_small, hc, ac)
                else:
                    render_scorebug(payload, hc, ac)

                print(f"Updated graphic for play {latest_play}")

                last_play = latest_play

        except Exception as e:
            print("Error:", e)

        finished_img = Image.open(OUTPUT_FILE)
        try:
            league_logo = Image.open(f"images/{competition_img}.png").convert("RGBA")

            league_logo.thumbnail((120, 120))
            #alpha = league_logo.getchannel("A")
            #alpha = alpha.point(lambda p: int(p * 0.8))
            #league_logo.putalpha(alpha)

            #logo_layer = Image.new("RGBA", finished_img.size, (0, 0, 0, 0))
            finished_img.paste(league_logo, (15, 25), league_logo)
        except:
            print(f"Could'nt work with {competition_img}")

        clock_draw = ImageDraw.Draw(finished_img)    

        #AWAY COLOUR
        clock_draw.rounded_rectangle(
            (1800, 20, 1900, 80),
            fill="#000000",
            radius=15,
            outline="#FFF",
            width=2

        )
        clock_draw.text(
            (1850,50),
            clock,
            fill="#FFF",
            anchor="mm",
            font=font_small,
            align="center"
        )

        #finished_img = Image.alpha_composite(finished_img, logo_layer)
        #finished_img.save(OUTPUT_FILE)

        #surface = pygame.image.fromstring(finished_img.tobytes(),finished_img.size,finished_img.mode)
        
        #screen.blit(surface,(0,0))
        #pygame.display.flip()
        
        frame_buffer(finished_img)
        
        time.sleep(POLL_INTERVAL)
        status_timer = status_timer + POLL_INTERVAL


if __name__ == "__main__":
    main()
