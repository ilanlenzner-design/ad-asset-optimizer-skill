---
name: ad-asset-optimizer
description: Optimize game ad assets for delivery. Parses GameResources.ts imports to identify used vs unused images, compresses used assets via TinyPNG, and deletes unused ones. Use when optimizing ad weight, cleaning up assets, or preparing an ad for delivery. Trigger phrases: "optimize ad", "clean up assets", "ad ready", "unused assets", "ad weight". Requires TINYPNG_API_KEY.
---

# Ad Asset Optimizer

Prereq: `TINYPNG_API_KEY` env var. Game projects live in `~/games/<project_name>/`.

## Full optimization (recommended)

```bash
python3 /Users/ilan/.claude/skills/ad-asset-optimizer/scripts/optimize.py \
  --dir /path/to/game/project \
  --compress --delete-unused --strict
```

## Analyze only (preview, no changes)

```bash
python3 /Users/ilan/.claude/skills/ad-asset-optimizer/scripts/optimize.py \
  --dir /path/to/game/project \
  --analyze --strict
```

## Flags

| Flag | Effect |
|------|--------|
| `--compress` | Compress used images via TinyPNG API |
| `--delete-unused` | Delete images not imported in source code |
| `--strict` | Ignore commented-out imports (recommended) |
| `--backup` | Copy files to `.deleted_assets_backup/` before modifying |
| `--analyze` | Report only, no changes |

## How it works

Parses ES6 `import ... from 'assets/...'` statements in `.ts`/`.js`/`.css` files (primarily `GameResources.ts`). Images in `assets/` matching an import = **used**. Everything else = **unused** and gets deleted.

## Output

Script prints a human-readable report + structured JSON to stdout. Present the summary to the user.
