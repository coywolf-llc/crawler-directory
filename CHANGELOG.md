# Changelog

Newest first. Each release is tagged with its version.

## 2026.10.25 (seed release)

The first release is an exact copy of the crawler list bundled with Coywolf Pack (`src/robots/data/bots.json`, built 2026-10-04) and its four presets, so sites that switch to this list see no change to their robots.txt.

- **694 bots:** 143 verified from operator documentation, 419 verified from Radar's user-agent strings, 132 unverified. 30 Radar slugs that share a token with another bot are merged (`mergedSlugs`). ChatGPT agent is left out (it signs its requests and documents no token).
- **4 presets:** Search engines (10 tokens), AI training (10), AI search and assistants (12) and SEO tools (7), with the same members in the same order as Coywolf Pack's `CRAWLER_GROUPS`.
- **Source data:** the Radar pull behind the pack's list wasn't kept, so `source/radar-snapshot.json` was reconstructed from the list itself (without user-agent patterns), and every token, plus every verification that needs Radar's user-agent strings or operator documentation, is pinned in `source/curated.json`. The next Radar pull replaces the snapshot.
- **Known issues, kept as is in this release:** 26 verified bots have an http source URL or none (listed in `source/exceptions.json`), and 4 bots have a source URL that isn't a single clean URL. They're reported as warnings and will be fixed in later releases.
