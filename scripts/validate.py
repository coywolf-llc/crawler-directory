#!/usr/bin/env python3
"""Validate the published crawler directory before it's pushed.

    python3 scripts/validate.py            check dist/v1/* (and source/* rules that need it)
    python3 scripts/validate.py --root DIR another checkout (tests)

Exits 1 when any check fails; warnings are printed but don't fail.
Python 3.11+, standard library only.

Checks (plan Part 2, "Checks before every push", the data ones):
  - crawler-directory.json matches schema/v1.json
  - slugs are unique (merged slugs included); tokens are unique, case-insensitive
  - tokens use only letters, digits, ".", "_" and "-", and are never "*"
  - every verified entry has an https sourceUrl and a verifiedAt date
  - every purpose has a source (source/curated.json)
  - group tokens exist and are verified; nothing is both excluded and in a group
  - manifest.json matches the file (bytes, sha256, version, counts); size limit
  - no control characters (U+0000-U+001F, U+007F) in any string
  - dist/v1/manifest.json.sig is a valid Ed25519 signature of manifest.json by the key in keys/ed25519.json
    (skipped with a warning only while neither the key nor a signature exists, before the first signed release)
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024  # the pack refuses larger files
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DIST = Path("dist/v1")
KEY_FILE = Path("keys/ed25519.json")
SIGNATURE = DIST / "manifest.json.sig"
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _ed25519():
    """scripts/ed25519.py (pure Python, stdlib only), loaded from next to this file."""
    path = Path(__file__).resolve().parent / "ed25519.py"
    spec = importlib.util.spec_from_file_location("crawler_directory_ed25519", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── A small JSON Schema (draft 2020-12) subset ─────────────────────

_ANNOTATIONS = {"$schema", "$id", "$comment", "title", "description", "format", "$defs", "default", "examples"}
_SUPPORTED = _ANNOTATIONS | {
    "$ref", "type", "const", "enum", "required", "properties", "additionalProperties", "items",
    "minItems", "maxItems", "minLength", "maxLength", "pattern", "minimum", "maximum",
}
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def schema_errors(value, schema: dict, root: dict | None = None, path: str = "$", out: list | None = None) -> list[str]:
    """Validate value against the subset of JSON Schema used by schema/v1.json."""
    root = root if root is not None else schema
    out = out if out is not None else []
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise ValueError(f"schema at {path} uses unsupported keywords: {sorted(unknown)}")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported: {ref}")
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        schema_errors(value, target, root, path, out)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            out.append(f"{path}: expected {'/'.join(types)}")
            return out
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: must be {json.dumps(schema['const'])}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {json.dumps(value)} is not one of {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: {json.dumps(value)} doesn't match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                schema_errors(item, schema["items"], root, f"{path}[{i}]", out)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path}: missing {key}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, v in value.items():
            if key in props:
                schema_errors(v, props[key], root, f"{path}.{key}", out)
            elif extra is False:
                out.append(f"{path}: unexpected field {key}")
            elif isinstance(extra, dict):
                schema_errors(v, extra, root, f"{path}.{key}", out)
    return out


# ── Data checks ────────────────────────────────────────────────────


def control_char_errors(value, path: str = "$", out: list | None = None) -> list[str]:
    """Every string (and object key) must be free of control characters, newlines and tabs included."""
    out = out if out is not None else []
    if isinstance(value, str):
        if CONTROL_RE.search(value):
            out.append(f"{path}: contains a control character ({json.dumps(value)[:80]})")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            control_char_errors(v, f"{path}[{i}]", out)
    elif isinstance(value, dict):
        for k, v in value.items():
            if CONTROL_RE.search(str(k)):
                out.append(f"{path}: key {json.dumps(k)} contains a control character")
            control_char_errors(v, f"{path}.{k}", out)
    return out


def check_directory(directory: dict, schema: dict, curated: dict | None = None, groups_src: list | None = None,
                    exceptions: dict | None = None) -> tuple[list[str], list[str]]:
    """Errors and warnings for a parsed crawler-directory.json (plus source/* when given)."""
    errors = schema_errors(directory, schema) + control_char_errors(directory)
    warnings: list[str] = []
    if errors:
        return errors, warnings  # the semantic checks below assume the shape
    bots = directory["bots"]
    exceptions = exceptions or {}
    https_ok = exceptions.get("verifiedWithoutHttpsSource", {})

    # Unique slugs (merged slugs included) and tokens.
    seen_slugs: dict[str, str] = {}
    for b in bots:
        for s in [b["slug"], *b.get("mergedSlugs", [])]:
            if s in seen_slugs:
                errors.append(f"duplicate slug {s} ({seen_slugs[s]} and {b['slug']})")
            seen_slugs.setdefault(s, b["slug"])
    seen_tokens: dict[str, str] = {}
    for b in bots:
        t = b["token"]
        key = t.lower()
        if key in seen_tokens:
            errors.append(f"duplicate token {t} ({seen_tokens[key]} and {b['slug']}); merge them (mergedSlugs) instead")
        seen_tokens.setdefault(key, b["slug"])

    # Safe tokens.
    for b in bots:
        for t in [b["token"], *(p["token"] for p in b.get("previousTokens", []))]:
            if t == "*" or not TOKEN_RE.fullmatch(t):
                errors.append(f"{b['slug']}: unsafe token {json.dumps(t)}")

    # Verification records.
    for b in bots:
        if b["status"] == "verified":
            if b["evidence"] not in ("operator-docs", "user-agent"):
                errors.append(f"{b['slug']}: verified with evidence {b['evidence']}")
            problems = []
            if not str(b.get("sourceUrl", "")).startswith("https://"):
                problems.append("an https sourceUrl")
            if not b.get("verifiedAt"):
                problems.append("verifiedAt")
            if problems:
                msg = f"{b['slug']}: verified without {' and '.join(problems)}"
                if b["slug"] in https_ok and problems == ["an https sourceUrl"]:
                    warnings.append(f"{msg} (known exception: {https_ok[b['slug']]})")
                else:
                    errors.append(msg)
        elif b["evidence"] in ("operator-docs", "user-agent"):
            errors.append(f"{b['slug']}: unverified with evidence {b['evidence']}")
    for b in bots:
        if re.search(r"\s", b.get("sourceUrl", "")):
            warnings.append(f"{b['slug']}: sourceUrl isn't a single URL: {json.dumps(b['sourceUrl'])}")
    for slug in https_ok:
        b = next((x for x in bots if x["slug"] == slug), None)
        if b and str(b.get("sourceUrl", "")).startswith("https://"):
            warnings.append(f"{slug}: listed in exceptions.json but now has an https source; remove the exception")

    # Purposes need a source.
    if curated is not None:
        for slug, c in curated.items():
            p = c.get("purpose")
            if p and not re.match(r"https?://\S+$", str(p.get("sourceUrl", ""))):
                errors.append(f"curated.json {slug}: purpose {p.get('purpose')} has no source URL")
        for b in bots:
            if b.get("purpose") and not (curated.get(b["slug"], {}).get("purpose") or {}).get("sourceUrl"):
                errors.append(f"{b['slug']}: purpose {b['purpose']} has no source in curated.json")

    # Groups.
    by_token = {b["token"].lower(): b for b in bots}
    ids = set()
    excluded_tokens: dict[str, str] = {}
    if curated is not None:
        for slug, c in curated.items():
            if c.get("exclude"):
                if slug in seen_slugs:
                    errors.append(f"{slug}: excluded in curated.json but published")
                if c.get("token"):
                    excluded_tokens[c["token"].lower()] = slug
    src_by_id = {g.get("id"): g for g in (groups_src or [])}
    for g in directory["groups"]:
        if g["id"] in ids:
            errors.append(f"group {g['id']}: duplicate id")
        ids.add(g["id"])
        lowered = [t.lower() for t in g["tokens"]]
        if len(set(lowered)) != len(lowered):
            errors.append(f"group {g['id']}: duplicate token")
        group_excludes = {t.lower() for t in src_by_id.get(g["id"], {}).get("exclude", [])}
        for t in g["tokens"]:
            b = by_token.get(t.lower())
            if not b:
                errors.append(f"group {g['id']}: {t} is not in the directory")
            elif b["status"] != "verified":
                errors.append(f"group {g['id']}: {t} is not verified")
            elif b["token"] != t:
                errors.append(f"group {g['id']}: {t} is spelled {b['token']} in the directory")
            if t.lower() in excluded_tokens:
                errors.append(f"group {g['id']}: {t} is excluded ({excluded_tokens[t.lower()]})")
            if t.lower() in group_excludes:
                errors.append(f"group {g['id']}: {t} is both excluded and a member")
    return errors, warnings


def check_manifest(manifest: dict, raw: bytes, directory: dict) -> list[str]:
    errors = []
    expect = {
        "schemaVersion": directory.get("schemaVersion"),
        "version": directory.get("version"),
        "generatedAt": directory.get("generatedAt"),
        "file": "crawler-directory.json",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    for k, v in expect.items():
        if manifest.get(k) != v:
            errors.append(f"manifest.json: {k} is {json.dumps(manifest.get(k))}, expected {json.dumps(v)}")
    counts = manifest.get("counts")
    if not isinstance(counts, dict) or counts.get("bots") != len(directory.get("bots", [])) or counts.get("groups") != len(directory.get("groups", [])):
        errors.append("manifest.json: counts don't match the file")
    if len(raw) > MAX_BYTES:
        errors.append(f"crawler-directory.json is {len(raw)} bytes; the limit is {MAX_BYTES}")
    return errors


def check_signature(root: Path) -> tuple[list[str], list[str]]:
    """The manifest signature, against the public key committed in keys/ed25519.json."""
    key_path, sig_path, manifest_path = root / KEY_FILE, root / SIGNATURE, root / DIST / "manifest.json"
    if not key_path.exists() and not sig_path.exists():
        return [], ["manifest signature not checked: no keys/ed25519.json and no dist/v1/manifest.json.sig yet "
                    "(expected only before the first signed release)"]
    if not key_path.exists():
        return ["dist/v1/manifest.json.sig exists but keys/ed25519.json doesn't; can't verify it"], []
    try:
        key = json.loads(key_path.read_text(encoding="utf-8"))
        public = base64.b64decode(key["publicKey"], validate=True)
    except (ValueError, KeyError, TypeError, binascii.Error) as e:
        return [f"keys/ed25519.json: unreadable ({e})"], []
    errors = []
    if key.get("algorithm") != "Ed25519" or len(public) != 32:
        errors.append("keys/ed25519.json: expected algorithm Ed25519 and a 32-byte publicKey")
    if key.get("keyId") != hashlib.sha256(public).hexdigest()[:16]:
        errors.append("keys/ed25519.json: keyId isn't the first 16 hex characters of SHA-256(publicKey)")
    if not sig_path.exists():
        return errors + ["dist/v1/manifest.json.sig is missing (keys/ed25519.json exists, so releases must be signed)"], []
    try:
        signature = base64.b64decode(sig_path.read_text(encoding="ascii").strip(), validate=True)
    except (ValueError, binascii.Error):
        return errors + ["dist/v1/manifest.json.sig isn't base64"], []
    if not manifest_path.exists() or not _ed25519().verify(public, manifest_path.read_bytes(), signature):
        errors.append("dist/v1/manifest.json.sig doesn't verify against manifest.json with the key in keys/ed25519.json")
    return errors, []


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def validate(root: Path) -> tuple[list[str], list[str]]:
    schema = _load(root / "schema/v1.json")
    raw_path = root / DIST / "crawler-directory.json"
    if schema is None or not raw_path.exists():
        return ["missing schema/v1.json or dist/v1/crawler-directory.json"], []
    raw = raw_path.read_bytes()
    try:
        directory = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return [f"crawler-directory.json: invalid JSON ({e})"], []
    curated = (_load(root / "source/curated.json") or {}).get("bots")
    groups_src = (_load(root / "source/groups.json") or {}).get("groups")
    exceptions = _load(root / "source/exceptions.json") or {}
    errors, warnings = check_directory(directory, schema, curated, groups_src, exceptions)
    manifest = _load(root / DIST / "manifest.json")
    if manifest is None:
        errors.append("missing dist/v1/manifest.json")
    else:
        errors += check_manifest(manifest, raw, directory)
    changes = _load(root / DIST / "changes.json")
    if not isinstance(changes, dict) or not isinstance(changes.get("releases"), list) or len(changes["releases"]) > 20:
        errors.append("dist/v1/changes.json: expected { releases: [...] } with at most 20 entries")
    elif changes["releases"] and changes["releases"][0].get("version") != directory.get("version"):
        errors.append("dist/v1/changes.json: newest release isn't the published version")
    else:
        errors += control_char_errors(changes, "changes.json")
    sig_errors, sig_warnings = check_signature(root)
    return errors + sig_errors, warnings + sig_warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="repository root")
    ap.add_argument("--quiet", action="store_true", help="don't list warnings")
    args = ap.parse_args(argv)
    errors, warnings = validate(Path(args.root))
    if warnings and not args.quiet:
        for w in warnings:
            print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if errors:
        print(f"validate: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(f"validate: ok ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
