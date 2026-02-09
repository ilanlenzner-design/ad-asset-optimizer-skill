---
name: ad-asset-optimizer
description: Optimize and clean up game ad assets before delivery. Detects unused images by analyzing source code imports, compresses used images via TinyPNG, and deletes unused assets. Use when (1) an ad is ready for delivery and needs optimization, (2) the user wants to clean up ad assets, (3) detect unused images in a game project, (4) optimize ad weight/size, or (5) mentions "optimize ad", "clean up assets", "ad ready", "unused assets", "ad weight", or "asset cleanup". Requires TINYPNG_API_KEY environment variable.
---

# Ad Asset Optimizer

Optimize game ad projects for delivery: detect unused assets, compress images via TinyPNG, delete dead files, and report final ad weight.

## Prerequisites

- `TINYPNG_API_KEY` env var must be set
- Python 3 (stdlib only, no external packages)

## Workflow

### Step 1: Analyze the Project

Run the optimizer in **analyze** mode to scan without making changes:

```bash
python3 /Users/ilan/.claude/skills/ad-asset-optimizer/scripts/optimize.py \
  --dir /path/to/game/project \
  --analyze
```

This will:
- Find all image files in `assets/`
- Parse all source files (`.ts`, `.js`, `.css`) for asset references
- Classify each image as **used** or **unused**
- Report current total size

### Step 2: Present Findings to User

Show the analysis results clearly:

**Used assets** — referenced in source code via imports or string literals:
```
Used (52 files, 1.8 MB):
  Background2.jpg       862 KB  ← imported in GameResources.ts
  TikiBody.png          168 KB  ← imported in GameResources.ts
  ...
```

**Unused assets** — NOT referenced anywhere in source code:
```
Unused (37 files, 380 KB):
  Club.png              1.7 KB  ← commented-out import in GameResources.ts
  Diamond.png           1.3 KB  ← no reference found
  click_00003.png       1.5 KB  ← individual frame (sprite sheet used instead)
  ...
```

### Step 3: Get User Confirmation

Before making changes, confirm:
1. Which unused files to delete
2. Whether to compress all used files
3. Whether to create backups

### Step 4: Run Optimization

Execute the full optimization:

```bash
# Full optimization: compress used + delete unused
python3 /Users/ilan/.claude/skills/ad-asset-optimizer/scripts/optimize.py \
  --dir /path/to/game/project \
  --compress --delete-unused

# Compress only (don't delete anything)
python3 /Users/ilan/.claude/skills/ad-asset-optimizer/scripts/optimize.py \
  --dir /path/to/game/project \
  --compress

# Delete unused only (don't compress)
python3 /Users/ilan/.claude/skills/ad-asset-optimizer/scripts/optimize.py \
  --dir /path/to/game/project \
  --delete-unused

# Keep backups of deleted files
python3 /Users/ilan/.claude/skills/ad-asset-optimizer/scripts/optimize.py \
  --dir /path/to/game/project \
  --compress --delete-unused --backup
```

### Step 5: Present Final Report

The script outputs a JSON report. Present it as a readable summary:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Ad Asset Optimization Report
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Project:          tiki_solitaire_2_iq_test

  Before:           89 files, 2.16 MB
  After:            52 files, 620 KB

  Compressed:       52 files (1.8 MB → 620 KB, -66%)
  Deleted unused:   37 files (380 KB removed)

  Total reduction:  1.54 MB saved (71%)
  Final ad weight:  620 KB
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## How Unused Detection Works

The script detects unused assets by:

1. **Scanning all image files** in the `assets/` directory recursively
2. **Parsing source files** (`.ts`, `.js`, `.tsx`, `.jsx`, `.css`) for references:
   - ES6 import statements: `import X from 'assets/images/file.png'`
   - Require calls: `require('assets/images/file.png')`
   - CSS url() references: `url('assets/fonts/file.woff')`
   - String literals containing the filename
3. **An image is "used"** if its filename appears in ANY source file (even as a partial match)
4. **An image is "unused"** if no source file references it at all

### Edge Cases Handled

- **Commented-out imports**: Still detected as referenced (conservative). The `--strict` flag ignores commented imports.
- **Frame sequences**: Individual frames (click_00003.png) often unused when a sprite sheet exists
- **Min variants**: Both `Button.png` and `Button-min.png` checked independently
- **Marketing assets**: `game-capture.jpg` at root level flagged if unreferenced

## Integration with Apollo

The script outputs structured JSON to stdout, making it callable as an LLM tool:

```json
{
  "project": "tiki_solitaire_2_iq_test",
  "before": {"files": 89, "size_bytes": 2265088, "size_human": "2.16 MB"},
  "after": {"files": 52, "size_bytes": 634880, "size_human": "620 KB"},
  "compressed": [...],
  "deleted": [...],
  "errors": [],
  "total_saved_pct": 71.0,
  "final_weight_human": "620 KB"
}
```
