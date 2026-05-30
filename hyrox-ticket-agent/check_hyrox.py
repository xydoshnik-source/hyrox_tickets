#!/usr/bin/env python3
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
STATE_PATH = STATE_DIR / "status.json"


DEFAULT_EVENT_URL = "https://hyroxsa.com/event/hyrox-seoul/"
DEFAULT_TARGET_CATEGORY = r"HYROX DOUBLES MIXED|Doubles Mixed|Open Mixed|Mixed"


def env(name, default=""):
    return os.getenv(name, default).strip()


EVENT_URL = env("HYROX_EVENT_URL", DEFAULT_EVENT_URL)
TICKET_URL = env("HYROX_TICKET_URL", "")
TARGET_CATEGORY = env("TARGET_CATEGORY", DEFAULT_TARGET_CATEGORY)
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")
NOTIFY_EVERY_RUN = env("NOTIFY_EVERY_RUN", "").lower() in {"1", "true", "yes"}


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def normalize(raw):
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def find_links(raw, base_url):
    links = []
    for match in re.finditer(r'href=["\']([^"\']+)["\']', raw, flags=re.I):
        href = html.unescape(match.group(1))
        full = urllib.parse.urljoin(base_url, href)
        if any(word in full.lower() for word in ["ticket", "register", "booking", "checkout", "event"]):
            links.append(full)
    return sorted(set(links))


def has(pattern, text):
    return re.search(pattern, text, flags=re.I) is not None


def classify(text, links):
    lowered = text.lower()
    category_seen = has(TARGET_CATEGORY, text)

    not_open_markers = [
        "ticket sales start soon",
        "tickets sales start soon",
        "coming soon",
        "sales start soon",
        "registration opens soon",
        "tba",
    ]
    sold_out_markers = [
        "sold out",
        "currently unavailable",
        "no tickets available",
        "not available",
        "waitlist",
    ]
    available_markers = [
        "get your ticket",
        "buy ticket",
        "buy tickets",
        "register now",
        "book now",
        "purchase",
        "add to cart",
        "checkout",
    ]

    not_open = any(marker in lowered for marker in not_open_markers)
    sold_out = any(marker in lowered for marker in sold_out_markers)
    available_words = any(marker in lowered for marker in available_markers)
    ticketish_links = [link for link in links if any(w in link.lower() for w in ["ticket", "register", "booking", "checkout"])]

    if category_seen and available_words and not sold_out:
        return "available"
    if ticketish_links and not not_open and not sold_out:
        return "maybe_available"
    if not_open:
        return "not_open"
    if sold_out:
        return "sold_out"
    return "unknown"


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing; skip notification.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            print(response.read().decode("utf-8")[:500])
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Telegram API error {exc.code}: {body}", file=sys.stderr)
        raise


def build_message(status, previous_status, category_seen, links):
    status_ru = {
        "available": "билеты выглядят доступными",
        "maybe_available": "появился сигнал, что продажи могли открыться",
        "not_open": "продажи ещё не открыты",
        "sold_out": "похоже, распродано",
        "unknown": "статус неясен",
        None: "первый запуск",
    }
    lines = [
        "HYROX Seoul ticket watcher",
        "",
        f"Статус: {status_ru.get(status, status)}",
        f"До этого: {status_ru.get(previous_status, previous_status)}",
        f"Категория найдена на странице: {'да' if category_seen else 'нет'}",
        "",
        f"Страница: {EVENT_URL}",
    ]
    if TICKET_URL:
        lines.append(f"Checkout: {TICKET_URL}")
    if links:
        lines.append("")
        lines.append("Подозрительные ссылки:")
        for link in links[:5]:
            lines.append(f"- {link}")

    if status in {"available", "maybe_available"}:
        lines.insert(0, "⚡ Возможно, пора покупать HYROX Seoul Open/Mixed.")

    return "\n".join(lines)


def main():
    urls = [EVENT_URL]
    if TICKET_URL:
        urls.append(TICKET_URL)

    raw_parts = []
    all_links = []
    for url in urls:
        raw = fetch(url)
        raw_parts.append(raw)
        all_links.extend(find_links(raw, url))

    raw_all = "\n".join(raw_parts)
    text = normalize(raw_all)
    links = sorted(set(all_links))
    status = classify(text, links)
    category_seen = has(TARGET_CATEGORY, text)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    previous = load_state()
    previous_status = previous.get("status")
    previous_hash = previous.get("content_hash")

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    state = {
        "checked_at_utc": now,
        "event_url": EVENT_URL,
        "ticket_url": TICKET_URL,
        "target_category": TARGET_CATEGORY,
        "status": status,
        "category_seen": category_seen,
        "content_hash": content_hash,
        "previous_status": previous_status,
        "page_changed": previous_hash != content_hash,
        "links": links[:20],
    }

    print(json.dumps(state, ensure_ascii=False, indent=2))

    should_notify = NOTIFY_EVERY_RUN
    should_notify = should_notify or status in {"available", "maybe_available"} and status != previous_status
    should_notify = should_notify or previous_status in {None, "not_open", "sold_out", "unknown"} and status == "available"

    save_state(state)

    if should_notify:
        send_telegram(build_message(status, previous_status, category_seen, links))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
