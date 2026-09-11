#!/usr/bin/env python3
"""Send a message through the shared interview bot (see interview_bot.py) to a real
user's Telegram chat, optionally with a row of numbered inline-keyboard buttons.

Uses the shared _core interview bot's own token (INTERVIEWER_BOT_TOKEN env var, set
once in `_core/.env`) — one bot, reused by every project, deliberately never a
product's own bot token (see interview_bot.py's module docstring for why). Talks to
the raw Bot API via `requests`, no python-telegram-bot dependency needed.

Usage:
  send_dm.py --telegram-id <id> --text "<message>" [--buttons "1,2,3,4,5"]

Exits 0 silently on success. Exits 1 with an error on stderr on failure.
"""
import argparse
import os
import sys

import requests

API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


class SendDmError(Exception):
    pass


def send(telegram_id, text, buttons=None):
    token = os.environ.get("INTERVIEWER_BOT_TOKEN")
    if not token:
        raise SendDmError("INTERVIEWER_BOT_TOKEN is not set in the environment")

    payload = {"chat_id": telegram_id, "text": text}
    if buttons:
        labels = [b.strip() for b in buttons.split(",") if b.strip()]
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": label, "callback_data": label} for label in labels]
            ]
        }

    response = requests.post(API_BASE.format(token=token), json=payload, timeout=15)
    body = response.json()
    if response.status_code != 200 or not body.get("ok"):
        raise SendDmError(body.get("description", f"HTTP {response.status_code}"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telegram-id", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--buttons", default=None, help="comma-separated labels, e.g. 1,2,3,4,5")
    args = parser.parse_args()

    try:
        send(args.telegram_id, args.text, args.buttons)
    except SendDmError as e:
        print(f"send_dm error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
