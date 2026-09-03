import math
import queue
import time
from multiprocessing.queues import Queue
from typing import Any

import usb.core
import usb.util

# ---------------------------------------------------------------------------
# Display configuration
# ---------------------------------------------------------------------------

PAGE_FLIP_INTERVAL = 3
SCOREBUG_DISPLAY_TIME = 6

lcd_pages = [["Scorebug Display", "v1.0", None]]
lcd_page_num = 0
displayed_page_num: int | None = None
next_page_flip: float = 0.0


# ---------------------------------------------------------------------------
# FT245R
# ---------------------------------------------------------------------------

FTDI_VENDOR_ID = 0x0403
FTDI_PRODUCT_ID = 0x6001

# FTDI vendor requests
FTDI_SIO_SET_BAUDRATE = 0x03
FTDI_SIO_SET_BITMODE = 0x0B

# Async bit-bang mode
FTDI_BITMODE_ASYNC = 0x01

# All eight D0-D7 pins are outputs
FTDI_DIRECTION_MASK = 0xFF

# We configure the FTDI baud to 9600.
#
# In asynchronous bit-bang mode the FT245R outputs GPIO states
# at 16 times the configured baud rate:
#
#     9600 * 16 = 153600 states/second
#
BITBANG_BAUD = 9600
BITBANG_RATE = BITBANG_BAUD * 16

SLOT_SECONDS = 1.0 / BITBANG_RATE


# ---------------------------------------------------------------------------
# FT245R pin mapping
# ---------------------------------------------------------------------------
#
#  D7 D6 D5 D4 D3 D2 D1 D0
#  |  |  |  |  |  |  |  |
#  LCD DATA    E  RS DI CLK
#
# D7-D4 : HD44780 data
# D3    : HD44780 E
# D2    : HD44780 RS
# D1    : MY9221 DATA
# D0    : MY9221 CLOCK
#
# HD44780 R/W is permanently connected to GND.
#

LCD_DATA_MASK = 0xF0

LCD_E = 0x08
LCD_RS = 0x04

LED_DATA = 0x02
LED_CLOCK = 0x01

LCD_MASK = 0xFC
LED_MASK = 0x03


# ---------------------------------------------------------------------------
# Runtime FT245R state
# ---------------------------------------------------------------------------

ftdi_device = None
ftdi_endpoint = None

# Last actual byte left on D7-D0.
current_gpio = 0x00


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------


def slots_for_us(microseconds: float) -> int:
    """
    Convert a required delay into FT245R bit-bang slots.

    Always rounds upward so that we never generate a delay shorter
    than the requested minimum.
    """

    return max(
        1,
        math.ceil((microseconds / 1_000_000) / SLOT_SECONDS),
    )


def hold_state(waveform: list[int], microseconds: float) -> None:
    """
    Hold the final state in a waveform for at least the requested time.
    """

    if not waveform:
        return

    waveform.extend([waveform[-1]] * slots_for_us(microseconds))


# ---------------------------------------------------------------------------
# HD44780 waveform generation
# ---------------------------------------------------------------------------


def lcd_nibble(nibble: int, rs: bool) -> list[int]:
    """
    Generate the GPIO states required to write one 4-bit nibble.

    E starts low, pulses high, then returns low.

    D1/D0 are always zero because the LCD generator owns only
    D7-D2.
    """

    value = (nibble & 0x0F) << 4

    if rs:
        value |= LCD_RS

    return [
        value,  # E low
        value | LCD_E,  # E high
        value,  # E low
    ]


def lcd_raw_nibble(nibble: int) -> list[int]:
    """
    Used during HD44780 power-on initialisation before 4-bit mode
    has been established.

    RS = 0
    """

    return lcd_nibble(nibble, False)


def lcd_byte(
    value: int,
    rs: bool,
    execution_us: float = 50,
) -> list[int]:
    """
    Write one complete HD44780 byte in 4-bit mode.

    High nibble first, then low nibble.

    execution_us gives the LCD time to execute the instruction/data
    after the second nibble has been latched.
    """

    waveform = []

    waveform.extend(lcd_nibble((value >> 4) & 0x0F, rs))

    waveform.extend(lcd_nibble(value & 0x0F, rs))

    hold_state(waveform, execution_us)

    return waveform


def lcd_command(
    command: int,
    execution_us: float = 50,
) -> list[int]:

    return lcd_byte(
        command,
        False,
        execution_us,
    )


def lcd_data(value: int) -> list[int]:

    return lcd_byte(
        value,
        True,
        50,
    )


def lcd_set_address(address: int) -> list[int]:

    return lcd_command(0x80 | (address & 0x7F))


def lcd_write_text(text: str) -> list[int]:

    waveform = []

    for char in text:
        waveform.extend(lcd_data(ord(char)))

    return waveform


def build_lcd_waveform(
    line1: str,
    line2: str,
) -> list[int]:
    """
    Build an update for a complete 16x2 display.

    Both lines are padded to 16 characters so stale characters
    from a previous longer message are erased.
    """

    line1 = str(line1 or "")[:16].ljust(16)
    line2 = str(line2 or "")[:16].ljust(16)

    waveform = []

    # DDRAM line 1 starts at 0x00
    waveform.extend(lcd_set_address(0x00))

    waveform.extend(lcd_write_text(line1))

    # DDRAM line 2 starts at 0x40
    waveform.extend(lcd_set_address(0x40))

    waveform.extend(lcd_write_text(line2))

    return waveform


def build_lcd_init_waveform() -> list[int]:
    """
    HD44780 4-bit power-on initialisation.

    This intentionally contains the long startup delays inside the
    waveform rather than relying on Python sleeps between GPIO writes.
    """

    waveform = []

    # Start with all LCD control/data lines low.
    waveform.append(0x00)

    # Power-on wait: >15 ms
    # hold_state(waveform, 15_000)

    # Function-set bootstrap:
    #
    # 0011
    # wait >4.1 ms
    #
    waveform.extend(lcd_raw_nibble(0x03))
    hold_state(waveform, 4_100)

    # 0011
    # wait >100 us
    waveform.extend(lcd_raw_nibble(0x03))
    hold_state(waveform, 100)

    # 0011
    waveform.extend(lcd_raw_nibble(0x03))
    hold_state(waveform, 100)

    # 0010 -> enter 4-bit mode
    waveform.extend(lcd_raw_nibble(0x02))
    hold_state(waveform, 100)

    # 4-bit, 2 lines, 5x8 font
    waveform.extend(lcd_command(0x28))

    # Display off
    waveform.extend(lcd_command(0x08))

    # Clear display.
    # This command needs much longer than ordinary instructions.
    waveform.extend(lcd_command(0x01, execution_us=2000))

    # Entry mode:
    # increment address, no display shift
    waveform.extend(lcd_command(0x06))

    # Display on, cursor off, blink off
    waveform.extend(lcd_command(0x0C))

    return waveform


# ---------------------------------------------------------------------------
# MY9221 waveform generation
# ---------------------------------------------------------------------------


def led_word(word: int) -> list[int]:
    """
    Send one 16-bit MY9221 word MSB-first.

    The MY9221 uses both clock transitions. This follows the behaviour
    of the Seeed Grove LED Bar driver: DATA is established and CLOCK
    alternates state for each transmitted bit.

    Only D1/D0 are ever set by this generator.
    """

    waveform = []

    clock = 0
    word &= 0xFFFF

    for _ in range(16):

        value = 0x00

        if word & 0x8000:
            value |= LED_DATA

        if clock:
            value |= LED_CLOCK

        waveform.append(value)

        clock ^= 1
        word = (word << 1) & 0xFFFF

    return waveform


def build_led_waveform(level: int) -> list[int]:
    """
    Generate a complete Grove 10-segment LED bar update.

    level is 0-10.

    The module contains a 12-channel MY9221, hence twelve 16-bit
    channel values are transmitted even though the bar exposes
    ten visible segments.
    """

    level = max(0, min(10, int(level)))

    waveform = []

    # MY9221 command word
    waveform.extend(led_word(0x0000))

    # Twelve MY9221 channels.
    #
    # First ten correspond to the visible bar segments.
    # Remaining two are left off.
    for channel in range(12):

        if channel < level and channel < 10:
            brightness = 0xFFFF
        else:
            brightness = 0x0000

        waveform.extend(led_word(brightness))

    # ---------------------------------------------------------------
    # MY9221 latch sequence
    # ---------------------------------------------------------------

    # DATA low
    waveform.append(0x00)

    # Two CLOCK pulses
    waveform.extend(
        [
            LED_CLOCK,
            0x00,
            LED_CLOCK,
            0x00,
        ]
    )

    # DATA low for >=240 us
    hold_state(
        waveform,
        240,
    )

    # Four DATA pulses
    for _ in range(4):
        waveform.extend(
            [
                LED_DATA,
                0x00,
            ]
        )

    # >=1 us before final clock
    hold_state(
        waveform,
        1,
    )

    # Final CLOCK pulse
    waveform.extend(
        [
            LED_CLOCK,
            0x00,
        ]
    )

    return waveform


# ---------------------------------------------------------------------------
# Waveform compositor
# ---------------------------------------------------------------------------


def combine_waveforms(
    lcd: list[int] | None,
    led: list[int] | None,
    gpio_state: int,
) -> bytearray:
    """
    Combine independent LCD and LED GPIO waveforms.

    LCD owns D7-D2.
    LED owns D1-D0.

    If one waveform is shorter, its final state is held while the
    other peripheral continues.

    If a peripheral has no update at all, its current physical GPIO
    state is preserved.
    """

    if lcd is None and led is None:
        return bytearray()

    current_lcd = gpio_state & LCD_MASK
    current_led = gpio_state & LED_MASK

    lcd_length = len(lcd) if lcd is not None else 0
    led_length = len(led) if led is not None else 0

    length = max(
        lcd_length,
        led_length,
    )

    result = bytearray()

    for i in range(length):

        if lcd is not None and i < lcd_length:
            lcd_state = lcd[i] & LCD_MASK

        elif lcd is not None:
            lcd_state = lcd[-1] & LCD_MASK

        else:
            lcd_state = current_lcd

        if led is not None and i < led_length:
            led_state = led[i] & LED_MASK

        elif led is not None:
            led_state = led[-1] & LED_MASK

        else:
            led_state = current_led

        result.append(lcd_state | led_state)

    return result


# ---------------------------------------------------------------------------
# FTDI USB
# ---------------------------------------------------------------------------


def find_bulk_out_endpoint(device):
    """
    Find the FT245R bulk OUT endpoint rather than assuming its address.
    """

    configuration = device.get_active_configuration()

    interface = configuration[(0, 0)]

    endpoint = usb.util.find_descriptor(
        interface,
        custom_match=lambda ep: usb.util.endpoint_direction(ep.bEndpointAddress)
        == usb.util.ENDPOINT_OUT
        and usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK,
    )

    if endpoint is None:
        raise RuntimeError("FT245R bulk OUT endpoint not found")

    return endpoint


def set_ftdi_baud(device, baud: int) -> None:
    """
    Set the FTDI baud-rate divisor.

    For the FT245R's normal 3 MHz baud-rate base:

        divisor = 3,000,000 / baud

    9600 gives 312.5, represented by FTDI's fractional divisor
    encoding as divisor 312 + 1/2.

    Encoded value:
        integer divisor = 312 = 0x0138
        1/2 fraction code = 0x4000

        wValue = 0x4138
    """

    if baud != 9600:
        raise ValueError("This implementation currently expects BITBANG_BAUD=9600")

    divisor = 0x4138

    device.ctrl_transfer(
        0x40,  # vendor, host -> device
        FTDI_SIO_SET_BAUDRATE,  # request 0x03
        divisor,  # wValue
        0x0001,  # interface/channel 1
        None,
        timeout=500,
    )


def enable_async_bitbang(device) -> None:
    """
    Enable FT245R asynchronous bit-bang mode.

    wValue:
        high byte = 0x01 -> async bit-bang
        low byte  = 0xFF -> D7-D0 all outputs

        0x01FF
    """

    value = FTDI_DIRECTION_MASK | (FTDI_BITMODE_ASYNC << 8)

    device.ctrl_transfer(
        0x40,
        FTDI_SIO_SET_BITMODE,
        value,
        0x0001,
        None,
        timeout=500,
    )


def open_ft245r():

    try:
        device = usb.core.find(
            idVendor=FTDI_VENDOR_ID,
            idProduct=FTDI_PRODUCT_ID,
        )

    except usb.core.NoBackendError:
        print("No USB backend available - " "running display in GPIO debug mode")
        return None, None

    if device is None:
        print("FT245R not found - " "running display in GPIO debug mode")
        return None, None

    try:
        if device.is_kernel_driver_active(0):
            device.detach_kernel_driver(0)

    except (NotImplementedError, usb.core.USBError):
        pass

    try:
        device.set_configuration()

    except usb.core.USBError:
        pass

    endpoint = find_bulk_out_endpoint(device)

    set_ftdi_baud(
        device,
        BITBANG_BAUD,
    )

    enable_async_bitbang(device)

    return device, endpoint


def write_gpio(waveform: bytes | bytearray) -> None:

    global current_gpio

    if not waveform:
        return

    if ftdi_endpoint is None:

        duration_ms = len(waveform) / BITBANG_RATE * 1000

        print(f"GPIO [{len(waveform)} bytes, " f"{duration_ms:.2f} ms]:")

        print(" ".join(f"{value:02X}" for value in waveform))

        current_gpio = waveform[-1]
        return

    written = ftdi_endpoint.write(
        waveform,
        timeout=1000,
    )

    if written != len(waveform):
        raise RuntimeError(f"Short FT245R write: " f"{written}/{len(waveform)} bytes")

    current_gpio = waveform[-1]


# ---------------------------------------------------------------------------
# Physical display operations
# ---------------------------------------------------------------------------


def initialise_displays() -> None:
    """
    Initialise the HD44780.

    The LED bar does not require the same power-on command sequence.
    """

    global current_gpio

    lcd_wave = build_lcd_init_waveform()

    gpio = combine_waveforms(
        lcd_wave,
        None,
        current_gpio,
    )

    write_gpio(gpio)


def update_displays(page_num: int, now: float, force: bool = False) -> None:

    global next_page_flip
    global displayed_page_num

    next_page_flip = now + PAGE_FLIP_INTERVAL

    if page_num == displayed_page_num and not force:
        return

    page = lcd_pages[page_num]

    line1 = page[0] if len(page) > 0 else None
    line2 = page[1] if len(page) > 1 else None
    signal_strength = page[2] if len(page) > 2 else None

    lcd_wave = None
    led_wave = None

    # A page containing either LCD line means that the LCD should
    # receive an update.
    if line1 is not None or line2 is not None:

        lcd_wave = build_lcd_waveform(
            line1 or "",
            line2 or "",
        )

    # None explicitly means "leave the LED bar alone".
    if signal_strength is not None:

        led_wave = build_led_waveform(signal_strength)

    gpio = combine_waveforms(
        lcd_wave,
        led_wave,
        current_gpio,
    )

    write_gpio(gpio)

    displayed_page_num = page_num


# ---------------------------------------------------------------------------
# Display process
# ---------------------------------------------------------------------------


def run_display(
    updates: Queue,
    stop_event: Any,
):

    global next_page_flip
    global lcd_pages
    global lcd_page_num
    global ftdi_device
    global ftdi_endpoint

    try:
        ftdi_device, ftdi_endpoint = open_ft245r()

        initialise_displays()

        update_displays(
            lcd_page_num,
            time.monotonic(),
        )

        while stop_event is None or not stop_event.is_set():

            now = time.monotonic()

            force_update = False

            latest = None

            try:
                latest = updates.get_nowait()

            except queue.Empty:
                pass

            if latest is not None:

                if "pitch_speed" in latest and latest["pitch_speed"] is not None:

                    text = latest["pitch_speed"].get("text")

                    if text:
                        lcd_pages[0][1] = text
                        force_update = True

                if "status" in latest and latest["status"] is not None:

                    text = latest["status"].get("text")

                    if text:
                        lcd_pages[0][0] = text
                        force_update = True

                if "interfaces" in latest and latest["interfaces"] is not None:

                    # Keep scorebug page 0 and rebuild the network
                    # pages from the latest scan.
                    lcd_pages = [lcd_pages[0]]

                    for interface in latest["interfaces"]:

                        info = interface.get("info") or {}

                        lcd_pages.append(
                            [
                                interface.get("interface", ""),
                                interface.get("interface_ip", ""),
                                info.get("signal_bars"),
                            ]
                        )

            if force_update:

                lcd_page_num = 0

                update_displays(lcd_page_num, now, force_update)

                # Scorebug/status/pitch information owns the LCD
                # for three seconds.
                next_page_flip = now + SCOREBUG_DISPLAY_TIME

            elif now >= next_page_flip:

                # No scorebug update for three seconds:
                # rotate through network pages.

                if len(lcd_pages) > 1:

                    lcd_page_num += 1

                    if lcd_page_num >= len(lcd_pages):
                        lcd_page_num = 1

                    update_displays(
                        lcd_page_num,
                        now,
                    )

                else:

                    lcd_page_num = 0

                    update_displays(
                        lcd_page_num,
                        now,
                    )

            # Avoid spinning a CPU core while waiting for either
            # messages or the next page deadline.
            time.sleep(0.01)

    except usb.core.USBError as error:
        print(f"FT245R USB error: {error}")

    except Exception as error:
        print(f"Display error: {error}")

    finally:

        if ftdi_device is not None:

            try:
                usb.util.dispose_resources(ftdi_device)
            except Exception:
                pass
