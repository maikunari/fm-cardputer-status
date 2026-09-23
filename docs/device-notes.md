# Device notes (milestone M0)

## Status: not yet verified on the device

The build worker could not open the serial port, so the M0 checks below have
not been run. What was observed from the host on 2026-09-23:

| Check | Result |
|---|---|
| `lsusb` | `303a:816b M5Stack Cardputer-ADV(UiFlow2)`, so UiFlow2 MicroPython firmware |
| Serial link | `/dev/serial/by-id/usb-M5Stack_Cardputer-ADV_UiFlow2__aca70402975c0000-if00 -> ../../ttyACM0` |
| Permissions | `/dev/ttyACM0` is `root:uucp 0660`; the desk user is not in `uucp` yet |
| Host pushers | Neither `mpremote` nor `pyserial` is installed |
| Firewall | `ufw` is active on th50, so port 8765 needs an allow rule for the LAN |

## To do once `uucp` access exists

1. `sudo usermod -aG uucp "$USER"`, then log out and back in.
2. Install a pusher: `pipx install mpremote` (or `python3 -m pip install --user mpremote`).
3. List the filesystem and probe modules without changing anything:

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

4. Paste the output below.

The app needs `network` and `usocket` (or `socket`). It does not use
`requests2` or `urequests`: it speaks HTTP/1.0 over a raw socket so the 3 s
timeout behaves the same on every UiFlow2 build.

**Stop if** there is no `/flash/main.py` launcher or no `/flash/apps/`
directory. That means the Buddy bundle is not what is installed. Stock UiFlow2's
App List also scans `/flash/apps/`, so the app should still appear there, but
check before pushing.

## Observed output

_(pending)_
