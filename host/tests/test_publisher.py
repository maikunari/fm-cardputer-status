import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from fmstatus import publisher

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class PublisherHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "home-summary.json")
        self.write_fixture("active")
        self.source = publisher.StatusSource(self.path)
        self.server = publisher.ThreadingHTTPServer(
            ("127.0.0.1", 0), publisher.make_handler(self.source, token="s3cret"))
        self.base = "http://127.0.0.1:{}".format(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp)

    def write_fixture(self, name, **overrides):
        with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as fh:
            summary = json.load(fh)
        summary["generated_epoch"] = int(time.time())
        summary.update(overrides)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh)

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.headers, exc.read()

    def test_status_with_query_token(self):
        code, headers, body = self.get("/status?t=s3cret")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertLessEqual(len(body), 512)
        payload = json.loads(body)
        self.assertEqual(payload["v"], 1)
        self.assertEqual(payload["level"], "yellow")
        self.assertEqual(payload["n"], {"calls": 0, "working": 2, "held": 1})
        self.assertEqual(payload["seq"], 1)

    def test_status_with_header_token(self):
        code, _, _ = self.get("/status", {"X-FMS-Token": "s3cret"})
        self.assertEqual(code, 200)

    def test_status_rejects_missing_or_wrong_token(self):
        self.assertEqual(self.get("/status")[0], 401)
        self.assertEqual(self.get("/status?t=nope")[0], 401)

    def test_healthz_needs_no_token(self):
        code, _, body = self.get("/healthz")
        self.assertEqual((code, json.loads(body)), (200, {"ok": True}))

    def test_unknown_path_is_404(self):
        self.assertEqual(self.get("/state/home-summary.json")[0], 404)

    def test_post_is_rejected(self):
        req = urllib.request.Request(self.base + "/status?t=s3cret", data=b"x", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        ctx.exception.close()
        self.assertEqual(ctx.exception.code, 501)

    def test_seq_advances_on_change_and_missing_file_is_stale(self):
        first = json.loads(self.get("/status?t=s3cret")[2])
        self.write_fixture("idle")
        self.source._cached_at = None  # skip the 2 s read cache
        second = json.loads(self.get("/status?t=s3cret")[2])
        self.assertEqual(second["level"], "green")
        self.assertEqual(second["seq"], first["seq"] + 1)
        os.remove(self.path)
        self.source._cached_at = None
        third = json.loads(self.get("/status?t=s3cret")[2])
        self.assertEqual((third["level"], third["label"]), ("stale", "fm summary missing"))
        self.assertEqual(third["seq"], second["seq"] + 1)

    def test_seq_is_stable_without_change(self):
        a = json.loads(self.get("/status?t=s3cret")[2])
        b = json.loads(self.get("/status?t=s3cret")[2])
        self.assertEqual(a["seq"], b["seq"])


class Config(unittest.TestCase):
    def test_parse_bind(self):
        self.assertEqual(publisher.parse_bind("10.0.0.5:9000"), ("10.0.0.5", 9000))
        self.assertEqual(publisher.parse_bind("10.0.0.5"), ("10.0.0.5", 8765))
        host, port = publisher.parse_bind(":9001")
        self.assertTrue(host)
        self.assertEqual(port, 9001)

    def test_summary_path_from_env(self):
        self.assertEqual(publisher.summary_path_from_env({"FM_HOME": "/fm"}),
                         "/fm/state/home-summary.json")
        self.assertEqual(publisher.summary_path_from_env(
            {"FM_HOME": "/fm", "FMS_SUMMARY_PATH": "/x.json"}), "/x.json")
        self.assertIsNone(publisher.summary_path_from_env({}))

    def test_main_requires_a_summary_source(self):
        self.assertEqual(publisher.main([], env={}), 2)


if __name__ == "__main__":
    unittest.main()
