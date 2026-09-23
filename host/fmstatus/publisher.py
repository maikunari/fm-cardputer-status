"""Read-only HTTP publisher: Firstmate home-summary -> Cardputer protocol v1.

Serves exactly two paths, GET only:
  /status   {"v":1,"level":..,"label":..,"n":{..},"age":..,"seq":..}
  /healthz  {"ok":true}
Everything else is 404 (unknown path) or 501 (non-GET method).

It never writes into FM_HOME and never runs a Firstmate script. It reads
$FM_HOME/state/home-summary.json (or FMS_SUMMARY_PATH), caches the parsed
file for CACHE_S seconds, and keeps the red-debounce memory in process.

Environment (all optional except one of FM_HOME / FMS_SUMMARY_PATH):
  FM_HOME            Firstmate home; summary is $FM_HOME/state/home-summary.json
  FMS_SUMMARY_PATH   explicit summary path (overrides FM_HOME; handy for fixtures)
  FMS_BIND           host or host:port to bind (default: primary LAN IPv4, port 8765)
  FMS_TOKEN          when set, requests need ?t=<token> or X-FMS-Token: <token>
  FMS_RED_DEBOUNCE   seconds a blocked/failed item must persist before red (60)
  FMS_LABELS         "full" (default) or "counts" to keep task titles off the LAN
  FMS_VERBOSE        1 to log every request

Run:  cd host && python3 -m fmstatus.publisher [--once] [--bind HOST[:PORT]]
"""

import argparse
import hmac
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from fmstatus import rules

DEFAULT_PORT = 8765
CACHE_S = 2.0


def log(msg):
    sys.stderr.write("fm-cardputer-status: {}\n".format(msg))
    sys.stderr.flush()


def summary_path_from_env(env):
    explicit = env.get("FMS_SUMMARY_PATH")
    if explicit:
        return explicit
    home = env.get("FM_HOME")
    if home:
        return os.path.join(home, "state", "home-summary.json")
    return None


def primary_ipv4():
    """Best-effort LAN address: the source IP of the default route.

    A UDP connect() sends no packet; it only asks the kernel to pick a route.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1, never actually contacted
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def parse_bind(value):
    """'host', 'host:port', ':port' or '' -> (host, port)."""
    value = (value or "").strip()
    host, port = value, DEFAULT_PORT
    if value.count(":") == 1:
        host, _, port_s = value.partition(":")
        port = int(port_s)
    return (host or primary_ipv4()), port


class StatusSource:
    """Owns the cached summary, debounce memory and the change sequence."""

    def __init__(self, path, *, debounce_s=rules.RED_DEBOUNCE_S, labels="full",
                 clock=time.time):
        self.path = path
        self.debounce_s = debounce_s
        self.labels = labels
        self.clock = clock
        self._lock = threading.Lock()
        self._cached_at = None
        self._summary = None
        self._memory = {}
        self._last = None
        self.seq = 0

    def _read(self, now):
        if self._cached_at is not None and now - self._cached_at < CACHE_S:
            return self._summary
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self._summary = json.load(fh)
        except (OSError, ValueError, TypeError) as exc:
            if self._summary is not None or self._cached_at is None:
                log("summary unreadable ({}): {}".format(self.path, exc))
            self._summary = None
        self._cached_at = now
        return self._summary

    def status(self):
        with self._lock:
            now = self.clock()
            summary = self._read(now) if self.path else None
            result, self._memory = rules.evaluate(
                summary, now, self._memory, debounce_s=self.debounce_s, labels=self.labels)
            key = (result["level"], result["label"])
            if key != self._last:
                self.seq += 1
                if self._last is not None:
                    log("{} -> {} ({})".format(self._last[0], key[0], key[1]))
                self._last = key
            payload = {"v": 1, "level": result["level"], "label": result["label"],
                       "n": result["n"], "age": result["age"], "seq": self.seq}
            return payload


def make_handler(source, token=None, verbose=False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "fm-cardputer-status/1"
        sys_version = ""

        def _send(self, code, obj):
            body = json.dumps(obj, separators=(",", ":")).encode("ascii")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self, query):
            if not token:
                return True
            given = self.headers.get("X-FMS-Token") or (parse_qs(query).get("t") or [""])[0]
            return hmac.compare_digest(given.encode(), token.encode())

        def do_GET(self):
            parts = urlsplit(self.path)
            if parts.path == "/healthz":
                self._send(200, {"ok": True})
            elif parts.path == "/status":
                if self._authorized(parts.query):
                    self._send(200, source.status())
                else:
                    self._send(401, {"error": "token"})
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            if verbose:
                log("{} {}".format(self.address_string(), fmt % args))

    return Handler


def main(argv=None, env=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--bind", default=env.get("FMS_BIND", ""),
                    help="host[:port] (default: FMS_BIND, else LAN IPv4:%d)" % DEFAULT_PORT)
    ap.add_argument("--summary", default=summary_path_from_env(env),
                    help="home-summary.json path (default: FMS_SUMMARY_PATH or $FM_HOME/state/...)")
    ap.add_argument("--once", action="store_true", help="print one /status payload and exit")
    args = ap.parse_args(argv)

    if not args.summary:
        log("set FM_HOME or FMS_SUMMARY_PATH (or pass --summary)")
        return 2
    labels = env.get("FMS_LABELS", "full")
    if labels not in ("full", "counts"):
        log("FMS_LABELS must be 'full' or 'counts'")
        return 2
    source = StatusSource(args.summary, labels=labels,
                          debounce_s=int(env.get("FMS_RED_DEBOUNCE", rules.RED_DEBOUNCE_S)))
    if args.once:
        print(json.dumps(source.status(), separators=(",", ":")))
        return 0

    host, port = parse_bind(args.bind)
    token = env.get("FMS_TOKEN") or None
    server = ThreadingHTTPServer((host, port), make_handler(
        source, token=token, verbose=env.get("FMS_VERBOSE") == "1"))
    server.daemon_threads = True
    log("serving http://{}:{}/status from {} (token {})".format(
        host, port, args.summary, "on" if token else "off"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
