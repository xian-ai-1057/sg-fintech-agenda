import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scrape_sff_agenda import (
    FIELDS, FESTIVAL_DAYS, carry_over_zh, description_text, main, normalize_time_range,
    parse_modern_card, scrape_listing, session_key, write_csv,
)


class Element:
    def __init__(self, text="", **attributes):
        self.text = ""  # 關閉的 details / 視覺隱藏文字不能靠 .text 讀。
        self.attributes = {"textContent": text, **attributes}

    def get_attribute(self, key):
        return self.attributes.get(key)


class Card(Element):
    def __init__(self, stamp="1794931200000", time="12:30–4:30pm"):
        super().__init__(**{"data-day": stamp})
        self.items = {
            ".sff26-aglist__rha": [Element("Session", href="/agenda?session=AGND441-new-title")],
            ".sff26-aglist__rw b": [Element(time)],
            ".sff26-aglist__rws": [Element("Festival Stage")],
            ".sff26-aglist__rwh": [Element("Hall 1, Singapore EXPO")],
            ".sff26-aglist__rwp": [Element("Premium")],
            ".sff26-aglist__rt": [Element("Technology Rewiring")],
            ".sff26-aglist__rpn b": [Element("Alice"), Element("Alice"), Element("Bob")],
            ".sff26-aglist__syn-b": [Element(innerHTML="<p>First.<br>Second.</p>")],
        }

    def find_element(self, by, css):
        return self.items[css][0]

    def find_elements(self, by, css):
        return self.items.get(css, [])


class ScraperTests(unittest.TestCase):
    def test_description_preserves_breaks_and_entities(self):
        self.assertEqual(description_text("<p>AI &amp; finance.<br>Trust <b>matters</b>.</p><ul><li>One.</li><li>Two.</li></ul>"), "AI & finance.\n\nTrust matters.\n\nOne.\n\nTwo.")

    def test_compact_time_ranges(self):
        cases = {
            "9:00–9:15am": "9:00 AM - 9:15 AM",
            "12:30–4:30pm": "12:30 PM - 4:30 PM",
            "12:55–1:00pm": "12:55 PM - 1:00 PM",
            "11:35am–12:10pm": "11:35 AM - 12:10 PM",
            "11:30–1:00pm": "11:30 AM - 1:00 PM",
            "11:30am–1:00": "11:30 AM - 1:00 PM",
            "9:00 AM - 10:30 AM": "9:00 AM - 10:30 AM",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_time_range(source), expected)

    def test_invalid_times_fail_closed(self):
        for value in ["TBC", "9:00–10:00", "13:00–2:00pm", "9:60–10:00am", "4:00pm–3:00pm", "11:00pm–1:00am"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_time_range(value)

    def test_modern_card_reads_hidden_fields(self):
        row = parse_modern_card(Card())
        self.assertEqual(row["date"], "2026-11-18")
        self.assertEqual(row["datetime"], "Wed, 18 Nov | 12:30 PM - 4:30 PM")
        self.assertEqual(row["description"], "First.\n\nSecond.")
        self.assertEqual(row["speakers"], "Alice; Bob")
        self.assertEqual(row["url"], "https://www.fintechfestival.sg/agenda?session=AGND441-new-title")

    def test_unknown_day_fails_closed(self):
        with self.assertRaises(ValueError):
            parse_modern_card(Card(stamp="0"))

    def test_missing_or_external_link_fails_closed(self):
        for href in [None, "", "https://example.com/agenda?session=AGND441-title", "/agenda"]:
            card = Card()
            card.items[".sff26-aglist__rha"][0].attributes["href"] = href
            with self.subTest(href=href), self.assertRaises(ValueError):
                parse_modern_card(card)

    def test_failed_atomic_replace_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agenda.csv"
            path.write_bytes(b"original")
            with patch("scrape_sff_agenda.os.replace", side_effect=OSError("write failed")):
                with self.assertRaises(OSError):
                    write_csv(str(path), [{"title": "中文"}])
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_session_identity_survives_title_changes(self):
        self.assertEqual(session_key("https://www.fintechfestival.sg/agenda?session=AGND441-new-title"), "AGND441")

    def test_translations_require_same_english(self):
        previous = [
            {"url": "https://example.org/agenda?session=AGND441-old-title", "description": "Same.\n\nText.", "description_zh": "相同的內容。"},
            {"url": "https://example.org/agenda?session=AGND442-old-title", "description": "Old.", "description_zh": "過時內容。"},
            {"url": "https://example.org/agenda?session=AGND443-old-title", "description": "Removed.", "description_zh": "已撤下。"},
        ]
        fresh = [
            {"url": "https://example.org/agenda?session=AGND441-new-title", "description": "Same. Text."},
            {"url": "https://example.org/agenda?session=AGND442-old-title", "description": "New."},
            {"url": "https://example.org/agenda?session=AGND443-old-title", "description": ""},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agenda.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(previous)
            self.assertEqual(carry_over_zh(str(path), fresh), 1)
        self.assertEqual(fresh[0]["description_zh"], "相同的內容。")
        self.assertNotIn("description_zh", fresh[1])
        self.assertNotIn("description_zh", fresh[2])

    def test_partial_run_cannot_overwrite_agenda(self):
        with patch("sys.argv", ["scraper", "--max-sessions", "5"]):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)

    def test_count_mismatch_preserves_existing_data(self):
        class Driver:
            def get(self, url):
                pass
            def find_element(self, by, css):
                return Element("124")
            def find_elements(self, by, css):
                return [Card()]
        with self.assertRaisesRegex(ValueError, "Incomplete listing"):
            scrape_listing(Driver(), FESTIVAL_DAYS)


if __name__ == "__main__":
    unittest.main()
