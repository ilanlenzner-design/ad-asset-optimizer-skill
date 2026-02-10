#!/usr/bin/env python3
"""
Ad Asset Optimizer — detect unused assets, compress via TinyPNG, clean up.

Uses only Python stdlib (no external packages required).

Usage:
    python3 optimize.py --dir ./game-project --analyze
    python3 optimize.py --dir ./game-project --compress --delete-unused
    python3 optimize.py --dir ./game-project --compress --delete-unused --backup
    python3 optimize.py --dir ./game-project --compress --delete-unused --strict

Environment:
    TINYPNG_API_KEY must be set (for --compress mode).
"""

import argparse
import base64
import json
import os
import re
import shutil
import sys
import urllib.request
import urllib.error
from pathlib import Path

TINIFY_URL = "https://api.tinify.com/shrink"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".css", ".html"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "output", "__pycache__"}


def get_api_key():
    key = os.environ.get("TINYPNG_API_KEY", "")
    if not key:
        print("ERROR: TINYPNG_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return key


def format_size(bytes_val):
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    else:
        return f"{bytes_val / (1024 * 1024):.2f} MB"


def find_images(project_dir):
    images = []
    assets_dir = os.path.join(project_dir, "assets")
    if not os.path.isdir(assets_dir):
        for root, dirs, files in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                    images.append(os.path.join(root, f))
        return images

    for root, dirs, files in os.walk(assets_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                images.append(os.path.join(root, f))
    return images


def find_source_files(project_dir):
    sources = []
    src_dir = os.path.join(project_dir, "src")
    search_dirs = [src_dir] if os.path.isdir(src_dir) else [project_dir]
    search_dirs.append(project_dir)

    seen = set()
    for search_dir in search_dirs:
        for root, dirs, files in os.walk(search_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                fpath = os.path.join(root, f)
                if fpath in seen:
                    continue
                if os.path.splitext(f)[1].lower() in SOURCE_EXTENSIONS:
                    sources.append(fpath)
                    seen.add(fpath)
    return sources


def read_file_content(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def parse_import_paths(project_dir, strict=False):
    imported_paths = set()
    import_pattern = re.compile(r"""from\s+['"](.+?\.(?:png|jpg|jpeg|webp))['"]""", re.IGNORECASE)

    source_files = find_source_files(project_dir)
    for sf in source_files:
        content = read_file_content(sf)
        for line in content.split("\n"):
            stripped = line.strip()
            if strict and stripped.startswith("//"):
                continue
            match = import_pattern.search(line)
            if match:
                raw_path = match.group(1)
                if raw_path.startswith("assets/"):
                    imported_paths.add(raw_path)
                elif raw_path.startswith("./") or raw_path.startswith("../"):
                    abs_path = os.path.normpath(os.path.join(os.path.dirname(sf), raw_path))
                    imported_paths.add(os.path.relpath(abs_path, project_dir))
                else:
                    imported_paths.add(raw_path)

    return imported_paths


def detect_unused(project_dir, images, strict=False):
    imported_paths = parse_import_paths(project_dir, strict=strict)

    imported_abs = set()
    for p in imported_paths:
        abs_p = os.path.normpath(os.path.join(project_dir, p))
        imported_abs.add(abs_p)

    used = []
    unused = []
    assets_dir = os.path.join(project_dir, "assets")
    base_dir = assets_dir if os.path.isdir(assets_dir) else project_dir

    for img_path in images:
        norm_path = os.path.normpath(img_path)
        size = os.path.getsize(img_path)
        entry = {
            "path": img_path,
            "rel_path": os.path.relpath(img_path, base_dir),
            "filename": os.path.basename(img_path),
            "size": size,
            "size_human": format_size(size),
        }

        if norm_path in imported_abs:
            used.append(entry)
        else:
            unused.append(entry)

    return used, unused


def compress_file(filepath, api_key):
    auth_string = base64.b64encode(f"api:{api_key}".encode()).decode()
    with open(filepath, "rb") as f:
        image_data = f.read()

    req = urllib.request.Request(
        TINIFY_URL,
        data=image_data,
        headers={
            "Authorization": f"Basic {auth_string}",
            "Content-Type": "application/octet-stream",
        },
    )

    try:
        with urllib.request.urlopen(req) as response:
            body = json.loads(response.read().decode())
            compression_count = response.headers.get("Compression-Count", "?")
            output_url = body.get("output", {}).get("url", "")
            input_size = body.get("input", {}).get("size", 0)
            output_size = body.get("output", {}).get("size", 0)
            return output_url, input_size, output_size, compression_count
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        try:
            err = json.loads(error_body)
            msg = err.get("message", error_body)
        except json.JSONDecodeError:
            msg = error_body
        raise RuntimeError(f"TinyPNG API error ({e.code}): {msg}")


def download_file(url, dest):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        data = response.read()
    with open(dest, "wb") as f:
        f.write(data)


def run_analyze(project_dir, images, strict=False):
    used, unused = detect_unused(project_dir, images, strict=strict)

    used_size = sum(e["size"] for e in used)
    unused_size = sum(e["size"] for e in unused)
    total_size = used_size + unused_size

    print(f"\n{'='*60}")
    print(f"  Ad Asset Analysis")
    print(f"{'='*60}")
    print(f"  Project: {os.path.basename(project_dir)}")
    print(f"  Total images: {len(images)} ({format_size(total_size)})")
    print(f"  Used: {len(used)} ({format_size(used_size)})")
    print(f"  Unused: {len(unused)} ({format_size(unused_size)})")
    print()

    if unused:
        print(f"  UNUSED ASSETS ({len(unused)} files, {format_size(unused_size)}):")
        for entry in sorted(unused, key=lambda x: -x["size"]):
            print(f"    {entry['rel_path']:50s} {entry['size_human']:>10s}")
        print()

    if used:
        print(f"  USED ASSETS ({len(used)} files, {format_size(used_size)}):")
        for entry in sorted(used, key=lambda x: -x["size"])[:15]:
            print(f"    {entry['rel_path']:50s} {entry['size_human']:>10s}")
        if len(used) > 15:
            print(f"    ... and {len(used) - 15} more")
        print()

    print(f"{'='*60}\n")

    result = {
        "mode": "analyze",
        "project": os.path.basename(project_dir),
        "total": {"files": len(images), "size_bytes": total_size, "size_human": format_size(total_size)},
        "used": {
            "files": len(used),
            "size_bytes": used_size,
            "size_human": format_size(used_size),
            "items": [{"file": e["rel_path"], "size": e["size"], "size_human": e["size_human"]} for e in used],
        },
        "unused": {
            "files": len(unused),
            "size_bytes": unused_size,
            "size_human": format_size(unused_size),
            "items": [{"file": e["rel_path"], "size": e["size"], "size_human": e["size_human"]} for e in unused],
        },
    }
    print(json.dumps(result, indent=2))
    return result


def generate_review_html(project_dir, sorted_unused, manifest):
    project_name = os.path.basename(project_dir)
    cards = []
    for i, entry in enumerate(sorted_unused):
        abs_path = entry["path"]
        rel_display = entry["rel_path"]
        fname = entry["filename"]
        size_h = entry["size_human"]
        cards.append(
            f'<div class="card" data-index="{i}">'
            f'<label class="cb-wrap"><input type="checkbox" checked data-index="{i}"><span class="check"></span></label>'
            f'<img src="file://{abs_path}" alt="{fname}" onerror="this.src=\'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2280%22 height=%2280%22><rect fill=%22%23333%22 width=%2280%22 height=%2280%22/><text fill=%22%23888%22 x=%2240%22 y=%2244%22 text-anchor=%22middle%22 font-size=%2210%22>no preview</text></svg>\'">'
            f'<div class="info"><span class="name" title="{rel_display}">{fname}</span><span class="size">{size_h}</span></div>'
            f'</div>'
        )
    cards_html = "\n".join(cards)

    manifest_json = json.dumps(manifest, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Delete Review - {project_name}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#1a1a2e;color:#e0e0e0;min-height:100vh}}
.header{{background:#16213e;padding:20px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #0f3460;position:sticky;top:0;z-index:10}}
.header h1{{font-size:20px;font-weight:600}}
.header .subtitle{{color:#888;font-size:14px;margin-top:4px}}
.actions{{display:flex;gap:12px;align-items:center}}
.btn{{padding:10px 24px;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;transition:all .15s}}
.btn-save{{background:#e94560;color:#fff}}
.btn-save:hover{{background:#c73751}}
.btn-toggle{{background:#0f3460;color:#e0e0e0;border:1px solid #1a1a4e}}
.btn-toggle:hover{{background:#1a1a4e}}
.counter{{font-size:14px;color:#888;min-width:180px;text-align:right}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:16px;padding:24px 32px}}
.card{{background:#16213e;border-radius:12px;overflow:hidden;border:2px solid transparent;transition:all .15s;position:relative}}
.card.unchecked{{opacity:.35;border-color:#333}}
.card.unchecked img{{filter:grayscale(1)}}
.cb-wrap{{position:absolute;top:8px;left:8px;z-index:2;cursor:pointer}}
.cb-wrap input{{display:none}}
.check{{display:block;width:24px;height:24px;border-radius:6px;background:rgba(0,0,0,.5);border:2px solid #555;transition:all .15s}}
.cb-wrap input:checked+.check{{background:#e94560;border-color:#e94560}}
.cb-wrap input:checked+.check::after{{content:'\\2715';display:block;color:#fff;text-align:center;font-size:14px;line-height:20px;font-weight:700}}
.card img{{width:100%;aspect-ratio:1;object-fit:contain;background:#111;padding:8px}}
.info{{padding:8px 10px}}
.name{{display:block;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.size{{display:block;font-size:11px;color:#888;margin-top:2px}}
</style>
</head>
<body>
<div class="header">
<div>
<h1>Unused Assets Review - {project_name}</h1>
<div class="subtitle">Checked assets will be deleted. Uncheck to keep.</div>
</div>
<div class="actions">
<button class="btn btn-toggle" onclick="toggleAll()">Toggle All</button>
<button class="btn btn-save" onclick="saveManifest()">Save &amp; Download Manifest</button>
<div class="counter" id="counter"></div>
</div>
</div>
<div class="grid">{cards_html}</div>
<script>
const manifest = {manifest_json};
function updateCounter(){{
  const checked=document.querySelectorAll('.card input:checked').length;
  const total=document.querySelectorAll('.card input').length;
  let size=0;
  document.querySelectorAll('.card input:checked').forEach(cb=>{{
    size+=manifest.files[cb.dataset.index].size;
  }});
  const h=size<1024?size+' B':size<1048576?(size/1024).toFixed(1)+' KB':(size/1048576).toFixed(2)+' MB';
  document.getElementById('counter').textContent=checked+'/'+total+' selected ('+h+')';
}}
document.querySelectorAll('.card input').forEach(cb=>{{
  cb.addEventListener('change',function(){{
    this.closest('.card').classList.toggle('unchecked',!this.checked);
    updateCounter();
  }});
}});
function toggleAll(){{
  const cbs=document.querySelectorAll('.card input');
  const allChecked=[...cbs].every(c=>c.checked);
  cbs.forEach(c=>{{c.checked=!allChecked;c.closest('.card').classList.toggle('unchecked',allChecked)}});
  updateCounter();
}}
function saveManifest(){{
  const kept=[];
  const selected=[];
  document.querySelectorAll('.card input').forEach(cb=>{{
    if(cb.checked) selected.push(manifest.files[cb.dataset.index]);
    else kept.push(manifest.files[cb.dataset.index].filename);
  }});
  const updated=Object.assign({{}},manifest,{{
    files:selected,
    total_unused_files:selected.length,
    total_unused_size:selected.reduce((s,f)=>s+f.size,0),
    total_unused_size_human:(selected.reduce((s,f)=>s+f.size,0)/1024).toFixed(1)+' KB'
  }});
  const blob=new Blob([JSON.stringify(updated,null,2)],{{type:'application/json'}});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='.deletion_manifest.json';
  a.click();
  if(kept.length){{alert('Manifest saved! Kept '+kept.length+' asset(s).\\nMove the downloaded file to:\\n'+manifest.project_dir+'/\\nThen run --confirm-delete.');}}
  else{{alert('Manifest saved (all '+selected.length+' files selected for deletion).\\nMove the downloaded file to:\\n'+manifest.project_dir+'/\\nThen run --confirm-delete.');}}
}}
updateCounter();
</script>
</body>
</html>"""

    review_path = os.path.join(project_dir, ".deletion_review.html")
    with open(review_path, "w") as f:
        f.write(html)
    return review_path


def run_optimize(project_dir, images, api_key, do_compress=False, do_delete=False, backup=False, strict=False):
    used, unused = detect_unused(project_dir, images, strict=strict)

    initial_total_size = sum(e["size"] for e in used) + sum(e["size"] for e in unused)
    initial_file_count = len(images)

    compressed_results = []
    compress_errors = []
    total_compressed_saved = 0
    total_compressed_original = 0
    compression_count = "?"

    if do_compress and used:
        print(f"\n{'='*60}")
        print(f"  Compressing {len(used)} used assets via TinyPNG")
        print(f"{'='*60}\n")

        for i, entry in enumerate(used, 1):
            fpath = entry["path"]
            rel = entry["rel_path"]
            original_size = entry["size"]

            try:
                print(f"  [{i}/{len(used)}] {rel} ({entry['size_human']})...", end=" ", flush=True)
                output_url, input_size, output_size, compression_count = compress_file(fpath, api_key)

                savings_pct = ((input_size - output_size) / input_size * 100) if input_size > 0 else 0
                if savings_pct < 1:
                    print(f"SKIP (already optimized)")
                    compressed_results.append({
                        "file": rel, "status": "skipped",
                        "original_size": input_size, "compressed_size": output_size,
                    })
                    continue

                if backup:
                    shutil.copy2(fpath, fpath + ".bak")

                download_file(output_url, fpath)
                saved = input_size - output_size
                total_compressed_saved += saved
                total_compressed_original += input_size

                print(f"{format_size(output_size)} (-{savings_pct:.0f}%)")
                compressed_results.append({
                    "file": rel, "status": "compressed",
                    "original_size": input_size, "compressed_size": output_size,
                    "saved_bytes": saved, "saved_pct": round(savings_pct, 1),
                })
            except Exception as e:
                print(f"ERROR: {e}")
                compress_errors.append({"file": rel, "error": str(e)})

    if do_delete and unused:
        manifest_path = os.path.join(project_dir, ".deletion_manifest.json")
        unused_size = sum(e["size"] for e in unused)
        sorted_unused = sorted(unused, key=lambda x: -x["size"])
        manifest = {
            "project": os.path.basename(project_dir),
            "project_dir": project_dir,
            "total_unused_files": len(unused),
            "total_unused_size": unused_size,
            "total_unused_size_human": format_size(unused_size),
            "files": [
                {"path": e["path"], "rel_path": e["rel_path"], "filename": e["filename"],
                 "size": e["size"], "size_human": e["size_human"]}
                for e in sorted_unused
            ],
        }
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        review_path = generate_review_html(project_dir, sorted_unused, manifest)

        print(f"\n{'='*60}")
        print(f"  Unused Assets Pending Deletion ({len(unused)} files, {format_size(unused_size)})")
        print(f"{'='*60}\n")
        for entry in sorted_unused:
            print(f"    {entry['rel_path']:50s} {entry['size_human']:>10s}")
        print(f"\n  Manifest saved to: {manifest_path}")
        print(f"  Review page: {review_path}")
        print(f"  Open the review page to visually inspect and unselect assets to keep.")
        print(f"  Then run with --confirm-delete to delete the selected files.")
        print(f"{'='*60}\n")

    final_size = initial_total_size - total_compressed_saved
    final_file_count = initial_file_count
    total_reduction = total_compressed_saved
    total_reduction_pct = (total_reduction / initial_total_size * 100) if initial_total_size > 0 else 0

    print(f"\n{'='*60}")
    print(f"  Ad Asset Optimization Report")
    print(f"{'='*60}")
    print(f"  Project:          {os.path.basename(project_dir)}")
    print()
    print(f"  Before:           {initial_file_count} files, {format_size(initial_total_size)}")
    print(f"  After:            {final_file_count} files, {format_size(final_size)}")
    print()
    if do_compress:
        compressed_count = len([r for r in compressed_results if r.get("status") == "compressed"])
        skipped_count = len([r for r in compressed_results if r.get("status") == "skipped"])
        compress_pct = (total_compressed_saved / total_compressed_original * 100) if total_compressed_original > 0 else 0
        print(f"  Compressed:       {compressed_count} files ({format_size(total_compressed_original)} -> {format_size(total_compressed_original - total_compressed_saved)}, -{compress_pct:.0f}%)")
        if skipped_count:
            print(f"  Already optimal:  {skipped_count} files")
        if compress_errors:
            print(f"  Compress errors:  {len(compress_errors)}")
    if do_delete and unused:
        pending_size = sum(e["size"] for e in unused)
        print(f"  Pending deletion: {len(unused)} files ({format_size(pending_size)}) — awaiting user approval")
    print()
    print(f"  Total reduction:  {format_size(total_reduction)} saved ({total_reduction_pct:.1f}%)")
    print(f"  Final ad weight:  {format_size(final_size)}")
    if compression_count != "?":
        print(f"  API compressions: {compression_count} used this month")
    print(f"{'='*60}\n")

    report = {
        "mode": "optimize",
        "project": os.path.basename(project_dir),
        "before": {
            "files": initial_file_count,
            "size_bytes": initial_total_size,
            "size_human": format_size(initial_total_size),
        },
        "after": {
            "files": final_file_count,
            "size_bytes": final_size,
            "size_human": format_size(final_size),
        },
        "compression": {
            "files_compressed": len([r for r in compressed_results if r.get("status") == "compressed"]),
            "files_skipped": len([r for r in compressed_results if r.get("status") == "skipped"]),
            "original_bytes": total_compressed_original,
            "saved_bytes": total_compressed_saved,
            "saved_pct": round((total_compressed_saved / total_compressed_original * 100) if total_compressed_original > 0 else 0, 1),
            "results": compressed_results,
            "errors": compress_errors,
        },
        "deletion_pending": {
            "files": len(unused) if do_delete else 0,
            "size_bytes": sum(e["size"] for e in unused) if do_delete else 0,
            "size_human": format_size(sum(e["size"] for e in unused)) if (do_delete and unused) else "0 B",
            "manifest": os.path.join(project_dir, ".deletion_manifest.json") if do_delete else None,
        },
        "total_saved_bytes": total_reduction,
        "total_saved_pct": round(total_reduction_pct, 1),
        "final_weight_bytes": final_size,
        "final_weight_human": format_size(final_size),
        "api_compressions_this_month": compression_count,
    }

    print(json.dumps(report, indent=2))
    return report


def run_confirm_delete(project_dir, backup=False):
    manifest_path = os.path.join(project_dir, ".deletion_manifest.json")
    if not os.path.exists(manifest_path):
        print("ERROR: No deletion manifest found. Run with --delete-unused first.", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    files = manifest.get("files", [])
    if not files:
        print("Manifest is empty — nothing to delete.")
        os.remove(manifest_path)
        return {"deleted": 0, "freed_bytes": 0}

    print(f"\n{'='*60}")
    print(f"  Deleting {len(files)} unused assets")
    print(f"{'='*60}\n")

    deleted_results = []
    deleted_total_size = 0

    for entry in files:
        fpath = entry["path"]
        rel = entry["rel_path"]
        size = entry["size"]

        if not os.path.exists(fpath):
            print(f"  SKIP (already gone): {rel}")
            continue

        try:
            if backup:
                backup_dir = os.path.join(project_dir, ".deleted_assets_backup")
                backup_path = os.path.join(backup_dir, rel)
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                shutil.copy2(fpath, backup_path)

            os.remove(fpath)
            deleted_total_size += size
            deleted_results.append({"file": rel, "size": size, "size_human": entry["size_human"]})
            print(f"  Deleted: {rel} ({entry['size_human']})")
        except Exception as e:
            print(f"  ERROR deleting {rel}: {e}")

    parent_dirs = set()
    for entry in files:
        parent_dirs.add(os.path.dirname(entry["path"]))
    for d in sorted(parent_dirs, key=len, reverse=True):
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                print(f"  Removed empty directory: {os.path.relpath(d, project_dir)}")
        except Exception:
            pass

    os.remove(manifest_path)

    print(f"\n  Deleted {len(deleted_results)} files, freed {format_size(deleted_total_size)}")
    print(f"{'='*60}\n")

    result = {
        "deleted": len(deleted_results),
        "freed_bytes": deleted_total_size,
        "freed_human": format_size(deleted_total_size),
        "items": deleted_results,
    }
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description="Optimize game ad assets")
    parser.add_argument("--dir", required=True, help="Game project directory")
    parser.add_argument("--analyze", action="store_true", help="Analyze only — no changes")
    parser.add_argument("--compress", action="store_true", help="Compress used images via TinyPNG")
    parser.add_argument("--delete-unused", action="store_true", help="List unused assets and create deletion manifest")
    parser.add_argument("--confirm-delete", action="store_true", help="Delete files listed in the manifest (requires prior --delete-unused run)")
    parser.add_argument("--backup", action="store_true", help="Keep backups before changes")
    parser.add_argument("--strict", action="store_true", help="Ignore commented-out imports when detecting usage")
    args = parser.parse_args()

    if not os.path.isdir(args.dir):
        print(f"ERROR: Directory not found: {args.dir}", file=sys.stderr)
        sys.exit(1)

    if args.confirm_delete:
        run_confirm_delete(args.dir, backup=args.backup)
        return

    images = find_images(args.dir)
    if not images:
        print("No image files found in project.")
        sys.exit(0)

    if args.analyze:
        run_analyze(args.dir, images, strict=args.strict)
    elif args.compress or args.delete_unused:
        api_key = get_api_key() if args.compress else ""
        run_optimize(args.dir, images, api_key, do_compress=args.compress,
                     do_delete=args.delete_unused, backup=args.backup, strict=args.strict)
    else:
        print("Specify --analyze, --compress, --delete-unused, or --confirm-delete. Use --help for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
