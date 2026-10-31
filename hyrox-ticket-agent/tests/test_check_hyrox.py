import datetime as dt
import importlib.util
import json
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "check_hyrox.py"
SPEC = importlib.util.spec_from_file_location("check_hyrox", MODULE_PATH)
AGENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AGENT)
SINGAPORE = AGENT.WATCHES[0]
KUALA_LUMPUR = AGENT.WATCHES[1]
EVENT_NAMES = {
    "singapore_open_men": "HYROX Singapore II | Season 26/27",
    "kuala_lumpur_open_men": "AirAsia | HYROX Kuala Lumpur | Season 26/27",
}


def checkout_html(tickets, sale_status="onSale", event_name="HYROX Singapore II | Season 26/27"):
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
            SINGAPORE,
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["matched_tickets"][0]["remaining"], 3)

    def test_zero_or_negative_inventory_is_unavailable(self):
        tickets = [
            target_ticket("HYROX MEN 남자 오픈 | Friday", remaining=-4),
            target_ticket("HYROX MEN 남자 오픈 | Saturday", remaining=-21),
        ]
        result = AGENT.classify_checkout(AGENT.parse_checkout_page(checkout_html(tickets)), SINGAPORE)
        self.assertEqual(result["status"], "unavailable")

    def test_doubles_pro_mixed_and_bad_partner_metadata_are_rejected(self):
        tickets = [
            target_ticket("HYROX DOUBLES MEN | Friday", remaining=8),
            target_ticket("HYROX MEN PRO | Friday", remaining=8),
            target_ticket("HYROX DOUBLES MIXED | Friday", remaining=8),
            target_ticket("KLOOK | HYROX WOMEN | Friday", remaining=8),
        ]
        with self.assertRaisesRegex(ValueError, "exact Men's Open Singles"):
            AGENT.classify_checkout(AGENT.parse_checkout_page(checkout_html(tickets)), SINGAPORE)

    def test_missing_inventory_fails_instead_of_claiming_no_tickets(self):
        ticket = target_ticket()
        ticket.pop("v")
        with self.assertRaisesRegex(ValueError, "did not return inventory"):
            AGENT.classify_checkout(AGENT.parse_checkout_page(checkout_html([ticket])), SINGAPORE)

    def test_wrong_event_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unexpected event"):
            AGENT.classify_checkout(
                AGENT.parse_checkout_page(
                    checkout_html([target_ticket(remaining=2)], event_name="HYROX Bangkok")
                ),
                SINGAPORE,
            )

    def test_kuala_lumpur_uses_exact_open_men_category_and_inventory(self):
        event = AGENT.parse_checkout_page(
            checkout_html(
                [target_ticket("HYROX MEN | Thursday", remaining=3)],
                event_name=EVENT_NAMES[KUALA_LUMPUR["id"]],
            )
        )
        result = AGENT.classify_checkout(event, KUALA_LUMPUR)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["matched_tickets"][0]["remaining"], 3)

    def test_kuala_lumpur_partner_ticket_is_not_target_availability(self):
        ticket = target_ticket("KLOOK | HYROX MEN | Thursday", remaining=8, active=False)
        event = AGENT.parse_checkout_page(
            checkout_html([ticket], event_name=EVENT_NAMES[KUALA_LUMPUR["id"]])
        )
        with self.assertRaisesRegex(ValueError, "exact Men's Open Singles"):
            AGENT.classify_checkout(event, KUALA_LUMPUR)


class NotificationTests(unittest.TestCase):
    def test_unavailable_tickets_are_silent(self):
        sent = []
        state = AGENT.run(
            now_utc=dt.datetime(2026, 10, 31, 0, 0, tzinfo=dt.timezone.utc),
            fetcher=lambda url: fetched(watch_for_url(url), [target_ticket(remaining=0)]),
            sender=lambda text, preview=False: sent.append((text, preview)),
            state_loader=lambda: {},
            state_saver=lambda _value: None,
        )

        self.assertEqual(state["watches"]["singapore_open_men"]["status"], "unavailable")
        self.assertEqual(state["watches"]["kuala_lumpur_open_men"]["status"], "unavailable")
        self.assertEqual(sent, [])

    def test_failed_checks_are_silent(self):
        sent = []
        state = AGENT.run(
            now_utc=dt.datetime(2026, 10, 31, 0, 0, tzinfo=dt.timezone.utc),
            fetcher=lambda _url: (_ for _ in ()).throw(TimeoutError("checkout timed out")),
            sender=lambda text, preview=False: sent.append((text, preview)),
            state_loader=lambda: {},
            state_saver=lambda _value: None,
        )

        self.assertEqual(state["watches"]["singapore_open_men"]["status"], "check_failed")
        self.assertEqual(state["watches"]["kuala_lumpur_open_men"]["status"], "check_failed")
        self.assertEqual(sent, [])

    def test_new_availability_sends_only_the_available_event(self):
        sent = []
        previous = {
            "watches": {
                "singapore_open_men": {"status": "unavailable"},
                "kuala_lumpur_open_men": {"status": "unavailable"},
            }
        }

        AGENT.run(
            now_utc=dt.datetime(2026, 10, 31, 0, 0, tzinfo=dt.timezone.utc),
            fetcher=lambda url: fetched(
                watch_for_url(url),
                [
                    target_ticket(
                        "HYROX MEN | Thursday",
                        remaining=2 if "malaysia.hyrox.com" in url else 0,
                    )
                ],
            ),
            sender=lambda text, preview=False: sent.append((text, preview)),
            state_loader=lambda: previous,
            state_saver=lambda _value: None,
        )

        self.assertEqual(len(sent), 1)
        self.assertIn("БИЛЕТ ПОЯВИЛСЯ", sent[0][0])
        self.assertIn("Kuala Lumpur", sent[0][0])
        self.assertIn(KUALA_LUMPUR["checkout_url"], sent[0][0])
        self.assertNotIn("Singapore", sent[0][0])
        self.assertFalse(sent[0][1])

    def test_unchanged_available_status_does_not_repeat_alert(self):
        sent = []
        previous = {
            "watches": {
                "singapore_open_men": {"status": "available"},
                "kuala_lumpur_open_men": {"status": "available"},
            }
        }
        AGENT.run(
            now_utc=dt.datetime(2026, 10, 31, 1, 0, tzinfo=dt.timezone.utc),
            fetcher=lambda url: fetched(watch_for_url(url), [target_ticket(remaining=2)]),
            sender=lambda text, preview=False: sent.append((text, preview)),
            state_loader=lambda: previous,
            state_saver=lambda _value: None,
        )
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
