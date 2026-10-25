#!/usr/bin/env python3
"""Build dist/v1/* (the published crawler directory) from source/*.

    python3 scripts/build.py            regenerate dist/v1/
    python3 scripts/build.py --check    exit 1 if dist/v1/ differs from what source/ produces
    python3 scripts/build.py --root DIR use another checkout (tests)

Python 3.11+, standard library only, so the GitHub workflow and Bots Manager
run the same code.

Inputs (all in source/):
  radar-snapshot.json  the last Cloudflare Radar pull (machine-owned)
  curated.json         Coywolf's decisions per slug: token, verification,
                       purpose (+ source), category fix, exclude, aliases...
  extras.json          bots Radar doesn't list
  groups.json          presets: purposes + include/exclude (+ display order)
  release.json         release history, newest first; the first entry gives
                       the published version and generatedAt (never the clock,
                       so --check is reproducible)

Ported from coywolf-pack scripts/build-bots.mjs. Verification, per bot:
  1. A verification pinned in curated.json (status verified, or unverified
     with evidence "none" and a note) is used as is.
  2. A hash-like token is unverified / heuristic, with a note.
  3. The token appears in a user-agent string the snapshot lists for the bot:
     verified / user-agent (dated with the snapshot's fetchedAt).
  4. Anything else: unverified / heuristic.
Bots that share a token are merged into the best-verified entry (mergedSlugs).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

SCHEMA_VERSION = 1
DIST_DIR = Path("dist/v1")
DIRECTORY_FILE = "crawler-directory.json"
MANIFEST_FILE = "manifest.json"
CHANGES_FILE = "changes.json"
MAX_CHANGES = 20

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
HASH_NOTE = "Radar lists a hash-like identifier, not a product token; robots.txt can't reliably target this bot."

# Field order of a published bot (the v1 contract).
BOT_FIELDS = [
    "slug", "name", "operator", "category", "purpose", "description", "token", "aliases",
    "status", "evidence", "sourceUrl", "verifiedAt", "note", "origin", "radarSeenAt",
    "delisted", "mergedSlugs", "previousTokens",
]
GROUP_FIELDS = ["id", "label", "phrase", "description", "purposes", "tokens"]


class BuildError(Exception):
    pass


# ── Helpers (ports of build-bots.mjs) ──────────────────────────────


def is_token(t: str | None) -> bool:
    return bool(t) and TOKEN_RE.fullmatch(t) is not None


def clean_token(t: str | None) -> str:
    t = (t or "").replace("\\", "").strip()
    return re.sub(r"/.*$", "", t, flags=re.S)


def derive_token(patterns: list[str] | None, name: str) -> str | None:
    """Best guess at a robots.txt token from Radar's UA patterns, or the bot name."""
    for p in patterns or []:
        t = clean_token(p)
        if is_token(t) and t.lower() != "user_agent":
            return t
    return name if is_token(name) else None


def hash_like(t: str) -> bool:
    """Hex digests and random-looking strings aren't product tokens."""
    if re.fullmatch(r"[0-9a-f]{12,}", t, re.I):
        return True
    return (
        len(t) >= 16
        and not re.search(r"[._-]", t)
        and len(re.findall(r"\d", t)) >= 3
        and re.search(r"[a-z]", t) is not None
        and re.search(r"[A-Z]", t) is not None
    )


def in_ua(token: str, uas: list[str]) -> bool:
    rx = re.compile(r"(^|[^A-Za-z0-9._-])" + re.escape(token) + r"(?=\Z|[/;\s)(,])", re.I)
    return any(rx.search(ua) for ua in uas)


def clip(s: str | None) -> str:
    t = re.sub(r"\s+", " ", s or "").strip()
    return t[:237].rstrip() + "…" if len(t) > 240 else t


def with_https(u: str | None) -> str | None:
    if not u:
        return None
    return u if re.match(r"https?://", u) else f"https://{u}"


# Approximates ICU root collation at base strength (JavaScript's
# localeCompare(b, "en", { sensitivity: "base" })), which build-bots.mjs used:
# accents and case ignored; whitespace < punctuation < symbols < digits < letters.
_PUNCT_ORDER = " _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$"


def collation_key(s: str) -> tuple:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    key = []
    for c in s:
        if c.isspace():
            key.append((0, 0))
        elif c in _PUNCT_ORDER:
            key.append((1, _PUNCT_ORDER.index(c)))
        elif c.isdigit():
            key.append((3, unicodedata.digit(c, 0)))
        elif c.isalpha():
            key.append((4, ord(c)))
        else:
            key.append((2, ord(c)))
    return tuple(key)


# ── Loading ────────────────────────────────────────────────────────


def load_json(path: Path):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise BuildError(f"missing {path}") from None
    except json.JSONDecodeError as e:
        raise BuildError(f"{path}: invalid JSON ({e})") from None


def load_sources(root: Path) -> dict:
    src = root / "source"
    return {
        "snapshot": load_json(src / "radar-snapshot.json"),
        "curated": load_json(src / "curated.json").get("bots", {}),
        "extras": load_json(src / "extras.json").get("bots", []),
        "groups": load_json(src / "groups.json").get("groups", []),
        "release": load_json(src / "release.json"),
    }


# ── Build ──────────────────────────────────────────────────────────


def verification(slug: str, token: str, record: dict, cur: dict, ua_date: str | None) -> dict:
    pinned = cur.get("verification")
    operator_url = with_https(record.get("operatorUrl"))
    if pinned:
        status = pinned.get("status")
        evidence = pinned.get("evidence")
        if status == "verified" and evidence in ("operator-docs", "user-agent"):
            pass
        elif status == "unverified" and evidence == "none":
            pass
        else:
            raise BuildError(f"curated.json {slug}: unsupported verification {status}/{evidence}")
        v = {"token": token, "status": status, "evidence": evidence}
        source = pinned.get("sourceUrl", operator_url)
        if source:
            v["sourceUrl"] = source
        if pinned.get("verifiedAt"):
            v["verifiedAt"] = pinned["verifiedAt"]
        if pinned.get("note"):
            v["note"] = pinned["note"]
        return v
    source = {"sourceUrl": operator_url} if operator_url else {}
    if hash_like(token):
        return {"token": token, "status": "unverified", "evidence": "heuristic", **source, "note": HASH_NOTE}
    uas = [*(record.get("userAgents") or []), *(record.get("userAgentPatterns") or [])]
    if ua_date and in_ua(token, uas):
        return {"token": token, "status": "verified", "evidence": "user-agent", **source, "verifiedAt": ua_date}
    return {"token": token, "status": "unverified", "evidence": "heuristic", **source}


def build_bots(sources: dict) -> list[dict]:
    snapshot = sources["snapshot"]
    curated: dict = sources["curated"]
    fetched_at = snapshot.get("fetchedAt")
    entries: list[dict] = []

    def common(slug: str, record: dict, cur: dict, v: dict, origin: str) -> dict:
        e = {
            "slug": slug,
            "name": cur.get("name") or record.get("name") or slug,
            "operator": record.get("operator") or "",
            "category": cur.get("category") or record.get("category") or "OTHER",
            "description": clip(record.get("description")),
            **v,
            "origin": origin,
        }
        aliases = [
            a for a in cur.get("aliases", [])
            if a != "user_agent" and a.lower() != v["token"].lower() and not hash_like(a)
        ]
        if aliases:
            e["aliases"] = aliases
        if cur.get("delisted"):
            e["delisted"] = True
        if cur.get("previousTokens"):
            e["previousTokens"] = cur["previousTokens"]
        return e

    for record in snapshot.get("bots", []):
        slug = record["slug"]
        cur = curated.get(slug, {})
        if cur.get("exclude"):
            continue
        token = cur.get("token") or derive_token(record.get("userAgentPatterns"), record.get("name") or "")
        if not is_token(token):
            continue
        v = verification(slug, token, record, cur, record.get("detailsFrom") or fetched_at)
        entries.append(common(slug, record, cur, v, "radar"))

    for record in sources["extras"]:
        slug = record["slug"]
        cur = curated.get(slug, {})
        if cur.get("exclude"):
            continue
        token = cur.get("token") or (record.get("userAgents") or [None])[0] or record.get("name")
        if not is_token(token):
            continue
        if any(e["token"].lower() == token.lower() or e["slug"] == slug for e in entries):
            continue
        v = verification(slug, token, record, cur, record.get("checkedAt"))  # date the UA strings were checked
        entries.append(common(slug, record, cur, v, "curated"))

    # Merge bots that share a token: keep the best-verified entry, remember the others' slugs.
    def rank(e: dict) -> int:
        if e["status"] == "verified":
            return 0 if e["evidence"] == "operator-docs" else 1
        return 2

    by_token: dict[str, list[dict]] = {}
    for e in entries:
        by_token.setdefault(e["token"].lower(), []).append(e)
    merged = []
    for group in by_token.values():
        group.sort(key=lambda e: (rank(e), len(e["slug"]), collation_key(e["slug"])))
        best, *rest = group
        if rest:
            best["mergedSlugs"] = [r["slug"] for r in rest]
        merged.append(best)

    # Purposes come from curated.json (the operator's documentation), never the Radar category.
    for e in merged:
        p = curated.get(e["slug"], {}).get("purpose")
        if p and p.get("purpose"):
            e["purpose"] = p["purpose"]

    merged.sort(key=lambda e: collation_key(e["name"]))  # stable: ties keep insertion order
    return [{k: e[k] for k in BOT_FIELDS if k in e} for e in merged]


def build_groups(groups_src: list[dict], bots: list[dict]) -> list[dict]:
    by_token = {b["token"].lower(): b for b in bots}
    out = []
    for g in groups_src:
        gid = g.get("id")
        exclude = {t.lower() for t in g.get("exclude", [])}
        members = [
            b["token"] for b in bots
            if b["status"] == "verified" and b.get("purpose") in g["purposes"] and b["token"].lower() not in exclude
        ]
        for t in g.get("include", []):
            b = by_token.get(t.lower())
            if not b:
                raise BuildError(f"groups.json {gid}: include {t} is not in the directory")
            if b["token"] not in members:
                members.append(b["token"])
        member_set = {t.lower() for t in members}
        order = g.get("order", [])
        for t in order:
            if t.lower() not in member_set:
                raise BuildError(f"groups.json {gid}: order lists {t}, which isn't a member")
        pos = {t.lower(): i for i, t in enumerate(order)}
        members.sort(key=lambda t: (0, pos[t.lower()]) if t.lower() in pos else (1, collation_key(t)))
        entry = {**g, "tokens": members}
        out.append({k: entry[k] for k in GROUP_FIELDS})
    return out


def release_info(release: dict) -> tuple[dict, list[dict]]:
    releases = release.get("releases") or []
    if not releases:
        raise BuildError("release.json: no releases")
    current = releases[0]
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(\.\d+)?", current.get("version", "")):
        raise BuildError("release.json: version must look like 2026.10.25 or 2026.10.25.2")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", current.get("generatedAt", "")):
        raise BuildError("release.json: generatedAt must be UTC like 2026-10-25T18:00:00Z")
    return current, releases


def _line(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def _pretty(o) -> bytes:
    return (json.dumps(o, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def render_directory(version: str, generated_at: str, bots: list[dict], groups: list[dict]) -> bytes:
    """One bot per line, so diffs between releases stay readable."""
    head = f'{{"schemaVersion":{SCHEMA_VERSION},"version":{_line(version)},"generatedAt":{_line(generated_at)},'
    body = '"bots":[\n' + ",\n".join(_line(b) for b in bots) + '\n],"groups":[\n' + ",\n".join(_line(g) for g in groups) + "\n]}\n"
    return (head + body).encode("utf-8")


def counts(bots: list[dict], groups: list[dict]) -> dict:
    n = lambda f: sum(1 for b in bots if f(b))  # noqa: E731
    return {
        "bots": len(bots),
        "verified": n(lambda b: b["status"] == "verified"),
        "verifiedOperatorDocs": n(lambda b: b["status"] == "verified" and b["evidence"] == "operator-docs"),
        "verifiedUserAgent": n(lambda b: b["status"] == "verified" and b["evidence"] == "user-agent"),
        "unverified": n(lambda b: b["status"] == "unverified"),
        "groups": len(groups),
    }


def render(root: Path) -> dict[str, bytes]:
    """Every dist file, keyed by its path relative to root."""
    sources = load_sources(root)
    current, releases = release_info(sources["release"])
    bots = build_bots(sources)
    groups = build_groups(sources["groups"], bots)
    directory = render_directory(current["version"], current["generatedAt"], bots, groups)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "version": current["version"],
        "generatedAt": current["generatedAt"],
        "file": DIRECTORY_FILE,
        "bytes": len(directory),
        "sha256": hashlib.sha256(directory).hexdigest(),
        "counts": counts(bots, groups),
    }
    changes = {
        "schemaVersion": SCHEMA_VERSION,
        "releases": [
            {k: r[k] for k in ("version", "generatedAt", "summary", "baseline", "added", "removed", "tokenChanges", "groupChanges") if k in r}
            for r in releases[:MAX_CHANGES]
        ],
    }
    d = DIST_DIR.as_posix()
    return {
        f"{d}/{DIRECTORY_FILE}": directory,
        f"{d}/{MANIFEST_FILE}": _pretty(manifest),
        f"{d}/{CHANGES_FILE}": _pretty(changes),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if dist/ differs from what source/ produces")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="repository root")
    args = ap.parse_args(argv)
    root = Path(args.root)
    try:
        files = render(root)
    except BuildError as e:
        print(f"build: {e}", file=sys.stderr)
        return 2
    if args.check:
        stale = []
        for rel, data in files.items():
            path = root / rel
            if not path.exists() or path.read_bytes() != data:
                stale.append(rel)
        if stale:
            print("build --check: dist is out of date with source; run python3 scripts/build.py", file=sys.stderr)
            for rel in stale:
                print(f"  differs: {rel}", file=sys.stderr)
            return 1
        print("build --check: dist matches source")
        return 0
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"build: wrote {', '.join(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
