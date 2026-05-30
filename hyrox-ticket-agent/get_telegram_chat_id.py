#!/usr/bin/env python3
import json
import sys
import urllib.request


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python get_telegram_chat_id.py "BOT_TOKEN"')

    token = sys.argv[1]
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    updates = data.get("result", [])
    if not updates:
        print("No messages found. First send any message to your bot in Telegram, then run again.")
        return

    for update in updates[-10:]:
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        chat = message.get("chat", {})
        print(json.dumps({
            "chat_id": chat.get("id"),
            "chat_type": chat.get("type"),
            "username": chat.get("username"),
            "first_name": chat.get("first_name"),
            "last_name": chat.get("last_name"),
            "text": message.get("text"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

