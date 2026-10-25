"""validate.py rejects bad data."""

import copy
import hashlib
import json
import unittest

from helpers import ROOT, read_json, validate


class ValidateRejects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = read_json(ROOT / "schema/v1.json")
        cls.good = read_json(ROOT / "dist/v1/crawler-directory.json")
        cls.curated = read_json(ROOT / "source/curated.json")["bots"]
        cls.groups_src = read_json(ROOT / "source/groups.json")["groups"]
        cls.exceptions = read_json(ROOT / "source/exceptions.json")

    def check(self, directory, curated=None):
        return validate.check_directory(directory, self.schema, curated if curated is not None else self.curated, self.groups_src, self.exceptions)

    def bot(self, d, slug):
        return next(b for b in d["bots"] if b["slug"] == slug)

    def assertRejected(self, directory, needle, curated=None):
        errors, _ = self.check(directory, curated)
        self.assertTrue(errors, "expected errors")
        self.assertTrue(any(needle in e for e in errors), f"no error mentions {needle!r}: {errors[:5]}")

    def test_good_data_passes(self):
        errors, warnings = self.check(copy.deepcopy(self.good))
        self.assertEqual(errors, [])
        self.assertTrue(warnings)  # the seed's known exceptions

    def test_duplicate_token_case_insensitive(self):
        d = copy.deepcopy(self.good)
        self.bot(d, "2checkout")["token"] = "gptbot"
        self.assertRejected(d, "duplicate token")

    def test_duplicate_slug(self):
        d = copy.deepcopy(self.good)
        d["bots"].append({**self.bot(d, "2checkout"), "token": "Another-Token"})
        self.assertRejected(d, "duplicate slug 2checkout")

    def test_merged_slug_also_published(self):
        d = copy.deepcopy(self.good)
        self.bot(d, "2checkout")["mergedSlugs"] = ["gptbot"]
        self.assertRejected(d, "duplicate slug gptbot")

    def test_bad_token_charset(self):
        for bad in ["Bad Bot", "bot/1.0", "-bot", "bot#", "Bot\n", "Bøt"]:
            with self.subTest(token=bad):
                d = copy.deepcopy(self.good)
                self.bot(d, "2checkout")["token"] = bad
                self.assertRejected(d, "token")

    def test_star_token(self):
        d = copy.deepcopy(self.good)
        self.bot(d, "2checkout")["token"] = "*"
        self.assertRejected(d, "token")

    def test_unsafe_token_without_schema(self):
        # The semantic check also catches "*" on its own (defense in depth).
        d = copy.deepcopy(self.good)
        self.bot(d, "2checkout")["previousTokens"] = [{"token": "*", "until": "2026-10-01"}]
        errors, _ = validate.check_directory(d, {}, self.curated, self.groups_src, self.exceptions)
        self.assertTrue(any("unsafe token" in e for e in errors), errors)

    def test_verified_without_source_url(self):
        d = copy.deepcopy(self.good)
        del self.bot(d, "gptbot")["sourceUrl"]
        self.assertRejected(d, "gptbot: verified without an https sourceUrl")

    def test_verified_with_http_source_url(self):
        d = copy.deepcopy(self.good)
        self.bot(d, "gptbot")["sourceUrl"] = "http://developers.openai.com/api/docs/bots"
        self.assertRejected(d, "gptbot: verified without an https sourceUrl")

    def test_verified_without_date(self):
        d = copy.deepcopy(self.good)
        del self.bot(d, "gptbot")["verifiedAt"]
        self.assertRejected(d, "verifiedAt")

    def test_known_exception_is_only_a_warning(self):
        errors, warnings = self.check(copy.deepcopy(self.good))
        self.assertFalse(any(e.startswith("ahrefsbot") for e in errors))
        self.assertTrue(any(w.startswith("ahrefsbot") for w in warnings))

    def test_group_token_unverified(self):
        d = copy.deepcopy(self.good)
        d["groups"][1]["tokens"].append("anthropic-ai")
        self.assertRejected(d, "anthropic-ai is not verified")

    def test_group_token_missing(self):
        d = copy.deepcopy(self.good)
        d["groups"][0]["tokens"].append("NoSuchBot")
        self.assertRejected(d, "NoSuchBot is not in the directory")

    def test_group_member_excluded(self):
        d = copy.deepcopy(self.good)
        d["groups"][0]["tokens"].append("Timpibot")  # excluded from search_engines in groups.json
        self.assertRejected(d, "Timpibot is both excluded and a member")

    def test_excluded_bot_published(self):
        curated = copy.deepcopy(self.curated)
        curated["gptbot"]["exclude"] = {"reason": "test"}
        self.assertRejected(copy.deepcopy(self.good), "excluded in curated.json but published", curated)

    def test_purpose_without_source(self):
        curated = copy.deepcopy(self.curated)
        del curated["gptbot"]["purpose"]["sourceUrl"]
        self.assertRejected(copy.deepcopy(self.good), "purpose training has no source", curated)

    def test_schema_shape(self):
        d = copy.deepcopy(self.good)
        d["schemaVersion"] = 2
        self.assertRejected(d, "schemaVersion")
        d = copy.deepcopy(self.good)
        self.bot(d, "gptbot")["status"] = "trusted"
        self.assertRejected(d, "status")
        d = copy.deepcopy(self.good)
        self.bot(d, "gptbot")["surprise"] = True
        self.assertRejected(d, "unexpected field surprise")

    def test_manifest_mismatch(self):
        raw = (ROOT / "dist/v1/crawler-directory.json").read_bytes()
        manifest = read_json(ROOT / "dist/v1/manifest.json")
        self.assertEqual(validate.check_manifest(manifest, raw, self.good), [])
        tampered = raw.replace(b'"GPTBot"', b'"GPTBot2"', 1)
        errors = validate.check_manifest(manifest, tampered, json.loads(tampered))
        self.assertTrue(any("sha256" in e for e in errors) and any("bytes" in e for e in errors), errors)
        self.assertNotEqual(hashlib.sha256(tampered).hexdigest(), manifest["sha256"])


if __name__ == "__main__":
    unittest.main()
