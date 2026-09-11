"""The shared real-user interview channel every project reuses.

One Telegram bot, owned by `_core` (token in `_core/.env` as INTERVIEWER_BOT_TOKEN,
created once via @BotFather) — deliberately separate from any product's own bot, so:
  - it works for products that have no bot at all (a web app, a CLI) — a person just
    has to /start this one bot once, same as any Telegram bot's first-contact rule;
  - polling it never races with a product's own bot long-polling *its* token; and
  - one offset/inbox is shared correctly across every project that runs concurrently,
    instead of each project's `loop.py` racing the others over the same update queue.

Two steps, both idempotent and safe to call every cycle:
  `sync(agents_root)`    — fetch new Telegram messages ONCE, append them to the shared
                           inbox. Call this at most as often as loop.py's own cadence;
                           concurrent callers are serialized with a file lock.
  `deliver(...)`         — for ONE project: scan the shared inbox for messages from
                           people this project listens for (`interview_ids` in its
                           config.yml), append any new ones to its FEEDBACK.md
                           `## Interview` section, and remember how far it's read.

No new schema: the shared inbox is a plain JSONL file, offsets/cursors are plain text
files. `send_dm.py` (same directory) is the other half — it sends, this receives.
"""
import fcntl
import json
import os
import re
from pathlib import Path

import requests

API_BASE = "https://api.telegram.org/bot{token}/getUpdates"

INBOX_NAME = ".interview_inbox.jsonl"
OFFSET_NAME = ".interview_offset"
INTERVIEW_HEADER = "## Interview"


def _shared_dir(agents_root):
    d = Path(agents_root) / ".interview"
    d.mkdir(exist_ok=True)
    return d


def _locked(path):
    """A file used purely as an flock handle, for the single critical section in sync()."""
    f = open(path, "a+")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def sync(agents_root, token=None, timeout=15):
    """Fetch any new messages sent to the shared bot since the last call (from ANY
    project — this is the one place that talks to Telegram) and append them to the
    shared inbox. Returns how many new messages were fetched. No-op if the token isn't
    configured, so a project that doesn't use interviews pays nothing."""
    token = token or os.environ.get("INTERVIEWER_BOT_TOKEN")
    if not token:
        return 0

    shared = _shared_dir(agents_root)
    lock_path = shared / ".lock"
    with _locked(lock_path):
        offset_path = shared / OFFSET_NAME
        offset = int(offset_path.read_text().strip()) if offset_path.exists() else 0

        resp = requests.get(API_BASE.format(token=token),
                             params={"offset": offset, "timeout": 0}, timeout=timeout)
        body = resp.json()
        if not body.get("ok"):
            return 0
        updates = body["result"]
        if not updates:
            return 0

        inbox_path = shared / INBOX_NAME
        new_offset = offset
        written = 0
        with inbox_path.open("a") as inbox:
            for u in updates:
                new_offset = max(new_offset, u["update_id"] + 1)
                msg = u.get("message")
                if not msg or "text" not in msg:
                    continue
                inbox.write(json.dumps({
                    "chat_id": str(msg["chat"]["id"]),
                    "text": msg["text"],
                    "date": msg["date"],
                    "update_id": u["update_id"],
                }, ensure_ascii=False) + "\n")
                written += 1
        offset_path.write_text(str(new_offset))
        return written


def _read_cursor(state_db):
    row = state_db.execute("SELECT value FROM state WHERE key='interview_cursor'").fetchone()
    return int(row[0]) if row else 0


def _write_cursor(state_db, n):
    state_db.execute("INSERT OR REPLACE INTO state VALUES ('interview_cursor', ?)", (str(n),))
    state_db.commit()


def deliver(agents_root, config, state_db):
    """For one project (its parsed config.yml + its own state.db connection): read any
    shared-inbox lines this project hasn't seen yet, keep only the ones from people this
    project actually listens for (`interview_ids: {name: telegram_id}` in config.yml),
    and append them to FEEDBACK.md `## Interview`. Returns how many were appended."""
    interview_ids = config.get("interview_ids") or {}
    if not interview_ids:
        return 0
    id_to_name = {str(v): k for k, v in interview_ids.items()}

    inbox_path = _shared_dir(agents_root) / INBOX_NAME
    if not inbox_path.exists():
        return 0
    lines = inbox_path.read_text().splitlines()

    cursor = _read_cursor(state_db)
    matched = []
    for i, line in enumerate(lines[cursor:], start=cursor):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = id_to_name.get(msg["chat_id"])
        if name:
            matched.append((name, msg))
    _write_cursor(state_db, len(lines))
    if not matched:
        return 0

    product_dir = config.get("vars", {}).get("product_dir", "product")
    feedback_path = Path(config["project_path"]) / product_dir / "FEEDBACK.md"
    if not feedback_path.exists():
        return 0
    _append_replies(feedback_path, matched)
    return len(matched)


def _append_replies(feedback_path, matched):
    from datetime import datetime, timezone
    text = feedback_path.read_text()
    lines_out = []
    for name, msg in matched:
        when = datetime.fromtimestamp(msg["date"], tz=timezone.utc).strftime("%Y-%m-%d")
        lines_out.append(f"**{name} replied ({when}):** {msg['text']}")
    block = "\n".join(lines_out) + "\n"

    if INTERVIEW_HEADER in text:
        # Insert right after the header (and its HTML-comment hint line, if present).
        pattern = re.compile(rf"^{re.escape(INTERVIEW_HEADER)}\s*\n(<!--.*?-->\n)?", re.MULTILINE)
        m = pattern.search(text)
        insert_at = m.end() if m else text.index(INTERVIEW_HEADER) + len(INTERVIEW_HEADER) + 1
        text = text[:insert_at] + block + text[insert_at:]
    else:
        text = text.rstrip("\n") + f"\n\n{INTERVIEW_HEADER}\n{block}"
    feedback_path.write_text(text)
