# fm-cardputer-status

A Firstmate status light for the desk, on an M5Stack Cardputer-ADV. The whole
screen shows one colour:

| Screen | Meaning |
|---|---|
| **green** READY | nothing running, nothing waiting on you (`ready - 1 held` when a crew is parked on an external wait) |
| **yellow** WORKING | at least one crew is busy (`2 working - <task>`) |
| **red** NEEDS YOU | an open captain call, or a crew blocked or failed for more than 60 s |
| **grey** STALE | no fresh data: publisher down, Wi-Fi down, summary older than 15 min, or Firstmate says `unknown` |

A dead pipeline never shows green.

```
 th50 (desk)                                          Cardputer-ADV (UiFlow2 + Buddy launcher)
 Firstmate (unchanged) writes state/home-summary.json
        │ read-only                                   /flash/main.py  (Buddy launcher)
 host/fmstatus/publisher.py  ── GET /status ─Wi-Fi──►   ├─ apps/claude_buddy.py
   systemd --user, :8765, token                         └─ apps/fm_status.py   ◄── this repo
```

- **Firstmate is not modified.** The publisher reads
  `$FM_HOME/state/home-summary.json` (schema `fm-secondmate-home-summary.v1`),
  never writes into `FM_HOME`, and runs no `fm-*` script.
- **Claude Buddy is not touched.** The device app is one extra file in
  `/flash/apps/`, next to Buddy in the launcher menu. No reflash, no button
  sequence, no BLE. To remove it, delete that one file.
- The device polls; nothing flows back to Firstmate.

Wire contract and level rules: [docs/protocol.md](docs/protocol.md).
Device checks (M0): [docs/device-notes.md](docs/device-notes.md).

## Layout

```
firmware/apps/fm_status.py          device app (MicroPython, single file)
firmware/fm_status_cfg.example.json device config template (real one is gitignored)
firmware/push.sh                    copy app + config to the device over USB-serial
host/fmstatus/rules.py              pure mapping: home-summary -> level/label/counts
host/fmstatus/publisher.py          stdlib HTTP server: GET /status, /healthz
host/tests/                         unittest suite + trimmed real summary fixtures
host/systemd/                       systemd --user unit + env template
host/install-service.sh             installs the unit and env file
```

## Host: publisher

Needs only `python3` (3.8+ stdlib).

```sh
cd host
python3 -m unittest discover -s tests -t .                 # 53 tests, no deps
FM_HOME=~/Projects/firstmate python3 -m fmstatus.publisher --once   # print one payload
FM_HOME=~/Projects/firstmate python3 -m fmstatus.publisher          # serve on LAN-IP:8765
```

Settings are environment variables (full list in
`host/fmstatus/publisher.py` and `host/systemd/env.example`):

| Variable | Default | |
|---|---|---|
| `FM_HOME` | required | Firstmate home to read |
| `FMS_SUMMARY_PATH` | | serve this file instead (fixtures, forcing a colour) |
| `FMS_BIND` | primary LAN IPv4 `:8765` | `host` or `host:port` |
| `FMS_TOKEN` | off | shared secret the device must send |
| `FMS_LABELS` | `full` | `counts` keeps task titles off the LAN |
| `FMS_RED_DEBOUNCE` | `60` | seconds before blocked/failed turns red |

### Run it as a service

```sh
host/install-service.sh          # writes the unit + ~/.config/fm-cardputer-status/env (mode 600)
$EDITOR ~/.config/fm-cardputer-status/env    # set FMS_TOKEN (and FMS_BIND if needed)
systemctl --user enable --now fm-cardputer-status.service
systemctl --user status fm-cardputer-status.service
curl "http://<th50 LAN IP>:8765/status?t=<token>"      # e.g. 192.168.0.9
```

The unit points at this checkout, so re-run `install-service.sh` if you move
it. To keep it running while you're logged out: `loginctl enable-linger`.

th50 runs `ufw`. Allow the device's LAN in:

```sh
sudo ufw allow from 192.168.0.0/24 to any port 8765 proto tcp
```

## Device: install the app

One-time setup:

1. **Serial access.** `/dev/ttyACM0` is `root:uucp 0660`:
   `sudo usermod -aG uucp "$USER"`, then log out and back in.
2. **A pusher**, either:
   - `pipx install mpremote` (preferred), or
   - a clone of [moremas/build-with-claude](https://github.com/moremas/build-with-claude)
     plus `pyserial`, with `BUDDY_REPO=/path/to/build-with-claude`. Its
     `buddy/scripts/push.py` is the one proven against the Buddy bundle.
3. **Check the device** (M0): run the probe in
   [docs/device-notes.md](docs/device-notes.md) and paste the output there.

### Wi-Fi and token ("pairing")

```sh
cp firmware/fm_status_cfg.example.json firmware/fm_status_cfg.json   # gitignored
$EDITOR firmware/fm_status_cfg.json
```

| Key | |
|---|---|
| `ssid`, `psk` | a 2.4 GHz network. Leave `ssid` empty to reuse a link the launcher already brought up |
| `url` | `http://<th50 LAN IP>:8765/status` |
| `token` | the same value as `FMS_TOKEN` in the publisher's env file. That's the whole pairing step |
| `poll_s` | poll interval, default 5 |
| `chirp` | `true` to beep once on entering red (off by default; toggle on the device with `m`) |

The credentials sit in plain text on the device's flash. Anyone holding the
device and a USB cable can read them, so use a guest/IoT SSID if that matters.
Never paste them into chat, issues or git.

### Push

```sh
firmware/push.sh                 # app + config, then reboot the device
firmware/push.sh --app-only      # just the app after code changes
firmware/push.sh --cfg-only      # just the config
```

`push.sh` finds the single `/dev/serial/by-id/usb-M5Stack_Cardputer-ADV*`
link, or takes `--port`. It writes only `/flash/apps/fm_status.py` and
`/flash/fm_status_cfg.json`. It never writes `main.py`, `buddy_*.py` or NVS.
DTR/RTS reset does nothing on native USB; the script reboots with
`machine.reset()` instead.

After the reboot, pick **fm_status** in the launcher (`;` / `.` to move, Enter
to launch).

### On the device

| Key | |
|---|---|
| `r` | poll now (also leaves demo mode) |
| `b` | brightness: 3 steps |
| `m` | red chirp on/off |
| `n` | demo: cycle the four levels with fake data |
| `q` / ESC | back to the launcher (`machine.reset()`, the Buddy convention) |

Footer: `c<calls> w<working> h<held>`, data age, Wi-Fi RSSI. When polls fail,
the last error (`bad token`, `http 404`, a socket error...) is shown first.

### Remove it

```sh
mpremote connect /dev/serial/by-id/usb-M5Stack_Cardputer-ADV* \
  fs rm :/flash/apps/fm_status.py + fs rm :/flash/fm_status_cfg.json + reset
```

Buddy and everything else are untouched.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `push.sh: no read/write access` | join `uucp` (above) and log in again |
| `push.sh: found 0 Cardputer serial links` | cable/hub; `lsusb` should list `303a:816b`. Pass `--port` if the name differs |
| Device stuck on `waiting for wifi` | wrong `ssid`/`psk`, or a 5 GHz-only network |
| `no wifi` / `stale - no data` with a timeout | `ufw` rule missing, wrong IP in `url`, or the publisher is down (`systemctl --user status fm-cardputer-status`) |
| `bad token` in the footer | `token` in the device config ≠ `FMS_TOKEN` |
| `fm stale 20m` | Firstmate's watcher isn't refreshing `home-summary.json` (normally every ≤300 s) |
| `fm unknown` | Firstmate itself reports `state: unknown` (invalid backlog/child state); check Bearings |
| Boot takes ~8 s longer | Buddy's `main.py` tries its event SSID `cardputer` on every boot; harmless |
| `no config` / `config: bad url` | push `fm_status_cfg.json`; the URL must start with `http://` |

Force a colour for testing: point `FMS_SUMMARY_PATH` at an edited copy of a
fixture from `host/tests/fixtures/` and bump its `generated_epoch` to now.

## Follow-ups (not in this MVP)

- **M0 on the real unit**: run once `uucp` access exists; record in
  `docs/device-notes.md`.
- **M5 USB-serial fallback** (`FMS1 <level> <label>` lines over USB-CDC, when
  there's no Wi-Fi). Not built. The CDC port is the MicroPython REPL, so it
  needs care: never send control bytes, frame every line.
- **v1.1 (touches Firstmate)**: a `wedge_suspects` field in the home summary so
  a wedged crew shows red, plus an optional faster summary cadence
  (`FM_HOME_SUMMARY_INTERVAL=60`). Until then, red can lag by up to 5 minutes.
- Secondmate homes are not rolled up (none are registered today).
