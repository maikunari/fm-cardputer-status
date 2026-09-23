"""Host-side checks for firmware/apps/fm_status.py.

The app only calls run() under MicroPython, so CPython can import it and
exercise the pure helpers, fetch from the real publisher, and drive run()
with fake M5 hardware.
"""

import importlib.util
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest

from fmstatus import publisher

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_PATH = os.path.join(ROOT, "firmware", "apps", "fm_status.py")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

_spec = importlib.util.spec_from_file_location("fm_status", APP_PATH)
fm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fm)


def measure(text):
    return 12 * len(text)  # DejaVu9 at size 2 is roughly 12 px/char


class Url(unittest.TestCase):
    def test_parse_url(self):
        self.assertEqual(fm.parse_url("http://192.168.1.5:8765/status"),
                         ("192.168.1.5", 8765, "/status"))
        self.assertEqual(fm.parse_url("http://desk"), ("desk", 80, "/"))
        with self.assertRaises(ValueError):
            fm.parse_url("https://desk/status")

    def test_build_request_appends_quoted_token(self):
        host, port, req = fm.build_request("http://10.0.0.2:8765/status", "a b&c")
        self.assertEqual((host, port), ("10.0.0.2", 8765))
        self.assertTrue(req.startswith(b"GET /status?t=a%20b%26c HTTP/1.0\r\n"))
        self.assertTrue(req.endswith(b"\r\n\r\n"))


class Response(unittest.TestCase):
    def raw(self, code, body):
        return "HTTP/1.0 {} X\r\nContent-Type: application/json\r\n\r\n{}".format(code, body).encode()

    def test_ok(self):
        payload = fm.parse_response(self.raw(200, '{"v":1,"level":"red","label":"x","n":{},"age":1,"seq":2}'))
        self.assertEqual(payload["level"], "red")

    def test_errors_are_short_reasons(self):
        cases = [
            (b"garbage", "bad response"),
            (self.raw(401, "{}"), "bad token"),
            (self.raw(404, "{}"), "http 404"),
            (self.raw(200, "not json"), "bad json"),
            (self.raw(200, '{"v":2,"level":"red"}'), "protocol ?"),
            (self.raw(200, '{"v":1,"level":"blue"}'), "level ?"),
        ]
        for raw, reason in cases:
            with self.assertRaises(ValueError) as ctx:
                fm.parse_response(raw)
            self.assertEqual(str(ctx.exception), reason)


class View(unittest.TestCase):
    payload = {"v": 1, "level": "green", "label": "ready", "n": {"calls": 0, "working": 0, "held": 2}, "age": 10}

    def test_fresh_payload_is_shown(self):
        self.assertEqual(fm.view_for(self.payload, 5000, True), ("green", "ready", 15))

    def test_device_goes_stale_after_30s(self):
        self.assertEqual(fm.view_for(self.payload, 30000, True)[0], "green")
        self.assertEqual(fm.view_for(self.payload, 30001, True), ("stale", "stale - no data", 40))
        self.assertEqual(fm.view_for(self.payload, 30001, False)[1], "no wifi")

    def test_no_data_and_bad_config(self):
        self.assertEqual(fm.view_for(None, None, False), ("stale", "waiting for wifi", None))
        self.assertEqual(fm.view_for(self.payload, 0, True, "no config"), ("stale", "no config", None))

    def test_footer(self):
        self.assertEqual(fm.footer_text(self.payload, 15, -61), "c0 w0 h2  15s  -61dB")
        self.assertEqual(fm.footer_text(None, None, None), "c0 w0 h0  --  no wifi")
        self.assertEqual(fm.fmt_age(900), "15m")
        self.assertEqual(fm.fmt_age(7200), "2h")


class Layout(unittest.TestCase):
    def test_fit(self):
        self.assertEqual(fm.fit("short", 228, measure), "short")
        cut = fm.fit("x" * 40, 228, measure)
        self.assertTrue(cut.endswith(".."))
        self.assertLessEqual(measure(cut), 228)

    def test_wrap2_splits_on_words_and_cuts_second_line(self):
        lines = fm.wrap2("2 working - Example task name and a lot more text", 228, measure)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "2 working - Example")
        self.assertTrue(all(measure(line) <= 228 for line in lines))
        self.assertTrue(lines[1].endswith(".."))

    def test_wrap2_single_line_and_giant_word(self):
        self.assertEqual(fm.wrap2("ready", 228, measure), ["ready"])
        self.assertEqual(len(fm.wrap2("y" * 60, 228, measure)), 1)

    def test_every_demo_level_is_styled(self):
        self.assertEqual({p["level"] for p in fm.DEMO}, set(fm.STYLE))


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, text):
        path = os.path.join(self.tmp, "cfg.json")
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_example_config_is_valid(self):
        cfg, err = fm.load_cfg(os.path.join(ROOT, "firmware", "fm_status_cfg.example.json"))
        self.assertIsNone(err)
        self.assertEqual(cfg["poll_s"], 5)

    def test_bad_configs(self):
        self.assertEqual(fm.load_cfg(os.path.join(self.tmp, "missing.json"))[1], "no config")
        self.assertEqual(fm.load_cfg(self.write("{"))[1], "config not json")
        self.assertEqual(fm.load_cfg(self.write("{}"))[1], "config: no url")
        self.assertEqual(fm.load_cfg(self.write('{"url":"ftp://x"}'))[1], "config: bad url")


class PublisherCase(unittest.TestCase):
    """Serves captain_decision.json (fresh) with token "tok"."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "home-summary.json")
        with open(os.path.join(FIXTURES, "captain_decision.json")) as fh:
            summary = json.load(fh)
        summary["generated_epoch"] = int(time.time())
        with open(path, "w") as fh:
            json.dump(summary, fh)
        self.server = publisher.ThreadingHTTPServer(
            ("127.0.0.1", 0), publisher.make_handler(publisher.StatusSource(path), token="tok"))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:{}/status".format(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp)


class FetchAgainstPublisher(PublisherCase):
    """The device's raw-socket GET talks to the real publisher."""

    def app(self, token):
        app = fm.App(None, None, None, socket)
        app.cfg = {"url": self.url, "token": token}
        return app

    def test_fetch_red(self):
        payload = self.app("tok").fetch()
        self.assertEqual(payload["level"], "red")
        self.assertEqual(payload["label"], "1 call - example open decision")

    def test_fetch_wrong_token(self):
        with self.assertRaises(ValueError) as ctx:
            self.app("nope").fetch()
        self.assertEqual(str(ctx.exception), "bad token")


class FakeTime:
    """MicroPython's ticks API on a virtual clock that sleep_ms advances."""

    def __init__(self):
        self.ms = 1000

    def ticks_ms(self):
        return self.ms

    def ticks_diff(self, a, b):
        return a - b

    def ticks_add(self, a, b):
        return a + b

    def sleep_ms(self, ms):
        self.ms += ms


class FakeLcd:
    class FONTS:
        DejaVu9 = object()

    def __init__(self):
        self.size = 1
        self.fills = []
        self.text = []

    def setFont(self, font):
        pass

    def setTextSize(self, size):
        self.size = size

    def setTextColor(self, fg, bg):
        pass

    def setBrightness(self, value):
        self.brightness = value

    def textWidth(self, text):
        return 6 * self.size * len(text)

    def fillScreen(self, color):
        self.fills.append(color)

    def fillRect(self, x, y, w, h, color):
        pass

    def drawString(self, text, x, y):
        self.text.append(text)


class FakeWlan:
    def active(self, *args):
        return True

    def isconnected(self):
        return True

    def status(self, what):
        return -58


class RunLoop(PublisherCase):
    """Drive run() with fake M5 hardware, a scripted keyboard and the real publisher."""

    def run_app(self, keys):
        import sys
        import types
        from unittest import mock

        cfg_path = os.path.join(self.tmp, "fm_status_cfg.json")
        with open(cfg_path, "w") as fh:
            json.dump({"url": self.url, "token": "tok", "poll_s": 5}, fh)
        lcd = FakeLcd()
        resets = []
        script = list(keys)

        class Keyboard:
            def tick(self):
                pass

            def get_key(self):
                return script.pop(0) if script else 0x1B

        modules = {
            "M5": types.SimpleNamespace(Lcd=lcd, Speaker=types.SimpleNamespace(tone=lambda f, d: None)),
            "machine": types.SimpleNamespace(reset=lambda: resets.append(True)),
            "hardware": types.SimpleNamespace(MatrixKeyboard=Keyboard),
            "network": types.SimpleNamespace(STA_IF=0, WLAN=lambda _: FakeWlan()),
            "usocket": socket,
        }
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(fm, "time", FakeTime()), \
                mock.patch.object(fm, "CFG_PATH", cfg_path):
            fm.run()
        return lcd, resets

    def test_polls_paints_red_and_exits_to_launcher(self):
        lcd, resets = self.run_app([None, None, ord("r"), None])
        self.assertEqual(resets, [True])
        self.assertIn(fm.STYLE["red"][0], lcd.fills)
        self.assertIn("NEEDS YOU", lcd.text)
        self.assertIn("c1 w1 h0  0s  -58dB", lcd.text)

    def test_every_key_path(self):
        keys = [ord(c) for c in "nnnnbbbm"] + [ord("m"), ord("r"), ord("Q")]
        lcd, resets = self.run_app(keys)
        self.assertEqual(resets, [True])
        for bg, _, title in fm.STYLE.values():
            self.assertIn(bg, lcd.fills)
        self.assertIn("chirp on red: on", lcd.text)


if __name__ == "__main__":
    unittest.main()
