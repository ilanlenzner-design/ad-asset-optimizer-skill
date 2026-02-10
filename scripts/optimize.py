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

    deleted_results = []
    deleted_total_size = 0

    if do_delete and unused:
        print(f"\n{'='*60}")
        print(f"  Deleting {len(unused)} unused assets (not imported in source)")
        print(f"{'='*60}\n")

        for entry in unused:
            fpath = entry["path"]
            rel = entry["rel_path"]
            size = entry["size"]

            try:
                if backup:
                    backup_dir = os.path.join(project_dir, ".deleted_assets_backup")
                    backup_path = os.path.join(backup_dir, entry["rel_path"])
                    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                    shutil.copy2(fpath, backup_path)

                os.remove(fpath)
                deleted_total_size += size
                deleted_results.append({"file": rel, "size": size, "size_human": entry["size_human"]})
                print(f"  Deleted: {rel} ({entry['size_human']})")
            except Exception as e:
                print(f"  ERROR deleting {rel}: {e}")

        parent_dirs = set()
        for entry in unused:
            parent_dirs.add(os.path.dirname(entry["path"]))
        for d in sorted(parent_dirs, key=len, reverse=True):
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
                    print(f"  Removed empty directory: {os.path.relpath(d, project_dir)}")
            except Exception:
                pass

    final_size = initial_total_size - total_compressed_saved - deleted_total_size
    final_file_count = initial_file_count - len(deleted_results)
    total_reduction = total_compressed_saved + deleted_total_size
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
    if do_delete:
        print(f"  Deleted unused:   {len(deleted_results)} files ({format_size(deleted_total_size)} removed)")
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
        "deletion": {
            "files_deleted": len(deleted_results),
            "bytes_freed": deleted_total_size,
            "size_human": format_size(deleted_total_size),
            "items": deleted_results,
        },
        "total_saved_bytes": total_reduction,
        "total_saved_pct": round(total_reduction_pct, 1),
        "final_weight_bytes": final_size,
        "final_weight_human": format_size(final_size),
        "api_compressions_this_month": compression_count,
    }

    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description="Optimize game ad assets")
    parser.add_argument("--dir", required=True, help="Game project directory")
    parser.add_argument("--analyze", action="store_true", help="Analyze only — no changes")
    parser.add_argument("--compress", action="store_true", help="Compress used images via TinyPNG")
    parser.add_argument("--delete-unused", action="store_true", help="Delete unused assets (not imported in source code)")
    parser.add_argument("--backup", action="store_true", help="Keep backups before changes")
    parser.add_argument("--strict", action="store_true", help="Ignore commented-out imports when detecting usage")
    args = parser.parse_args()

    if not os.path.isdir(args.dir):
        print(f"ERROR: Directory not found: {args.dir}", file=sys.stderr)
        sys.exit(1)

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
        print("Specify --analyze, --compress, or --delete-unused. Use --help for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
