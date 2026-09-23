# Protocol v1

The contract between the host publisher (`host/fmstatus/publisher.py`) and the
device app (`firmware/apps/fm_status.py`). The device polls; the host keeps no
per-client state.

## HTTP

```
GET /status            (token: ?t=<token> or header X-FMS-Token: <token>)
GET /healthz           (no token) -> {"ok":true}
```

| Case | Response |
|---|---|
| `/status`, token ok or `FMS_TOKEN` unset | `200` + payload below |
| `/status`, token missing or wrong | `401 {"error":"token"}` |
| any other path | `404 {"error":"not found"}` |
| any method other than GET | `501` |

Responses are `application/json`, `Cache-Control: no-store`,
`Connection: close`. The device speaks HTTP/1.0 over a raw socket with a 3 s
timeout.

## Payload

```json
{"v":1,"level":"red","label":"2 calls - cardputer build","n":{"calls":2,"working":1,"held":0},"age":42,"seq":1817}
```

| Field | Meaning |
|---|---|
| `v` | Always `1`. The device rejects anything else. |
| `level` | `green` \| `yellow` \| `red` \| `stale`. The device trusts it verbatim. |
| `label` | At most 40 chars, printable ASCII only. The publisher transliterates (`…` → `...`, `·`/`—` → `-`) and drops everything else, because DejaVu9 on UiFlow lacks most glyphs. The separator is ` - `, not the plan's `·`, for the same reason. |
| `n.calls` | Items currently driving red (0 unless red). |
| `n.working` | Active crews (`counts.active_children`). |
| `n.held` | Externally held crews (`counts.holds`). |
| `age` | Seconds since the summary's `generated_epoch`; `null` when there is no summary. |
| `seq` | Publisher-local counter that goes up whenever `(level, label)` changes. Restarts at 1 with the process. |

The whole payload stays well under 512 bytes.

## Level rules (host side)

Input: `$FM_HOME/state/home-summary.json`, schema
`fm-secondmate-home-summary.v1`. Only these fields are read: `schema`,
`generated_epoch`, `state`, `decisions_open[].{id,key,verb,summary}`,
`active_children[].{id,name}`, `endpoints[].{id,state}`,
`counts.{active_children,holds}`. First match wins:

1. File missing or unreadable, `schema` differs, `generated_epoch` missing, or
   the summary is more than 900 s old → `stale` (`fm summary missing`,
   `fm schema mismatch`, `fm summary bad`, `fm stale Nm`).
2. `state == "unknown"` → `stale`, label `fm unknown`.
3. `state == "captain_decision"` → `red` immediately. Also red: any
   `decisions_open[]` with verb `blocked` (or `needs-decision` /
   `captain-hold`), and any `endpoints[].state == "failed"`, once the publisher
   has seen it continuously for `FMS_RED_DEBOUNCE` seconds (default 60).
   Label: `N call(s) - <first summary>`.
4. `state == "active_child_work"` → `yellow`, `N working - <first child name>`.
5. `state` is `externally_held` or `no_active_work` → `green`, `ready` or
   `ready - N held`.

Any other `state` value is treated as schema drift → `stale` (`fm state ?`).
With `FMS_LABELS=counts` the titles are left out (`2 working`, `1 call`).

## Device-side staleness

Independent of the publisher: if no poll has succeeded for 30 s (six missed
5 s polls) the device shows `stale` with `stale - no data` (link up) or
`no wifi` (link down). A green screen can only come from a fresh, valid
response.

## Serial fallback (not implemented)

Planned as milestone M5: one line every 5 s over USB-CDC,

```
FMS1 red 2 calls - cardputer build
```

never sending control bytes (`0x03`/`0x04` would interrupt the REPL). See the
README's follow-ups.
