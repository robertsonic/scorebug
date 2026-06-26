# Richmond Baseball Scorebug

A lightweight, self-contained baseball graphics appliance for the Raspberry Pi 4.

The Richmond Baseball Scorebug retrieves live WBSC game data and renders a professional scorebug directly to the Raspberry Pi HDMI output without requiring OBS, X11, Wayland or a desktop environment.

Designed for use with the Blackmagic ATEM Mini Pro, the system provides a dedicated graphics feed that can be controlled entirely from a mobile phone or desktop browser via a built-in web interface.

---

## Features

* Live WBSC game data
* HDMI graphics output (1920×1080)
* Built-in mobile-friendly web interface
* Automatic lineup card
* Team colour selection
* Competition branding
* Live clock
* Automatic service restart
* Headless operation
* Designed for Raspberry Pi 4
* No OBS or desktop environment required

---

## Requirements

### Hardware

* Raspberry Pi 4 (2 GB RAM or greater recommended)
* Raspberry Pi OS Lite (64-bit)
* HDMI cable
* Network connection (Wi-Fi, Ethernet or USB 4G modem)
* Blackmagic ATEM Mini Pro (optional)

### Software

* Python 3.13
* Git

---

# Installation

Clone the repository:

```bash
git clone https://github.com/<your-user>/scorebug.git
cd scorebug
```

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Copy the supplied systemd service files into place:

```bash
sudo cp services/scorebug.service /etc/systemd/system/
sudo cp services/scorebug-web.service /etc/systemd/system/
```

Enable the services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable scorebug
sudo systemctl enable scorebug-web
```

Start the services:

```bash
sudo systemctl start scorebug
sudo systemctl start scorebug-web
```

The appliance will now start automatically every time the Raspberry Pi boots.

---

# First Boot

After approximately one minute, open a browser on the same network and navigate to:

```text
http://<raspberry-pi-ip>:8080
```

The control page allows you to configure the current game.

---

# Starting a Game

1. Open the web interface.
2. Select the competition.
3. Enter the WBSC Game ID.
4. Choose the home and away team colours.
5. Press **Save**.

The graphics engine automatically reloads the new settings without restarting.

---

# During the Game

The scorebug automatically updates with:

* Team names
* Runs
* Inning
* Balls
* Strikes
* Outs
* Base occupancy
* Batter
* Pitcher
* Competition logo
* Live clock

At the start of each game a lineup graphic is shown automatically before switching to the live scorebug.

---

# Changing Games

Games can be changed at any time.

Simply update the Game ID from the web interface and press **Save**.

No restart is required.

---

# Updating

To update the appliance:

```bash
git pull
```

Restart the services:

```bash
sudo systemctl restart scorebug
sudo systemctl restart scorebug-web
```

Or simply reboot:

```bash
sudo reboot
```

---

# Architecture

```
WBSC Live Data
        │
        ▼
 scorebug.py
        │
        ▼
    Pillow
        │
        ▼
32-bit Framebuffer
        │
        ▼
 Raspberry Pi HDMI
        │
        ▼
 ATEM Mini Pro
        │
        ▼
 Live Stream
```

The web interface communicates independently with the graphics engine through a shared configuration file.

```
Phone
   │
   ▼
Flask Web Interface
   │
   ▼
game.json
   │
   ▼
Scorebug Engine
```

---

# Services

Two systemd services are installed:

| Service                | Purpose               |
| ---------------------- | --------------------- |
| `scorebug.service`     | Graphics engine       |
| `scorebug-web.service` | Web control interface |

Both services:

* Start automatically at boot
* Restart automatically after failures

View logs with:

```bash
journalctl -u scorebug -f
```

```bash
journalctl -u scorebug-web -f
```

---

# Networking

The appliance supports multiple Internet connections including:

* Wi-Fi
* Ethernet
* USB 4G modem

When configured, the Raspberry Pi can also provide a private Ethernet network for downstream devices such as an ATEM Mini Pro while automatically routing traffic through the preferred Internet connection.

---

# Development

Development is performed on Windows using VS Code.

Typical workflow:

```bash
git commit
git push
```

On the Raspberry Pi:

```bash
git pull
```

---

# Roadmap

Planned features include:

* Automatic game discovery by competition
* RTMP/RTSP graphics output
* Built-in media server
* Team logos
* Sponsor graphics
* Remote management
* Cloudflare Tunnel support
* Temperature monitoring
* ATEM control

---

## License

MIT License.
