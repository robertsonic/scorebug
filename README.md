# Richmond Baseball Scorebug

A self-contained baseball graphics appliance for live streaming games with an ATEM Mini Pro.

The scorebug automatically retrieves live WBSC game data and overlays professional graphics onto the stream. Match settings can be changed from a mobile phone without requiring a laptop, OBS or a desktop environment.

---

# How To Use

The scorebug is designed to run automatically when powered on.

## Starting a Game

1. Power on the Raspberry Pi and ATEM Mini Pro.
2. Wait approximately one minute for the services to start.
3. Connect a phone to the same network.
4. Open:

```text
http://<pi-ip>:8080
```

5. Enter the WBSC Game ID.
6. Select the competition:

- BBF NBL
- BBF Div 2
- BBF Div 3
- BBF Div 4
- BBF Div 5

7. Select the home and away team colours.
8. Press **Save**.

The graphics will automatically update.

---

## Graphics Displayed

The scorebug displays:

- Team names
- Score
- Inning
- Outs
- Balls and strikes count
- Occupied bases
- Batter information
- Pitcher information
- Competition logo
- Clock

At the beginning of the game, a lineup card is automatically shown.

---

## Changing Games

At any time:

1. Open the web page.
2. Change the Game ID.
3. Press **Save**.

The scorebug will automatically switch to the new game without requiring a restart.

---

## Recovering From Failures

The graphics engine and web interface run as Linux services.

If either process crashes:

- It will automatically restart.
- No user intervention is required.

---

## Updating The Software

Changes are developed on Windows and pushed to GitHub.

On the Raspberry Pi:

```bash
git pull
```

Then either reboot:

```bash
sudo reboot
```

or restart the services:

```bash
sudo systemctl restart scorebug
sudo systemctl restart scorebug-web
```

---

# System Architecture

```text
Phone
↓
Web Interface
↓
game.json
↓
Scorebug Engine
↓
Framebuffer
↓
HDMI
↓
ATEM Mini Pro
↓
YouTube
```

---

# Components

## scorebug.py

Responsible for:

- Polling WBSC
- Rendering graphics
- Rendering lineups
- Rendering scorebug
- Adding competition logo
- Adding clock
- Writing directly to the framebuffer

## web_control.py

Provides the control webpage.

Settings are stored in:

```text
game.json
```

which is automatically reloaded by the graphics engine.

---

# Services

Two systemd services are used:

- `scorebug.service`
- `scorebug-web.service`

Both services are configured to:

- Start automatically at boot.
- Restart automatically if they fail.

Logs may be viewed with:

```bash
journalctl -u scorebug -f
```

```bash
journalctl -u scorebug-web -f
```

---

# Development

Development occurs on Windows:

```text
VS Code
↓
GitHub
↓
Raspberry Pi
```

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

# Technical Details

## Running Without A Desktop Environment

The Raspberry Pi runs without:

- X11
- Wayland
- OBS
- Pygame

Graphics are rendered using:

```text
WBSC JSON
↓
PIL
↓
RGB565 conversion
↓
/dev/fb0
↓
HDMI
```

This creates a true embedded appliance.

---

## Framebuffer

The Raspberry Pi runs using the full KMS driver:

```ini
dtoverlay=vc4-kms-v3d
```

To force a 32-bit framebuffer, edit:

```bash
sudo nano /boot/firmware/cmdline.txt
```

and add/amend the HDMI video mode:

```text
video=HDMI-A-1:1920x1080M@60
```

to:

```text
video=HDMI-A-1:1920x1080M-32@60
```

This forces the KMS driver to create a 32-bit framebuffer.

Reboot the Pi:

```bash
sudo reboot
```

Confirm the framebuffer depth:

```bash
cat /sys/class/graphics/fb0/bits_per_pixel
```

which should return:

```text
32
```

Images are rendered using Pillow and converted from RGBA to BGRA before being written directly to:

```text
/dev/fb0
```

using:

```python
def frame_buffer(img):
    img = img.convert("RGBA").resize((WIDTH, HEIGHT))

    with open(FB, "wb") as fb:
        fb.write(img.tobytes("raw", "BGRA"))

```

This provides:

- 32-bit colour.
- Smooth anti-aliased fonts.
- No colour banding.
- No desktop environment.
- No X11.
- No Wayland.
- No OBS.
- No Pygame.

while maintaining a true embedded appliance architecture.

```text
WBSC JSON
        ↓
scorebug.py
        ↓
PIL
        ↓
RGBA
        ↓
NumPy RGBA→BGRA
        ↓
/dev/fb0 (32-bit)
        ↓
vc4-kms-v3d
        ↓
HDMI
        ↓
ATEM Mini Pro
        ↓
YouTube
```

---

## Console Configuration

To create a dedicated graphics output:

- `getty@tty1` is disabled.
- The Linux kernel console is moved from `tty1` to `tty3`.

This prevents:

- Login prompts.
- Kernel messages.
- Blinking cursors.

appearing over the graphics.

---

## Service Files

### scorebug.service

```ini
[Unit]
Description=Baseball Scorebug
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/richmond/scorebug
ExecStart=/home/richmond/scorebug/.venv/bin/python /home/richmond/scorebug/scorebug.py
Restart=always
RestartSec=3
User=richmond

[Install]
WantedBy=multi-user.target
```

### scorebug-web.service

```ini
[Unit]
Description=Baseball Scorebug Web Interface
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/richmond/scorebug
ExecStart=/home/richmond/scorebug/.venv/bin/python /home/richmond/scorebug/web_control.py
Restart=always
RestartSec=3
User=richmond

[Install]
WantedBy=multi-user.target
```

---

# Future Enhancements

- ATEM control
- Stream start and stop
- Sponsor graphics
- Team logos
- Temperature monitoring
- Cloudflare Tunnel remote access
- SIM modem operation
- Full remote management

---

# Final Architecture

```text
Phone
    ↓
Flask Web Interface
    ↓
game.json
    ↓
scorebug.py
    ↓
PIL
    ↓
RGB565 conversion
    ↓
Framebuffer
    ↓
HDMI
    ↓
ATEM Mini Pro
    ↓
YouTube
```

The result is a fully self-contained baseball broadcast appliance.