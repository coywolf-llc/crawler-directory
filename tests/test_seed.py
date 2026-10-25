"""The seed release reproduces Coywolf Pack's bundled list and presets exactly."""

import json
import unittest

from helpers import FIXTURES, ROOT, build, read_json

GROUP_FIELDS = ["id", "label", "phrase", "description", "purposes", "tokens"]


class SeedEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = build.render(ROOT)
        cls.directory = json.loads(files["dist/v1/crawler-directory.json"])
        cls.expected_bots = read_json(FIXTURES / "bots-2026-10-04.json")["bots"]
        cls.expected_groups = read_json(FIXTURES / "crawler-groups-2026-10-25.json")["groups"]

    def test_same_bots_in_the_same_order(self):
        self.assertEqual([b["slug"] for b in self.directory["bots"]], [b["slug"] for b in self.expected_bots])

    def test_every_bot_field_for_field(self):
        expected = {b["slug"]: b for b in self.expected_bots}
        for bot in self.directory["bots"]:
            with self.subTest(slug=bot["slug"]):
                self.assertEqual(bot, expected[bot["slug"]])

    def test_counts(self):
        bots = self.directory["bots"]
        self.assertEqual(len(bots), 694)
        self.assertEqual(sum(b["status"] == "verified" for b in bots), 562)
        self.assertEqual(sum(len(b.get("mergedSlugs", [])) for b in bots), 30)

    def test_groups_match_crawler_groups_exactly(self):
        expected = [{k: g[k] for k in GROUP_FIELDS} for g in self.expected_groups]
        self.assertEqual(self.directory["groups"], expected)

    def test_group_tokens_are_verified_bots_with_a_matching_purpose(self):
        by_token = {b["token"]: b for b in self.directory["bots"]}
        for g in self.directory["groups"]:
            for t in g["tokens"]:
                self.assertEqual(by_token[t]["status"], "verified", t)
                self.assertIn(by_token[t]["purpose"], g["purposes"], t)

    def test_header(self):
        d = self.directory
        self.assertEqual(d["schemaVersion"], 1)
        self.assertRegex(d["version"], r"^\d{4}\.\d{2}\.\d{2}$")
        self.assertRegex(d["generatedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_committed_dist_validates(self):
        from helpers import validate

        errors, _ = validate.validate(ROOT)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
