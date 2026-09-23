"""Firstmate status board for the Cardputer-ADV (UiFlow2 + Buddy launcher).

Polls the fm-cardputer-status publisher on the desk machine over Wi-Fi and
paints the whole 240x135 screen one colour:

  green   ready: nothing running, nothing waiting on the captain
  yellow  working: calm sailboat (no WORKING banner) — fleet under way
  red     needs you: an open captain call (or a crew stuck past the debounce)
  stale   no fresh data: never trust an old green

The publisher decides the level (docs/protocol.md). This app only renders
it, and independently goes stale when no poll has succeeded for STALE_MS.

Keys:  r poll now   b brightness   m red chirp on/off   n demo levels
       q / ESC back to the launcher (machine.reset(), the Buddy convention)

Config: /flash/fm_status_cfg.json (see firmware/fm_status_cfg.example.json).
Read-only: nothing flows back to Firstmate, and no BLE is touched, so
Claude Buddy's radio state is left alone.
"""

import sys
import time

try:
    import ujson as json
except ImportError:
    import json

CFG_PATH = "/flash/fm_status_cfg.json"
STALE_MS = 30000
HTTP_TIMEOUT_S = 3
WIFI_BACKOFF_MS = (2000, 4000, 8000, 16000, 30000)

W = 240
H = 135
FOOTER_H = 18

BLACK = 0x000000
WHITE = 0xFFFFFF
CREAM = 0xF0EEE6
DARK = 0x1F1F1F
GRAY = 0x9E9E9E
SEA = 0x0B1220
SEA_WAVE = 0x1B3A5F
BOAT = 0xD4A84B
BOAT_HULL = 0xC9A227

# level -> (background, text, title). Yellow title is unused; boat means working.
STYLE = {
    "red": (0xC62828, WHITE, "NEEDS YOU"),
    "yellow": (SEA, CREAM, ""),
    "green": (0x2E7D32, WHITE, "READY"),
    "stale": (0x37474F, 0xB0BEC5, "STALE"),
}

BRIGHTNESS = (40, 120, 255)

DEMO = (
    {"v": 1, "level": "green", "label": "ready - 1 held", "n": {"calls": 0, "working": 0, "held": 1}, "age": 12, "seq": 1},
    {"v": 1, "level": "yellow", "label": "2 working - Example task 1...", "n": {"calls": 0, "working": 2, "held": 0}, "age": 40, "seq": 2},
    {"v": 1, "level": "red", "label": "1 call - example open decision...", "n": {"calls": 1, "working": 1, "held": 0}, "age": 7, "seq": 3},
    {"v": 1, "level": "stale", "label": "fm stale 16m", "n": {"calls": 0, "working": 0, "held": 0}, "age": 960, "seq": 4},
)


# ---------------------------------------------------------------- pure helpers
# No device imports below this line until run(); host tests exercise these.

def draw_boat(lcd, cx, cy, scale=1):
    """Minimal Firstmate-style sailboat (hull + mast + triangle sail).

    Drawn with fill/line primitives so UiFlow2 needs no image assets.
    cx, cy = hull center. scale is integer 1 or 2.
    """
    s = int(scale) if scale else 1
    if s < 1:
        s = 1
    # hull (trapezoid via two triangles + mid rect)
    hx, hy = cx, cy
    w, h = 22 * s, 7 * s
    try:
        lcd.fillTriangle(hx - w, hy, hx + w, hy, hx + w - 4 * s, hy + h, BOAT_HULL)
        lcd.fillTriangle(hx - w, hy, hx - w + 4 * s, hy + h, hx + w - 4 * s, hy + h, BOAT_HULL)
        lcd.fillRect(hx - w + 4 * s, hy, (2 * w - 8 * s), h, BOAT_HULL)
    except Exception:
        lcd.fillRect(hx - w // 2, hy, w, h, BOAT_HULL)
    # mast
    mx, my = hx - 2 * s, hy - 22 * s
    try:
        lcd.fillRect(mx, my, 2 * s, 22 * s, BOAT)
    except Exception:
        pass
    # sail (right triangle)
    try:
        lcd.fillTriangle(
            mx + 2 * s, my + 2 * s,
            mx + 2 * s, my + 18 * s,
            mx + 16 * s, my + 18 * s,
            BOAT,
        )
    except Exception:
        lcd.fillRect(mx + 2 * s, my + 6 * s, 12 * s, 10 * s, BOAT)



def parse_url(url):
    """'http://host[:port]/path?q' -> (host, port, path_and_query)."""
    if not url.startswith("http://"):
        raise ValueError("only http:// urls are supported")
    rest = url[7:]
    slash = rest.find("/")
    hostport, path = (rest, "/") if slash < 0 else (rest[:slash], rest[slash:])
    host, port = hostport, 80
    if ":" in hostport:
        host, port_s = hostport.rsplit(":", 1)
        port = int(port_s)
    if not host:
        raise ValueError("url has no host")
    return host, port, path


def _quote(text):
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~"
    out = []
    for ch in str(text):
        if ch in safe:
            out.append(ch)
        else:
            for b in ch.encode("utf-8"):
                out.append("%%%02X" % b)
    return "".join(out)


def build_request(url, token=None):
    host, port, path = parse_url(url)
    if token:
        path += ("&" if "?" in path else "?") + "t=" + _quote(token)
    req = "GET {} HTTP/1.0\r\nHost: {}:{}\r\nAccept: application/json\r\n\r\n".format(path, host, port)
    return host, port, req.encode()


def parse_response(raw):
    """Raw HTTP/1.0 response bytes -> validated protocol v1 dict.

    Raises ValueError with a short, screen-sized reason on anything odd.
    """
    split = raw.find(b"\r\n\r\n")
    if split < 0:
        raise ValueError("bad response")
    head, body = raw[:split], raw[split + 4:]
    status_line = head.split(b"\r\n", 1)[0].split()
    code = int(status_line[1]) if len(status_line) > 1 else 0
    if code == 401:
        raise ValueError("bad token")
    if code != 200:
        raise ValueError("http {}".format(code))
    try:
        payload = json.loads(body.decode())
    except (ValueError, UnicodeError):
        raise ValueError("bad json")
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("protocol ?")
    if payload.get("level") not in STYLE:
        raise ValueError("level ?")
    return payload


def view_for(payload, since_ms, link_up, cfg_error=None):
    """Decide what to paint: (level, label, age_s or None).

    Staleness here is the device's own: the publisher cannot vouch for a
    payload the device failed to refresh.
    """
    if cfg_error:
        return "stale", cfg_error, None
    if payload is None or since_ms is None:
        return "stale", "waiting for wifi" if not link_up else "stale - no data", None
    age = payload.get("age")
    age_s = None if age is None else age + since_ms // 1000
    if since_ms > STALE_MS:
        return "stale", "no wifi" if not link_up else "stale - no data", age_s
    return payload["level"], payload.get("label") or "", age_s


def fmt_age(age_s):
    if age_s is None:
        return "--"
    if age_s < 120:
        return "{}s".format(age_s)
    if age_s < 7200:
        return "{}m".format(age_s // 60)
    return "{}h".format(age_s // 3600)


def footer_text(payload, age_s, rssi):
    n = (payload or {}).get("n") or {}
    parts = ["c{} w{} h{}".format(n.get("calls", 0), n.get("working", 0), n.get("held", 0)),
             fmt_age(age_s)]
    parts.append("{}dB".format(rssi) if rssi is not None else "no wifi")
    return "  ".join(parts)


def fit(text, width, measure, ellipsis=".."):
    """Longest prefix of text (plus ellipsis if cut) no wider than width."""
    if measure(text) <= width:
        return text
    while text and measure(text + ellipsis) > width:
        text = text[:-1]
    return text.rstrip() + ellipsis


def wrap2(text, width, measure):
    """Split into at most two lines that fit width; the second may be cut."""
    if measure(text) <= width:
        return [text]
    words = text.split(" ")
    line = ""
    for i, word in enumerate(words):
        trial = word if not line else line + " " + word
        if measure(trial) > width:
            if not line:  # one giant word: hard-cut it
                return [fit(text, width, measure)]
            return [line, fit(" ".join(words[i:]), width, measure)]
        line = trial
    return [line]


def load_cfg(path=CFG_PATH):
    """-> (cfg dict, error string or None)."""
    try:
        with open(path) as fh:
            cfg = json.load(fh)
    except OSError:
        return {}, "no config"
    except ValueError:
        return {}, "config not json"
    if not isinstance(cfg, dict) or not cfg.get("url"):
        return {}, "config: no url"
    try:
        parse_url(cfg["url"])
    except ValueError:
        return {}, "config: bad url"
    return cfg, None


# ---------------------------------------------------------------- device side

class App:
    def __init__(self, M5, lcd, network, socket):
        self.M5 = M5
        self.lcd = lcd
        self.network = network
        self.socket = socket
        self.cfg, self.cfg_error = load_cfg(CFG_PATH)
        self.poll_ms = int(self.cfg.get("poll_s", 5) * 1000)
        self.chirp = bool(self.cfg.get("chirp", False))
        self.bright_i = 1
        self.payload = None
        self.fetched_ms = None
        self.last_err = None
        self.next_poll_ms = 0
        self.demo_i = None
        self.shown = None
        self.shown_footer = None
        self.prev_level = None
        self.sta = None
        self.wifi_try = 0
        self.next_wifi_ms = 0

    # -- wifi -------------------------------------------------------------
    def wifi_tick(self, now):
        """Keep the STA link up without blocking the UI loop.

        Reuses a link the launcher already brought up; otherwise connects to
        the configured SSID and retries with capped backoff.
        """
        if self.network is None:
            return False
        if self.sta is None:
            try:
                self.sta = self.network.WLAN(self.network.STA_IF)
                if not self.sta.active():
                    self.sta.active(True)
            except Exception as e:
                print("fm_status: wlan init:", e)
                self.sta = None
                return False
        if self.sta.isconnected():
            self.wifi_try = 0
            return True
        ssid = self.cfg.get("ssid")
        if ssid and time.ticks_diff(now, self.next_wifi_ms) >= 0:
            delay = WIFI_BACKOFF_MS[min(self.wifi_try, len(WIFI_BACKOFF_MS) - 1)]
            self.wifi_try += 1
            self.next_wifi_ms = time.ticks_add(now, delay)
            try:
                self.sta.disconnect()
            except Exception:
                pass
            try:
                self.sta.connect(ssid, self.cfg.get("psk", ""))
            except Exception as e:
                print("fm_status: wifi connect:", e)
        return False

    def rssi(self):
        try:
            if self.sta is not None and self.sta.isconnected():
                return self.sta.status("rssi")
        except Exception:
            pass
        return None

    # -- http -------------------------------------------------------------
    def fetch(self):
        """One GET over a raw socket: HTTP/1.0, 3 s timeout, tiny body.

        A raw socket is used instead of requests2/urequests because its
        timeout behaviour is predictable on every UiFlow2 build.
        """
        host, port, req = build_request(self.cfg["url"], self.cfg.get("token"))
        addr = self.socket.getaddrinfo(host, port)[0][-1]
        s = self.socket.socket()
        try:
            s.settimeout(HTTP_TIMEOUT_S)
            s.connect(addr)
            s.send(req)
            chunks = []
            size = 0
            while size < 4096:
                data = s.recv(512)
                if not data:
                    break
                chunks.append(data)
                size += len(data)
        finally:
            s.close()
        return parse_response(b"".join(chunks))

    def poll(self, now, link_up):
        self.next_poll_ms = time.ticks_add(now, self.poll_ms)
        if self.cfg_error or not link_up:
            return
        try:
            self.payload = self.fetch()
            self.fetched_ms = time.ticks_ms()
            self.last_err = None
        except Exception as e:
            self.last_err = str(e)[:24]
            print("fm_status: poll failed:", e)

    # -- screen -----------------------------------------------------------
    def paint(self, level, label, footer, now_ms=0):
        lcd = self.lcd
        bg, fg, title = STYLE[level]
        # Working (yellow): calm boat scene; bob every ~400 ms so it feels alive.
        if level == "yellow":
            bob = 1 if ((now_ms // 400) % 2) == 0 else 0
            key = (level, label, bob)
            if key != self.shown:
                lcd.fillScreen(bg)
                # soft wave band
                try:
                    lcd.fillRect(0, H - FOOTER_H - 10, W, 10, SEA_WAVE)
                except Exception:
                    pass
                draw_boat(lcd, W // 2, 78 + bob, 2)
                # short task label under boat (no WORKING banner)
                lcd.setTextColor(fg, bg)
                lcd.setTextSize(1)
                line = fit(label, W - 16, lcd.textWidth)
                lcd.drawString(line, (W - lcd.textWidth(line)) // 2, H - FOOTER_H - 28)
                self.shown = key
                self.shown_footer = None
        elif (level, label) != self.shown:
            lcd.fillScreen(bg)
            lcd.setTextColor(fg, bg)
            if title:
                lcd.setTextSize(3)
                lcd.drawString(title, (W - lcd.textWidth(title)) // 2, 10)
            lcd.setTextSize(2)
            lines = wrap2(label, W - 12, lcd.textWidth)
            y = 58 if len(lines) == 2 else 68
            if not title:
                y = 40
            for line in lines:
                lcd.drawString(line, (W - lcd.textWidth(line)) // 2, y)
                y += 22
            self.shown = (level, label)
            self.shown_footer = None
        if footer != self.shown_footer:
            lcd.fillRect(0, H - FOOTER_H, W, FOOTER_H, DARK)
            lcd.setTextSize(1)
            lcd.setTextColor(GRAY, DARK)
            lcd.drawString(fit(footer, W - 12, lcd.textWidth), 6, H - 14)
            self.shown_footer = footer

    def chirp_on_red(self, level):
        """One short tone when entering red, if enabled (off by default)."""
        entering = level == "red" and self.prev_level != "red"
        self.prev_level = level
        if not (entering and self.chirp):
            return
        try:
            self.M5.Speaker.tone(2000, 120)
        except Exception as e:
            print("fm_status: chirp:", e)

    def render(self, now, link_up):
        if self.demo_i is not None:
            p = DEMO[self.demo_i]
            level, label, age_s = p["level"], "[demo] " + p["label"], p["age"]
            footer = footer_text(p, age_s, self.rssi())
        else:
            since_ms = None if self.fetched_ms is None else time.ticks_diff(now, self.fetched_ms)
            level, label, age_s = view_for(self.payload, since_ms, link_up, self.cfg_error)
            footer = footer_text(self.payload, age_s, self.rssi())
            if self.last_err and level == "stale":
                footer = self.last_err + "  " + footer
            self.chirp_on_red(level)
        self.paint(level, label, footer, now)

    def set_brightness(self):
        try:
            self.lcd.setBrightness(BRIGHTNESS[self.bright_i])
        except Exception as e:
            print("fm_status: brightness:", e)

    def flash_note(self, text):
        """Show a one-line note in the footer until the next repaint."""
        self.lcd.fillRect(0, H - FOOTER_H, W, FOOTER_H, DARK)
        self.lcd.setTextSize(1)
        self.lcd.setTextColor(CREAM, DARK)
        self.lcd.drawString(text, 6, H - 14)
        self.shown_footer = text

    # -- keys -------------------------------------------------------------
    def on_key(self, ch, now, link_up):
        """Return True to exit."""
        if ch in ("q", "\x1b"):
            return True
        if ch == "r":
            self.demo_i = None
            self.flash_note("polling...")
            self.poll(now, link_up)
        elif ch == "b":
            self.bright_i = (self.bright_i + 1) % len(BRIGHTNESS)
            self.set_brightness()
        elif ch == "m":
            self.chirp = not self.chirp
            self.flash_note("chirp on red: " + ("on" if self.chirp else "off"))
            time.sleep_ms(700)
        elif ch == "n":
            self.demo_i = 0 if self.demo_i is None else (self.demo_i + 1) % len(DEMO)
        return False


def _keychar(k):
    if k is None:
        return None
    if isinstance(k, int):
        if k == 0x1B:
            return "\x1b"
        if 0x20 <= k <= 0x7E:
            return chr(k).lower()
        return None
    if isinstance(k, str) and k:
        return k[0].lower()
    return None


def run():
    import gc

    import M5
    import machine
    from hardware import MatrixKeyboard

    try:
        import network
    except ImportError:
        network = None
    try:
        import usocket as socket
    except ImportError:
        import socket

    lcd = M5.Lcd
    try:
        lcd.setFont(lcd.FONTS.DejaVu9)
    except Exception as e:
        print("fm_status: setFont fallback:", e)

    app = App(M5, lcd, network, socket)
    app.set_brightness()
    kb = MatrixKeyboard()
    # Swallow the Enter that launched us, same 400 ms the Buddy apps use.
    time.sleep_ms(400)
    try:
        while True:
            now = time.ticks_ms()
            link_up = app.wifi_tick(now)
            kb.tick()
            ch = _keychar(kb.get_key())
            if ch is not None and app.on_key(ch, now, link_up):
                return
            if app.demo_i is None and time.ticks_diff(now, app.next_poll_ms) >= 0:
                app.poll(now, link_up)
                gc.collect()
            app.render(time.ticks_ms(), link_up)
            time.sleep_ms(40)
    finally:
        try:
            lcd.fillScreen(BLACK)
        except Exception as e:
            print("fm_status: clear warning:", e)
        time.sleep_ms(200)
        machine.reset()


# The Buddy launcher runs apps by importing them, so run() is called at
# module level. On CPython (host tests) the module only provides helpers.
if sys.implementation.name == "micropython":
    run()
