#!/usr/bin/env python3
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo


ROOT = pathlib.Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
STATE_PATH = STATE_DIR / "status.json"
REPORT_TZ = ZoneInfo("Europe/Moscow")


def env(name, default=""):
    return os.getenv(name, default).strip()


def env_bool(name, default=False):
    value = env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")
SEND_DAILY_RECEIPT = env_bool("SEND_DAILY_RECEIPT")
FORCE_DAILY_RECEIPT = env_bool("FORCE_DAILY_RECEIPT") or env_bool("FORCE_STATUS_REPORT")

WATCH = {
    "id": "seoul_open_men",
    "name": "HYROX Seoul",
    "date_label": "13-15 November 2026",
    "category_label": "Men's Open Singles / HYROX MEN",
    "checkout_url": env(
        "SEOUL_OPEN_MEN_CHECKOUT_URL",
        "https://korea.hyrox.com/checkout/69fafdfb5e85aa5b5e0ae5a1",
    ),
    "expected_event_pattern": r"\bHYROX\s+Seoul\b",
    "target_key": "SOLO_OPEN_M",
}


class NextDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.in_next_data = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag.lower() == "script" and attributes.get("id") == "__NEXT_DATA__":
            self.in_next_data = True

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_next_data:
            self.in_next_data = False

    def handle_data(self, data):
        if self.in_next_data:
            self.parts.append(data)


def fetch_page(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return {
            "body": response.read().decode(charset, errors="replace"),
            "url": response.geturl(),
            "http_status": response.status,
        }


def parse_checkout_page(raw):
    parser = NextDataParser()
    parser.feed(raw)
    if not parser.parts:
        raise ValueError("checkout does not contain __NEXT_DATA__")

    try:
        data = json.loads("".join(parser.parts))
        page_props = data["props"]["pageProps"]
        event = page_props["event"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("checkout data has an unexpected structure") from exc

    if not isinstance(event, dict) or not isinstance(event.get("tickets"), list):
        raise ValueError("checkout data does not contain a ticket list")
    return event


def is_target_ticket(ticket):
    name = str(ticket.get("name", "")).strip()
    meta = ticket.get("meta") if isinstance(ticket.get("meta"), dict) else {}
    key = meta.get("competition_class_matching_key")

    # The name check protects us from known bad metadata on partner tickets.
    exact_name = re.match(r"^HYROX MEN(?:\s|$)", name, flags=re.I) is not None
    excluded = re.search(r"DOUBLES|MIXED|PRO|RELAY|ADAPTIVE|WOMEN", name, flags=re.I)
    return exact_name and not excluded and key == WATCH["target_key"]


def availability_value(ticket):
    value = ticket.get("v")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def ticket_snapshot(ticket):
    styles = ticket.get("styleOptions") if isinstance(ticket.get("styleOptions"), dict) else {}
    return {
        "id": ticket.get("id") or ticket.get("_id"),
        "name": str(ticket.get("name", "")).strip(),
        "active": ticket.get("active") is True,
        "hidden": styles.get("hiddenInSelectionArea") is True,
        "remaining": availability_value(ticket),
    }


def classify_checkout(event):
    event_name = str(event.get("name", "")).strip()
    if re.search(WATCH["expected_event_pattern"], event_name, flags=re.I) is None:
        raise ValueError(f"unexpected event in checkout: {event_name or 'missing name'}")

    target_tickets = [ticket_snapshot(ticket) for ticket in event["tickets"] if is_target_ticket(ticket)]
    if not target_tickets:
        raise ValueError("exact Men's Open Singles category was not found")

    sale_status = event.get("saleStatus")
    if sale_status not in {"onSale", "soldOut", "planned", "past"}:
        raise ValueError(f"unknown checkout sale status: {sale_status!r}")

    selectable = [ticket for ticket in target_tickets if ticket["active"] and not ticket["hidden"]]
    available = [ticket for ticket in selectable if ticket["remaining"] is not None and ticket["remaining"] > 0]
    if sale_status == "onSale" and available:
        status = "available"
    elif sale_status != "onSale" or not selectable:
        status = "unavailable"
    elif all(ticket["remaining"] is not None for ticket in selectable):
        status = "unavailable"
    else:
        raise ValueError("checkout did not return inventory for every target ticket")

    return {
        "event_name": event_name,
        "sale_status": sale_status,
        "status": status,
        "matched_tickets": target_tickets,
    }


def load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read state: {exc}") from exc


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def previous_watch(previous):
    if isinstance(previous.get("watch"), dict):
        return previous["watch"]
    old_watch = previous.get("watches", {}).get("seoul", {})
    return old_watch if isinstance(old_watch, dict) else {}


def check_target(previous, fetcher=fetch_page):
    fetched = fetcher(WATCH["checkout_url"])
    if not isinstance(fetched, dict) or not isinstance(fetched.get("body"), str):
        raise ValueError("fetcher returned an unexpected response")

    parsed = classify_checkout(parse_checkout_page(fetched["body"]))
    checked_url = fetched.get("url") or WATCH["checkout_url"]
    proof = {
        "event_name": parsed["event_name"],
        "sale_status": parsed["sale_status"],
        "tickets": parsed["matched_tickets"],
    }
    content_hash = hashlib.sha256(
        json.dumps(proof, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return {
        "id": WATCH["id"],
        "event": parsed["event_name"],
        "date": WATCH["date_label"],
        "category": WATCH["category_label"],
        "checked_url": checked_url,
        "http_status": fetched.get("http_status"),
        "status": parsed["status"],
        "sale_status": parsed["sale_status"],
        "category_verified": True,
        "matched_tickets": parsed["matched_tickets"],
        "content_hash": content_hash,
        "previous_status": previous.get("status"),
        "check_ok": True,
        "error": None,
    }


def failed_result(previous, exc):
    return {
        "id": WATCH["id"],
        "event": WATCH["name"],
        "date": WATCH["date_label"],
        "category": WATCH["category_label"],
        "checked_url": WATCH["checkout_url"],
        "http_status": None,
        "status": "check_failed",
        "sale_status": None,
        "category_verified": False,
        "matched_tickets": [],
        "content_hash": previous.get("content_hash"),
        "previous_status": previous.get("status"),
        "check_ok": False,
        "error": f"{type(exc).__name__}: {exc}",
    }


def send_telegram(text, preview=False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Telegram secrets are missing")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": not preview,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API error {exc.code}: {error_body[:300]}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Telegram API returned invalid JSON") from exc

    if body.get("ok") is not True:
        raise RuntimeError(f"Telegram rejected the message: {body}")
    message_id = body.get("result", {}).get("message_id")
    print(f"Telegram accepted message_id={message_id}")


def as_moscow(now_utc=None):
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
    return now_utc.astimezone(REPORT_TZ)


def result_phrase(status):
    return {
        "available": "билет есть",
        "unavailable": "билетов нет",
        "check_failed": "проверка не удалась",
    }[status]


def ticket_evidence(result):
    evidence = []
    for ticket in result.get("matched_tickets", []):
        remaining = ticket.get("remaining")
        if ticket.get("active") and not ticket.get("hidden") and isinstance(remaining, int):
            detail = f"остаток {max(0, remaining)}"
        elif not ticket.get("active") or ticket.get("hidden"):
            detail = "недоступна для выбора"
        else:
            detail = "остаток не подтвержден"
        evidence.append(f"{ticket.get('name')}: {detail}")
    return evidence


def build_daily_receipt(result, now_utc=None):
    checked_at = as_moscow(now_utc).strftime("%d.%m.%Y %H:%M MSK")
    lines = [
        "Ежедневная проверка HYROX",
        f"Время проверки по Москве: {checked_at}",
        f"Событие: {result['event']} ({result['date']})",
        f"Категория: {result['category']}",
        f"Фактически проверенный URL: {result['checked_url']}",
        f"Результат: {result_phrase(result['status'])}",
    ]
    if result["status"] == "check_failed":
        lines.append(f"Ошибка: {result.get('error') or 'неизвестная ошибка'}")
    else:
        lines.extend(f"Позиция: {line}" for line in ticket_evidence(result))
    return "\n".join(lines)


def build_availability_alert(result, now_utc=None):
    checked_at = as_moscow(now_utc).strftime("%d.%m.%Y %H:%M MSK")
    available_names = [
        ticket["name"]
        for ticket in result.get("matched_tickets", [])
        if ticket.get("active")
        and not ticket.get("hidden")
        and isinstance(ticket.get("remaining"), int)
        and ticket["remaining"] > 0
    ]
    lines = [
        "HYROX SEOUL: БИЛЕТ ЕСТЬ",
        f"Категория: {result['category']}",
        f"Доступно: {', '.join(available_names)}",
        f"Купить: {result['checked_url']}",
        f"Проверено: {checked_at}",
    ]
    return "\n".join(lines)


def should_send_alert(result):
    return result["status"] == "available" and result.get("previous_status") != "available"


def run(
    now_utc=None,
    fetcher=fetch_page,
    sender=send_telegram,
    state_loader=load_state,
    state_saver=save_state,
    send_daily=SEND_DAILY_RECEIPT,
    force_receipt=FORCE_DAILY_RECEIPT,
):
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    state_error = None
    try:
        previous = state_loader()
    except Exception as exc:
        previous = {}
        state_error = exc
    old_watch = previous_watch(previous)

    if state_error is not None:
        result = failed_result(old_watch, RuntimeError(f"state load failed: {state_error}"))
    else:
        try:
            result = check_target(old_watch, fetcher=fetcher)
        except Exception as exc:
            result = failed_result(old_watch, exc)

    today_msk = as_moscow(now_utc).date().isoformat()
    last_receipt = previous.get("last_daily_receipt_date_msk")
    if last_receipt is None:
        last_receipt = previous.get("last_daily_report_date_msk")
    scheduled_receipt_due = send_daily and last_receipt != today_msk

    if should_send_alert(result):
        sender(build_availability_alert(result, now_utc), preview=True)

    if scheduled_receipt_due or force_receipt:
        sender(build_daily_receipt(result, now_utc), preview=False)
        if scheduled_receipt_due:
            last_receipt = today_msk

    state = {
        "schema_version": 2,
        "checked_at_utc": now_utc.astimezone(dt.timezone.utc).isoformat(),
        "last_daily_receipt_date_msk": last_receipt,
        "watch": result,
    }
    state_saver(state)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return state


def main():
    run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
