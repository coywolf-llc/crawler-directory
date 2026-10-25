"""build.py: --check detects drift, output is deterministic, and the ported logic behaves like build-bots.mjs."""

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from helpers import ROOT, build


def run(*args) -> int:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return build.main(list(args))


class BuildCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ("source", "dist", "schema"):
            shutil.copytree(ROOT / d, self.tmp / d)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit_json(self, rel, fn):
        p = self.tmp / rel
        data = json.loads(p.read_text(encoding="utf-8"))
        fn(data)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_clean_checkout_passes(self):
        self.assertEqual(run("--check", "--root", str(self.tmp)), 0)

    def test_source_change_without_rebuild_fails(self):
        self.edit_json("source/curated.json", lambda d: d["bots"]["2checkout"].update(token="TwoCheckout"))
        self.assertEqual(run("--check", "--root", str(self.tmp)), 1)
        self.assertEqual(run("--root", str(self.tmp)), 0)
        self.assertEqual(run("--check", "--root", str(self.tmp)), 0)

    def test_source_change_that_breaks_a_group_is_a_build_error(self):
        # GPTBot is listed in ai_training's order; renaming it without updating groups.json must not pass.
        self.edit_json("source/curated.json", lambda d: d["bots"]["gptbot"].update(token="GPTBot2"))
        self.assertEqual(run("--check", "--root", str(self.tmp)), 2)

    def test_hand_edited_dist_fails(self):
        p = self.tmp / "dist/v1/crawler-directory.json"
        p.write_bytes(p.read_bytes().replace(b'"GPTBot"', b'"GPTBot2"', 1))
        self.assertEqual(run("--check", "--root", str(self.tmp)), 1)

    def test_missing_dist_file_fails(self):
        (self.tmp / "dist/v1/manifest.json").unlink()
        self.assertEqual(run("--check", "--root", str(self.tmp)), 1)

    def test_release_bump_changes_manifest(self):
        self.edit_json("source/release.json", lambda d: d["releases"].insert(0, {**d["releases"][0], "version": "2026.10.26", "generatedAt": "2026-10-26T00:00:00Z", "baseline": False}))
        self.assertEqual(run("--check", "--root", str(self.tmp)), 1)

    def test_deterministic(self):
        self.assertEqual(build.render(self.tmp), build.render(self.tmp))


class PortedLogic(unittest.TestCase):
    """A synthetic source set exercising the rules the seed pins away."""

    def sources(self, snapshot, curated=None, extras=None, groups=None):
        return {
            "snapshot": {"fetchedAt": "2026-10-20", "bots": snapshot},
            "curated": curated or {},
            "extras": extras or [],
            "groups": groups or [],
            "release": {},
        }

    def test_helpers(self):
        self.assertEqual(build.derive_token(["Barkrowler/", "x"], "Babbar"), "Barkrowler")
        self.assertEqual(build.derive_token(["meta-externaltest/1\\.1"], "n"), "meta-externaltest")
        self.assertEqual(build.derive_token(["user_agent", "(bad"], "Plain"), "Plain")
        self.assertIsNone(build.derive_token([], "Has Space"))
        self.assertTrue(build.hash_like("36b1546a5700e52eb2972b3f92b314fa"))
        self.assertTrue(build.hash_like("aB3dE5fG7hJ9kL1mN"))
        self.assertFalse(build.hash_like("Googlebot-Image"))
        self.assertTrue(build.in_ua("Bot", ["Mozilla/5.0 (compatible; Bot/1.0)"]))
        self.assertFalse(build.in_ua("Bot", ["Mozilla/5.0 (compatible; RoBot/1.0)"]))
        self.assertEqual(build.clip("a  b\n c"), "a b c")
        self.assertEqual(len(build.clip("x" * 300)), 238)

    def test_derived_token_ua_verification_and_heuristics(self):
        bots = build.build_bots(self.sources([
            {"slug": "uabot", "name": "UA Bot", "operator": "Op", "category": "OTHER", "description": "d", "operatorUrl": "op.example", "userAgentPatterns": ["UABot/"], "userAgents": ["Mozilla/5.0 (compatible; UABot/2.0)"]},
            {"slug": "guess", "name": "Guess", "category": "OTHER", "userAgentPatterns": ["Guessed\\/"]},
            {"slug": "hash", "name": "h", "userAgentPatterns": ["36b1546a5700e52eb2972b3f92b314fa"]},
            {"slug": "notoken", "name": "No Token", "userAgentPatterns": ["(.*)"]},
        ]))
        by = {b["slug"]: b for b in bots}
        self.assertEqual(by["uabot"]["token"], "UABot")
        self.assertEqual((by["uabot"]["status"], by["uabot"]["evidence"], by["uabot"]["verifiedAt"]), ("verified", "user-agent", "2026-10-20"))
        self.assertEqual(by["uabot"]["sourceUrl"], "https://op.example")
        self.assertEqual((by["guess"]["token"], by["guess"]["evidence"]), ("Guessed", "heuristic"))
        self.assertEqual(by["hash"]["note"], build.HASH_NOTE)
        self.assertNotIn("notoken", by)

    def test_pins_exclude_category_purpose_and_merge(self):
        curated = {
            "a-long-slug": {"token": "Shared", "verification": {"status": "verified", "evidence": "operator-docs", "sourceUrl": "https://docs.example/bot", "verifiedAt": "2026-10-01"}},
            "short": {"token": "shared"},
            "gone": {"exclude": {"reason": "signed agent"}},
            "legacy": {"token": "Legacy", "verification": {"status": "unverified", "evidence": "none", "note": "not documented"}, "category": "AI_CRAWLER"},
            "trainer": {"token": "Trainer", "verification": {"status": "verified", "evidence": "operator-docs", "sourceUrl": "https://t.example/docs", "verifiedAt": "2026-10-01"}, "purpose": {"purpose": "training", "sourceUrl": "https://t.example/docs"}},
        }
        snapshot = [{"slug": s, "name": s.title()} for s in ["a-long-slug", "short", "gone", "legacy"]]
        extras = [{"slug": "trainer", "name": "Trainer", "category": "AI_CRAWLER"}, {"slug": "dupe", "name": "Shared"}]
        bots = build.build_bots(self.sources(snapshot, curated, extras))
        by = {b["slug"]: b for b in bots}
        self.assertEqual(set(by), {"a-long-slug", "legacy", "trainer"})  # short merged, gone excluded, dupe's token taken
        self.assertEqual(by["a-long-slug"]["mergedSlugs"], ["short"])  # better evidence beats a shorter slug
        self.assertEqual(by["legacy"]["category"], "AI_CRAWLER")
        self.assertEqual((by["legacy"]["status"], by["legacy"]["evidence"], by["legacy"]["note"]), ("unverified", "none", "not documented"))
        self.assertEqual((by["trainer"]["origin"], by["trainer"]["purpose"]), ("curated", "training"))
        self.assertEqual([b["slug"] for b in bots], ["a-long-slug", "legacy", "trainer"])  # sorted by name

    def test_groups_derive_include_exclude_order(self):
        bots = [
            {"token": t, "status": s, "purpose": p}
            for t, s, p in [("Zed", "verified", "seo"), ("alpha", "verified", "seo"), ("Beta", "verified", "seo"), ("Unv", "unverified", "seo"), ("Other", "verified", "training"), ("Out", "verified", "seo")]
        ]
        g = {"id": "seo_tools", "label": "SEO", "phrase": "seo", "description": "", "purposes": ["seo"], "include": ["Other"], "exclude": ["out"], "order": ["Zed"]}
        [out] = build.build_groups([g], bots)
        self.assertEqual(out["tokens"], ["Zed", "alpha", "Beta", "Other"])
        with self.assertRaises(build.BuildError):
            build.build_groups([{**g, "order": ["Unv"]}], bots)
        with self.assertRaises(build.BuildError):
            build.build_groups([{**g, "include": ["Missing"]}], bots)


if __name__ == "__main__":
    unittest.main()
