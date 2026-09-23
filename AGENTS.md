# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Tests: `cd host && python3 -m unittest discover -s tests -t .` (stdlib only). They cover the rules, the publisher over real HTTP, and the device app's helpers plus a faked `run()` loop.
- `firmware/apps/fm_status.py` runs on UiFlow2 MicroPython: stick to the MicroPython subset (no `typing`, `dataclasses`, `bytes.partition` or other CPython-only stdlib). Device imports (`M5`, `machine`, `hardware`, `network`) stay inside `run()`, which only fires under MicroPython, so the module imports on CPython for tests.
- The wire contract is `docs/protocol.md`; change it and `host/fmstatus/rules.py` together.
- Hard limits: the publisher stays read-only on `FM_HOME` (no `fm-*` scripts); never push anything to the device except `apps/fm_status.py` and `fm_status_cfg.json` (Claude Buddy's `main.py`, `buddy_*.py` and NVS must stay untouched); `firmware/fm_status_cfg.json` holds Wi-Fi creds and is gitignored.
- Device facts (serial link, `uucp` access, M0 probe) live in `docs/device-notes.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
