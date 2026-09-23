"""Pure mapping: Firstmate home-summary -> Cardputer status (protocol v1).

No I/O here. The publisher reads the summary file and owns the debounce
memory; this module only turns (summary, memory, now) into a level, a label,
some counts and the next memory. See docs/protocol.md for the wire contract.

Rules, first match wins:
  1. summary missing / wrong schema / older than MAX_AGE_S  -> stale
  2. state == "unknown"                                      -> stale
  3. state == "captain_decision", or a red verb / failed
     endpoint that has persisted >= debounce_s               -> red
  4. state == "active_child_work"                            -> yellow
  5. state in {externally_held, no_active_work}              -> green
Any other state value is treated as schema drift -> stale.
"""

import unicodedata

SCHEMA = "fm-secondmate-home-summary.v1"
MAX_AGE_S = 900
RED_DEBOUNCE_S = 60
LABEL_MAX = 40

LEVELS = ("green", "yellow", "red", "stale")

# Verbs whose open decision means "the captain is needed". captain-hold and
# needs-decision already make Firstmate report state=captain_decision; blocked
# only turns red here, after the debounce.
CAPTAIN_VERBS = frozenset(("captain-hold", "needs-decision"))
RED_VERBS = CAPTAIN_VERBS | {"blocked"}

# Typographic characters Firstmate titles commonly carry, mapped to ASCII
# before the generic NFKD strip (which would otherwise just drop them).
_ASCII_MAP = {
    "…": "...",  # ellipsis (fm-fleet-snapshot trunc() appends one)
    "·": "-",    # middle dot
    "•": "-",    # bullet
    "–": "-",    # en dash
    "—": "-",    # em dash
    "→": "->",   # right arrow
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
}


def sanitize_label(text, limit=LABEL_MAX):
    """Return printable ASCII, whitespace-collapsed, at most `limit` chars."""
    if text is None:
        return ""
    text = str(text)
    for src, dst in _ASCII_MAP.items():
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if " " <= ch <= "~" or ch in "\t\r\n")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _stale(label, age=None):
    return {
        "level": "stale",
        "label": sanitize_label(label),
        "n": {"calls": 0, "working": 0, "held": 0},
        "age": age,
    }


def _int(value, default=0):
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def _list(summary, key):
    value = summary.get(key)
    return value if isinstance(value, list) else []


def _counts(summary):
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    return {
        "working": _int(counts.get("active_children"), len(_list(summary, "active_children"))),
        "held": _int(counts.get("holds"), len(_list(summary, "holds"))),
        "decisions": _int(counts.get("decisions_open"), len(_list(summary, "decisions_open"))),
    }


def _red_candidates(summary):
    """Yield (memory_key, verb, title) for every item that can turn red."""
    for d in _list(summary, "decisions_open"):
        if not isinstance(d, dict):
            continue
        verb = d.get("verb")
        if verb not in RED_VERBS:
            continue
        key = "d|{}|{}|{}".format(d.get("id"), d.get("key"), verb)
        yield key, verb, d.get("summary") or d.get("id") or verb
    for e in _list(summary, "endpoints"):
        if isinstance(e, dict) and e.get("state") == "failed":
            yield "e|{}".format(e.get("id")), "failed", "failed: {}".format(e.get("id"))


def evaluate(summary, now, memory=None, *, debounce_s=RED_DEBOUNCE_S,
             max_age_s=MAX_AGE_S, labels="full"):
    """Map a parsed home-summary to a status dict.

    summary   parsed JSON (dict), or None when the file is missing/unreadable
    now       epoch seconds
    memory    {candidate_key: first_seen_epoch} from the previous call
    labels    "full" includes task titles; "counts" sends counts only

    Returns (status, next_memory). status = {level, label, n, age}.
    next_memory keeps only candidates still present, so a decision that
    closes and reopens starts its debounce again.
    """
    memory = memory or {}
    if not isinstance(summary, dict):
        return _stale("fm summary missing"), {}
    if summary.get("schema") != SCHEMA:
        return _stale("fm schema mismatch"), {}
    generated = summary.get("generated_epoch")
    if not isinstance(generated, (int, float)) or isinstance(generated, bool):
        return _stale("fm summary bad"), {}
    age = max(0, int(now - generated))
    if age > max_age_s:
        return _stale("fm stale {}m".format(age // 60), age), {}

    state = summary.get("state")
    if state == "unknown":
        return _stale("fm unknown", age), {}

    counts = _counts(summary)
    next_memory = {}
    red_items = []
    for key, verb, title in _red_candidates(summary):
        first_seen = memory.get(key, now)
        next_memory[key] = first_seen
        immediate = state == "captain_decision" and verb in CAPTAIN_VERBS
        if immediate or now - first_seen >= debounce_s:
            red_items.append(title)

    def status(level, label, calls=0):
        return {
            "level": level,
            "label": sanitize_label(label),
            "n": {"calls": calls, "working": counts["working"], "held": counts["held"]},
            "age": age,
        }

    if state == "captain_decision" or red_items:
        calls = max(len(red_items), 1)
        head = "{} call{}".format(calls, "" if calls == 1 else "s")
        if labels == "full" and red_items:
            head = "{} - {}".format(head, red_items[0])
        return status("red", head, calls), next_memory

    if state == "active_child_work":
        head = "{} working".format(counts["working"])
        children = _list(summary, "active_children")
        if labels == "full" and children and isinstance(children[0], dict):
            first = children[0].get("name") or children[0].get("id")
            if first:
                head = "{} - {}".format(head, first)
        return status("yellow", head), next_memory

    if state in ("externally_held", "no_active_work"):
        held = counts["held"]
        return status("green", "ready - {} held".format(held) if held else "ready"), next_memory

    return _stale("fm state ?", age), {}
