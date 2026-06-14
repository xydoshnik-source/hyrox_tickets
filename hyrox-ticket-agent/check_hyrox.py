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


def env(name, default=""):
    return os.getenv(name, default).strip()


TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")
NOTIFY_EVERY_RUN = env("NOTIFY_EVERY_RUN", "").lower() in {"1", "true", "yes"}

WATCHES = [
    {
        "id": "seoul",
        "name": "Seoul",
        "date_label": "14-15 Nov 2026",
        "target_label": "Open Mixed / Doubles Mixed",
        "event_url": env("SEOUL_EVENT_URL", "https://hyroxsa.com/event/hyrox-seoul/"),
        "ticket_url": env(
            "SEOUL_TICKET_URL",
            "https://korea.hyrox.com/event/hyrox-seoul-season-26-27-vthaza?useEmbed=true",
        ),
        "target_category": env("SEOUL_TARGET_CATEGORY", r"HYROX DOUBLES MIXED|Doubles Mixed|Open Mixed"),
        "ticket_url_pattern": r"korea\.hyrox\.com/event/",
    },
    {
        "id": "bangkok",
        "name": "Bangkok",
        "date_label": "14 Aug 2026",
        "target_label": "Open Men",
        "event_url": env("BANGKOK_EVENT_URL", "https://hyrox.com/event/hyrox-bangkok-2/"),
        "ticket_url": env(
            "BANGKOK_TICKET_URL",
            "https://thailand.hyrox.com/event/hyrox-bangkok-season-26-27-3fhlh8?useEmbed=true",
        ),
        "target_category": env("BANGKOK_TARGET_CATEGORY", r"HYROX MEN OPEN|Open Men|Men Open"),
        "ticket_url_pattern": r"thailand\.hyrox\.com/event/",
    },
]


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


def find_ticket_urls(links, ticket_url_pattern):
    ticket_urls = []
    for link in links:
        if re.search(ticket_url_pattern, link, flags=re.I):
            ticket_urls.append(link)
    return sorted(set(ticket_urls))


def has(pattern, text):
    return re.search(pattern, text, flags=re.I) is not None


def classify(text, links, target_category):
    lowered = text.lower()
    category_seen = has(target_category, text)

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
        "sale has ended",
        "currently unavailable",
        "no tickets available",
        "not available",
        "waitlist",
    ]
    available_markers = [
        "get your ticket",
        "buy ticket",
        "buy tickets",
        "buy tickets here",
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


def check_watch(watch, previous_watch):
    urls = [watch["event_url"]]
    if watch.get("ticket_url"):
        urls.append(watch["ticket_url"])

    raw_parts = []
    all_links = []
    for url in urls:
        raw = fetch(url)
        raw_parts.append(raw)
        all_links.extend(find_links(raw, url))

    ticket_urls = find_ticket_urls(all_links, watch["ticket_url_pattern"])
    for url in ticket_urls:
        if url not in urls:
            raw = fetch(url)
            raw_parts.append(raw)
            all_links.extend(find_links(raw, url))

    raw_all = "\n".join(raw_parts)
    text = normalize(raw_all)
    links = sorted(set(all_links))
    ticket_urls = sorted(set(ticket_urls + find_ticket_urls(links, watch["ticket_url_pattern"])))
    status = classify(text, links, watch["target_category"])
    category_seen = has(watch["target_category"], text)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    return {
        "id": watch["id"],
        "name": watch["name"],
        "date_label": watch["date_label"],
        "target_label": watch["target_label"],
        "event_url": watch["event_url"],
        "ticket_url": watch.get("ticket_url", ""),
        "target_category": watch["target_category"],
        "status": status,
        "category_seen": category_seen,
        "ticket_urls_checked": ticket_urls,
        "content_hash": content_hash,
        "previous_status": previous_watch.get("status"),
        "page_changed": previous_watch.get("content_hash") != content_hash,
    }


def should_notify_watch(result):
    previous_status = result.get("previous_status")
    status = result["status"]
    if NOTIFY_EVERY_RUN:
        return True
    if status in {"available", "maybe_available"} and status != previous_status:
        return True
    if previous_status in {None, "not_open", "sold_out", "unknown"} and status == "available":
        return True
    return False


def status_text(status):
    return {
        "available": "билеты выглядят доступными",
        "maybe_available": "появился сигнал, что продажи могли открыться",
        "not_open": "продажи ещё не открыты",
        "sold_out": "похоже, распродано",
        "unknown": "статус неясен",
        None: "первый запуск",
    }.get(status, status)


def build_message(results):
    checked_at = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).strftime("%d.%m.%Y %H:%M MSK")
    has_buy_signal = any(result["status"] in {"available", "maybe_available"} for result in results)
    lines = ["HYROX ticket watcher"]

    if has_buy_signal:
        lines.insert(0, "⚡ Есть сигнал по билетам HYROX.")

    for result in results:
        ticket_url = result["ticket_url"] or (result["ticket_urls_checked"][0] if result["ticket_urls_checked"] else result["event_url"])
        lines.extend(
            [
                "",
                f"{result['name']} — {result['date_label']}",
                f"Цель: {result['target_label']}",
                f"Статус: {status_text(result['status'])}",
                f"Категория видна на ticket-странице: {'да' if result['category_seen'] else 'нет'}",
                f"Ссылка: {ticket_url}",
            ]
        )

    lines.extend(["", f"Проверено: {checked_at}"])
    return "\n".join(lines)


def main():
    previous = load_state()
    previous_watches = previous.get("watches", {})

    results = []
    for watch in WATCHES:
        result = check_watch(watch, previous_watches.get(watch["id"], {}))
        results.append(result)

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    state = {
        "checked_at_utc": now,
        "watches": {result["id"]: result for result in results},
    }

    print(json.dumps(state, ensure_ascii=False, indent=2))

    save_state(state)

    if any(should_notify_watch(result) for result in results):
        send_telegram(build_message(results))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
