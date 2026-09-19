import datetime as dt
import importlib.util
import json
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "check_hyrox.py"
SPEC = importlib.util.spec_from_file_location("check_hyrox", MODULE_PATH)
AGENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AGENT)
SEOUL = AGENT.WATCHES[0]
SHANGHAI = AGENT.WATCHES[1]

EVENT_NAMES = {
    "seoul_open_men": "AirAsia | HYROX Seoul | Season 26/27",
    "shanghai_open_men": "ZhongAn HYROX Shanghai | Season 26/27",
}


def checkout_html(tickets, sale_status="onSale", event_name="AirAsia | HYROX Seoul | Season 26/27"):
    data = {
        "props": {
            "pageProps": {
                "event": {
                    "name": event_name,
                    "saleStatus": sale_status,
                    "tickets": tickets,
                }
            }
        }
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data)
        + "</script></html>"
    )


def target_ticket(name="HYROX MEN 남자 오픈 | Friday", remaining=0, active=True):
    return {
        "id": "ticket-1",
        "name": name,
        "active": active,
        "v": remaining,
        "styleOptions": {},
        "meta": {"competition_class_matching_key": "SOLO_OPEN_M"},
    }


def watch_for_url(url):
    return next(watch for watch in AGENT.WATCHES if watch["checkout_url"] == url)


def fetched(watch, tickets, sale_status="onSale"):
    return {
        "body": checkout_html(
            tickets,
            sale_status=sale_status,
            event_name=EVENT_NAMES[watch["id"]],
        ),
        "url": watch["checkout_url"],
        "http_status": 200,
    }


class CheckoutParsingTests(unittest.TestCase):
    def test_available_only_when_exact_target_has_positive_inventory(self):
        result = AGENT.classify_checkout(
            AGENT.parse_checkout_page(checkout_html([target_ticket(remaining=3)])),
            SEOUL,
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["matched_tickets"][0]["remaining"], 3)

    def test_zero_or_negative_inventory_is_unavailable(self):
        tickets = [
            target_ticket("HYROX MEN 남자 오픈 | Friday", remaining=-4),
            target_ticket("HYROX MEN 남자 오픈 | Saturday", remaining=-21),
        ]
        result = AGENT.classify_checkout(AGENT.parse_checkout_page(checkout_html(tickets)), SEOUL)
        self.assertEqual(result["status"], "unavailable")

    def test_doubles_pro_mixed_and_bad_partner_metadata_are_rejected(self):
        tickets = [
            target_ticket("HYROX DOUBLES MEN | Friday", remaining=8),
            target_ticket("HYROX MEN PRO | Friday", remaining=8),
            target_ticket("HYROX DOUBLES MIXED | Friday", remaining=8),
            target_ticket("KLOOK | HYROX WOMEN | Friday", remaining=8),
        ]
        with self.assertRaisesRegex(ValueError, "exact Men's Open Singles"):
            AGENT.classify_checkout(AGENT.parse_checkout_page(checkout_html(tickets)), SEOUL)

    def test_missing_inventory_fails_instead_of_claiming_no_tickets(self):
        ticket = target_ticket()
        ticket.pop("v")
        with self.assertRaisesRegex(ValueError, "did not return inventory"):
            AGENT.classify_checkout(AGENT.parse_checkout_page(checkout_html([ticket])), SEOUL)

    def test_wrong_event_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unexpected event"):
            AGENT.classify_checkout(
                AGENT.parse_checkout_page(
                    checkout_html([target_ticket(remaining=2)], event_name="HYROX Shanghai")
                ),
                SEOUL,
            )

    def test_shanghai_open_men_uses_the_same_exact_category(self):
        result = AGENT.classify_checkout(
            AGENT.parse_checkout_page(
                checkout_html(
                    [target_ticket("HYROX MEN | Saturday", remaining=4)],
                    event_name=EVENT_NAMES[SHANGHAI["id"]],
                )
            ),
            SHANGHAI,
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["matched_tickets"][0]["name"], "HYROX MEN | Saturday")


class NotificationTests(unittest.TestCase):
    def test_failed_daily_check_sends_explicit_error_receipt(self):
        sent = []
        stored = {}

        def fail_fetch(_url):
            raise TimeoutError("checkout timed out")

        def sender(text, preview=False):
            sent.append((text, preview))

        state = AGENT.run(
            now_utc=dt.datetime(2026, 9, 13, 7, 36, tzinfo=dt.timezone.utc),
            fetcher=fail_fetch,
            sender=sender,
            state_loader=lambda: {},
            state_saver=lambda value: stored.update(value),
            send_daily=True,
        )

        self.assertEqual(state["watches"]["seoul_open_men"]["status"], "check_failed")
        self.assertEqual(state["watches"]["shanghai_open_men"]["status"], "check_failed")
        self.assertEqual(len(sent), 1)
        self.assertIn("Результат: проверка не удалась", sent[0][0])
        self.assertIn("TimeoutError", sent[0][0])
        self.assertIn(SEOUL["checkout_url"], sent[0][0])
        self.assertIn(SHANGHAI["checkout_url"], sent[0][0])

    def test_broken_state_is_reported_as_a_failed_check(self):
        sent = []

        def broken_loader():
            raise ValueError("invalid state JSON")

        state = AGENT.run(
            now_utc=dt.datetime(2026, 9, 13, 7, 36, tzinfo=dt.timezone.utc),
            fetcher=lambda url: fetched(watch_for_url(url), [target_ticket(remaining=1)]),
            sender=lambda text, preview=False: sent.append(text),
            state_loader=broken_loader,
            state_saver=lambda _value: None,
            send_daily=True,
        )

        self.assertEqual(state["watches"]["seoul_open_men"]["status"], "check_failed")
        self.assertIn("state load failed", sent[0])

    def test_daily_receipt_is_sent_only_once_per_moscow_date(self):
        sent = []
        store = {}

        def loader():
            return dict(store)

        def saver(value):
            store.clear()
            store.update(value)

        def sender(text, preview=False):
            sent.append((text, preview))

        fetcher = lambda url: fetched(watch_for_url(url), [target_ticket(remaining=0)])
        now = dt.datetime(2026, 9, 13, 7, 36, tzinfo=dt.timezone.utc)
        AGENT.run(now, fetcher, sender, loader, saver, send_daily=True)
        AGENT.run(now + dt.timedelta(hours=1), fetcher, sender, loader, saver, send_daily=True)

        self.assertEqual(len(sent), 1)
        self.assertIn("Результат: билетов нет", sent[0][0])

    def test_new_availability_sends_alert_and_separate_receipt(self):
        sent = []
        previous = {
            "watches": {
                "seoul_open_men": {"status": "unavailable"},
                "shanghai_open_men": {"status": "unavailable"},
            },
            "last_daily_receipt_date_msk": None,
        }

        def sender(text, preview=False):
            sent.append((text, preview))

        AGENT.run(
            now_utc=dt.datetime(2026, 9, 13, 7, 36, tzinfo=dt.timezone.utc),
            fetcher=lambda url: fetched(
                watch_for_url(url),
                [target_ticket(remaining=2 if "korea.hyrox.com" in url else 0)],
            ),
            sender=sender,
            state_loader=lambda: previous,
            state_saver=lambda _value: None,
            send_daily=True,
        )

        self.assertEqual(len(sent), 2)
        self.assertIn("HYROX SEOUL: БИЛЕТ ЕСТЬ", sent[0][0])
        self.assertTrue(sent[0][1])
        self.assertIn("Результат: билет есть", sent[1][0])
        self.assertFalse(sent[1][1])

    def test_unchanged_available_status_does_not_repeat_alert(self):
        sent = []
        previous = {
            "watches": {
                "seoul_open_men": {"status": "available"},
                "shanghai_open_men": {"status": "unavailable"},
            },
            "last_daily_receipt_date_msk": "2026-09-13",
        }
        AGENT.run(
            now_utc=dt.datetime(2026, 9, 13, 9, 0, tzinfo=dt.timezone.utc),
            fetcher=lambda url: fetched(
                watch_for_url(url),
                [target_ticket(remaining=2 if "korea.hyrox.com" in url else 0)],
            ),
            sender=lambda text, preview=False: sent.append((text, preview)),
            state_loader=lambda: previous,
            state_saver=lambda _value: None,
            send_daily=False,
        )
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
