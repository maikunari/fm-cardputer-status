# Device notes (milestone M0)

Fill this in for **your** unit after first connect. Do not commit real serial
numbers, hostnames, or LAN addresses if you fork publicly.

## Expected hardware

| Check | What you want to see |
|---|---|
| `lsusb` | `303a:816b M5Stack Cardputer-ADV(UiFlow2)` (UiFlow2 MicroPython) |
| Serial symlink | `/dev/serial/by-id/usb-M5Stack_Cardputer-ADV*` → a `ttyACM*` node |
| Permissions | node is typically `root:uucp 0660`; your user must be in group `uucp` |
| Pusher | `mpremote` (or Buddy `push.py` + `pyserial`) available on `PATH` |

## Probe once `uucp` access works

```sh
mpremote connect /dev/serial/by-id/usb-M5Stack_Cardputer-ADV* exec "
import os, sys
print('flash', os.listdir('/flash'))
print('apps', os.listdir('/flash/apps'))
print(sys.implementation)
for m in ('network', 'usocket', 'socket', 'requests2', 'urequests'):
    try:
        __import__(m); print(m, 'ok')
    except ImportError as e:
        print(m, 'missing', e)
"
```

The app needs `network` and `usocket` (or `socket`). It does not use
`requests2` or `urequests`: it speaks HTTP/1.0 over a raw socket so the 3 s
timeout behaves the same on every UiFlow2 build.

**Stop if** there is no `/flash/main.py` launcher or no `/flash/apps/`
directory. That means the Buddy bundle is not what is installed. Stock UiFlow2's
App List also scans `/flash/apps/`, so the app should still appear there, but
check before pushing.

## Observed output

_(paste your probe output here locally; keep forks free of identifying detail)_
