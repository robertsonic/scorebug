from multiprocessing.queues import Queue
import queue
import time
from typing import Any

PAGE_FLIP_INTERVAL = 2

lcd_pages = [["Scorebug Display", "v1.0", None]]
lcd_page_num = 0

next_page_flip: float = 0.0


def update_displays(page: list[str, str, int | None], now):
    global next_page_flip

    next_page_flip = now + PAGE_FLIP_INTERVAL
    print(page)


def run_display(updates: Queue, stop_event: Any):
    global next_page_flip
    global lcd_pages
    global lcd_page_num

    update_displays(lcd_pages[0], time.monotonic())

    while stop_event is None or not stop_event.is_set():

        now = time.monotonic()
        force_update: bool = False
        _signal_strength: int | None = None

        latest = None
        try:
            # while True:
            latest = updates.get_nowait()
        except queue.Empty:
            pass

        if latest is None:
            continue

        if "pitch_speed" in latest and latest["pitch_speed"] != None:

            text = latest["pitch_speed"].get("text")
            if text:
                lcd_pages[0][1] = text or lcd_pages[0][1]
                force_update = True

        if "status" in latest and latest["status"] != None:

            text = latest["status"].get("text")
            if text:
                lcd_pages[0][0] = text
                force_update = True

        if "interfaces" in latest and "interfaces" != None:

            lcd_pages = [lcd_pages[0]]

            for i in range(len(latest["interfaces"])):
                print(latest["interfaces"][i])

                index = i + 1

                if index >= len(lcd_pages):
                    lcd_pages.extend([None] * (index - len(lcd_pages) + 1))

                lcd_pages[index] = [
                    latest["interfaces"][i]["interface"],
                    latest["interfaces"][i]["interface_ip"],
                    5,  # SIGNAL STRENGTH TODO
                ]

        if force_update:
            update_displays(lcd_pages[0], now)

        elif now >= next_page_flip:
            lcd_pages[0] = ["Scorebug Display", "", None]

            lcd_page_num += 1

            if lcd_page_num >= len(lcd_pages):
                lcd_page_num = 1

            update_displays(lcd_pages[lcd_page_num], now)
