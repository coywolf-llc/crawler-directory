# Crawler Directory

A list of web crawlers and the robots.txt token each one obeys, with a record of how every token was verified, plus presets (groups) such as "AI training" and "Search engines".

A robots.txt `User-agent:` line takes a product token (`GPTBot`, `Claude-SearchBot`, `meta-externalagent`), which often isn't the bot's name or its full user-agent string. This directory maps bots to their tokens and says how much to trust each one.

The list is curated by [Coywolf](https://coywolf.com) for the Robots.txt Rules module of Coywolf Pack for EmDash. Sites running the pack check it weekly. Anyone may read and use it under the license below.

> Contains data from [Cloudflare Radar](https://radar.cloudflare.com), licensed [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Coywolf has modified it (token verification, purposes, presets, corrections).

## Contributions

This repository doesn't accept outside contributions. Issues and pull requests are turned off, and changes come only from Coywolf's own review process.

## How tokens are verified

Every bot carries a `status` and the `evidence` behind it:

| status | evidence | Meaning |
| --- | --- | --- |
| `verified` | `operator-docs` | The operator's own documentation names the token. `sourceUrl` is that page and `verifiedAt` is the date it was checked. |
| `verified` | `user-agent` | The token appears in the user-agent string Cloudflare Radar publishes for the bot. |
| `unverified` | `heuristic` | Derived from a user-agent pattern or the bot's name, or a hash-like identifier that robots.txt can't reliably target (with a `note`). |
| `unverified` | `none` | Checked, and the operator doesn't document this token (for example legacy tokens such as `anthropic-ai` or `Claude-Web`), with a `note`. |

Other rules:

- Bots that share a token are merged into the best-verified entry, and the others' slugs are listed in `mergedSlugs`.
- Signed agents that identify only cryptographically (Web Bot Auth) and have no token, such as ChatGPT agent, are left out.
- `purpose` (what a bot is for: `training`, `ai-search`, `ai-assistant`, `search-engine`, `seo`, `other`) comes from the operator's documentation, never from the Radar category.
- **Groups (presets)** are derived from data: every bot with a verified token and one of the group's purposes, plus explicit includes, minus explicit excludes. A bot that only comes from Radar never joins a group.

## Using the list

The published files, on the `main` branch:

- Manifest: `https://raw.githubusercontent.com/coywolf-llc/crawler-directory/main/dist/v1/manifest.json`
- List: `https://raw.githubusercontent.com/coywolf-llc/crawler-directory/main/dist/v1/crawler-directory.json`
- Recent changes: `https://raw.githubusercontent.com/coywolf-llc/crawler-directory/main/dist/v1/changes.json` (the last 20 releases)

The manifest looks like this:

```json
{
  "schemaVersion": 1,
  "version": "2026.10.25",
  "generatedAt": "2026-10-25T00:00:00Z",
  "file": "crawler-directory.json",
  "bytes": 273593,
  "sha256": "…",
  "counts": { "bots": 694, "verified": 562, "verifiedOperatorDocs": 143, "verifiedUserAgent": 419, "unverified": 132, "groups": 4 }
}
```

To consume it safely:

1. Fetch the manifest. Refuse it unless `schemaVersion` is one you support (this is format version 1, under `/v1/`; a breaking change gets a new path).
2. Skip the download when `version` matches the copy you already have.
3. Fetch the list. Refuse it if it's larger than you expect (Coywolf Pack refuses anything over 2 MiB), if its size doesn't equal `bytes`, or if its SHA-256 doesn't equal `sha256`.
4. Validate it against [`schema/v1.json`](schema/v1.json). Within v1, fields are only added, never removed or renamed, so ignore fields you don't know.
5. Treat it as untrusted data. Never write a robots.txt line from it without your own checks.

GitHub's edge caches these files for about five minutes. The version is date-based (`2026.10.25`, then `2026.10.25.2` for a second release that day), and each release is tagged.

**Signing (coming next):** the manifest will be signed with an Ed25519 key, and Coywolf Pack will refuse a list whose manifest signature doesn't verify. The signature field isn't in the manifest yet.

### The format

```json
{ "schemaVersion": 1, "version": "2026.10.25", "generatedAt": "2026-10-25T00:00:00Z",
  "bots": [ { "slug", "name", "operator", "category", "purpose?", "description", "token", "aliases?",
              "status", "evidence", "sourceUrl?", "verifiedAt?", "note?", "origin", "radarSeenAt?",
              "delisted?", "mergedSlugs?", "previousTokens?": [{ "token", "until" }] } ],
  "groups": [ { "id", "label", "phrase", "description", "purposes", "tokens" } ] }
```

`origin` is `radar` (listed in Cloudflare Radar's bot directory) or `curated` (an operator-documented bot Radar doesn't list). `previousTokens` lists tokens a bot used before, so a rule naming an old token can be mapped to the new one.

## How the repository works

```
source/radar-snapshot.json   the last Cloudflare Radar pull (machine-owned)
source/curated.json          Coywolf's decisions per slug: token, verification, purpose (+ source), category fix, exclude, aliases
source/extras.json           bots Radar doesn't list
source/groups.json           presets: purposes, include, exclude and display order
source/release.json          release history; the newest entry sets the published version and date
source/exceptions.json       validation exceptions carried over from the seed (warnings, to be fixed)
dist/v1/                     the published files, built from source/ (never edited by hand)
schema/v1.json               JSON Schema (draft 2020-12) for dist/v1/crawler-directory.json
scripts/build.py             builds dist/v1/ from source/
scripts/validate.py          the checks every release must pass
```

Keeping Radar's data (`radar-snapshot.json`) apart from Coywolf's decisions (`curated.json`) means a Radar pull can never overwrite a verification, and a rejected bot stays rejected.

The scripts need Python 3.11 or later and nothing else:

```bash
python3 scripts/build.py           # rebuild dist/v1/ from source/
python3 scripts/build.py --check   # fail if dist/v1/ isn't what source/ produces
python3 scripts/validate.py        # schema, unique slugs and tokens, safe tokens, sources, groups, manifest
python3 -m unittest discover -s tests
```

The build never reads the clock (the version and date come from `source/release.json`), so the same source always produces byte-identical files. The [Verify workflow](.github/workflows/verify.yml) runs all three on every push and pull request to `main`.

## License

- **Data** (`source/`, `dist/`): [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). You may share and adapt it for non-commercial purposes, as long as you credit Coywolf and Cloudflare Radar, link to the license and say what you changed. See [LICENSE-DATA](LICENSE-DATA).
- **Code** (schema, scripts, tests, workflow): MIT, copyright Coywolf, LLC. See [LICENSE](LICENSE).
