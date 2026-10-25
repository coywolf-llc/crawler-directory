"""build.py: curated.json overrides written by Bots Manager (operator, description, note, delisted bots, review records)."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, build


class Curation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ("source", "dist", "schema"):
            shutil.copytree(ROOT / d, self.tmp / d)
        self.sources = build.load_sources(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def bots(self):
        return {b["slug"]: b for b in build.build_bots(self.sources)}

    def test_operator_description_and_note_overrides(self):
        self.sources["curated"]["2checkout"].update(
            operator="Verifone, Inc.", description="  Payment   webhooks. ", note="Checked by hand."
        )
        b = self.bots()["2checkout"]
        self.assertEqual(b["operator"], "Verifone, Inc.")
        self.assertEqual(b["description"], "Payment webhooks.")
        self.assertEqual(b["note"], "Checked by hand.")

    def test_empty_operator_override_is_kept(self):
        self.sources["curated"]["2checkout"]["operator"] = ""
        self.assertEqual(self.bots()["2checkout"]["operator"], "")

    def test_review_records_are_ignored(self):
        before = self.bots()["2checkout"]
        self.sources["curated"]["2checkout"]["review"] = [{"kind": "new", "decision": "accept", "date": "2026-10-25"}]
        self.assertEqual(self.bots()["2checkout"], before)

    def test_delisted_bot_with_radar_record_stays_published(self):
        snap = self.sources["snapshot"]
        record = next(r for r in snap["bots"] if r["slug"] == "2checkout")
        snap["bots"] = [r for r in snap["bots"] if r["slug"] != "2checkout"]
        self.assertNotIn("2checkout", self.bots())
        self.sources["curated"]["2checkout"].update(delisted=True, radarRecord={k: v for k, v in record.items() if k != "slug"})
        b = self.bots()["2checkout"]
        self.assertTrue(b["delisted"])
        self.assertEqual(b["origin"], "radar")
        self.assertEqual(b["operator"], "Verifone")

    def test_delisted_without_record_drops_out(self):
        snap = self.sources["snapshot"]
        snap["bots"] = [r for r in snap["bots"] if r["slug"] != "2checkout"]
        self.sources["curated"]["2checkout"]["delisted"] = True
        self.assertNotIn("2checkout", self.bots())

    def test_changes_json_carries_delisted(self):
        p = self.tmp / "source/release.json"
        rel = json.loads(p.read_text(encoding="utf-8"))
        rel["releases"].insert(0, {"version": "2026.10.26", "generatedAt": "2026-10-26T00:00:00Z", "summary": "x",
                                   "added": [], "removed": [], "delisted": [{"slug": "a", "token": "A"}],
                                   "tokenChanges": [], "groupChanges": []})
        p.write_text(json.dumps(rel, indent=2) + "\n", encoding="utf-8")
        files = build.render(self.tmp)
        changes = json.loads(files["dist/v1/changes.json"])
        self.assertEqual(changes["releases"][0]["delisted"], [{"slug": "a", "token": "A"}])


if __name__ == "__main__":
    unittest.main()


class ControlCharacters(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ("source", "dist", "schema"):
            shutil.copytree(ROOT / d, self.tmp / d)
        self.sources = build.load_sources(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_build_replaces_control_characters(self):
        self.sources["curated"]["2checkout"].update(
            name="Two\nCheckout", operator="Veri\tfone\x00", description="Line one\r\nline two\x7f",
            note="a\x1bb", aliases=["two\ncheckout"])
        b = {x["slug"]: x for x in build.build_bots(self.sources)}["2checkout"]
        self.assertEqual((b["name"], b["operator"], b["description"], b["note"], b["aliases"]),
                         ("Two Checkout", "Veri fone ", "Line one line two", "a b", ["two checkout"]))
        groups = self.sources["groups"]
        groups[0]["label"] = "Search\nengines"
        self.assertEqual(build.build_groups(groups, list({x["slug"]: x for x in build.build_bots(self.sources)}.values()))[0]["label"],
                         "Search engines")

    def test_relisted_bot_is_no_longer_delisted(self):
        record = next(r for r in self.sources["snapshot"]["bots"] if r["slug"] == "2checkout")
        self.sources["curated"]["2checkout"].update(delisted=True, radarRecord={k: v for k, v in record.items() if k != "slug"})
        b = {x["slug"]: x for x in build.build_bots(self.sources)}["2checkout"]
        self.assertNotIn("delisted", b)  # it's in the snapshot again
