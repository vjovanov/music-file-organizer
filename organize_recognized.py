#!/usr/bin/env python3
"""
organize_recognized.py

Organize recognized music files into a directory structure defined by a pattern:

  Pattern placeholders:
    %A = artist, %L = album, %S = song,
    %Y = release year, %G = genre, %B = label, %I = ISRC,
    %E = Explicit|Clean, %e = true|false, %a = artist adamid,
    %T = Apple Music track id, %U = Apple Music album id
  Examples:
    - "%A/%L/%S" -> <artist>/<album>/<song>.<ext>
    - "%A - %S"  -> <artist - song>.<ext>
    - "%A/%Y - %L/%S" -> <artist>/<year - album>/<song>.<ext>
    - "%G/%A - %S (%E)" -> <genre>/<artist - song (Explicit|Clean)>.<ext>

Key features:
- Default behavior is COPY (non-destructive). Use --move to move instead.
- Dry-run by default; use --apply to actually copy/move files.
- Identical files (bit-for-bit) are NOT duplicated; only one copy is kept.
- For differing contents that map to the same destination, subsequent files are named
  with the original source basename after a configurable duplicate token (default: "_duplicate_"), e.g.,
  "Song.mp3", "Song_duplicate_OriginalName.mp3", and only if that conflicts use "Song_duplicate_OriginalName_2.mp3".
- Writes a detailed duplicates report to JSON (duplicates.json by default) and notes identical ones skipped.
- Skips missing sources and reports them.
- Produces stable, cross-platform-safe path components via sanitization.
"""

import argparse
import json
import os
import re
import hashlib
import shutil
import sys
import unicodedata
from collections import defaultdict
from typing import Dict, List, Tuple
from datetime import datetime, timezone


def sanitize_component(s: str) -> str:
    """
    Sanitize a single filesystem path component:
    - Normalize unicode
    - Replace path separators with '-'
    - Replace reserved/unsafe/control characters with '-'
    - Collapse whitespace and trim trailing/leading dots/spaces
    - Provide a fallback "Unknown" when empty after sanitization
    """
    if s is None:
        s = ""
    s = str(s)
    s = unicodedata.normalize("NFKC", s)

    # Replace path separators explicitly
    s = s.replace("/", "-").replace("\\", "-")

    # Replace reserved/unsafe and control characters using explicit per-char check
    out_chars = []
    for ch in s:
        code = ord(ch)
        if code < 32 or ch in '<>:"|?*':
            out_chars.append("-")
        else:
            out_chars.append(ch)
    s = "".join(out_chars)

    # Collapse whitespace and trim problematic trailing chars
    # NOTE: Fixes previous bug: pattern must be r"\s+" not "\\s+"
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" .")

    if not s:
        s = "Unknown"
    return s


def is_unknown_text(text: str) -> bool:
    """
    Return True if a value represents an 'Unknown' placeholder that should be dropped
    when --keep-unknowns is not set.
    Matches case-insensitively:
      - 'Unknown'
      - 'Unknown <something>' e.g., 'Unknown Artist', 'Unknown Album', etc.
    """
    if text is None:
        return True
    t = str(text).strip().lower()
    return t == "unknown" or t.startswith("unknown ")


def load_mapping(path: str) -> Dict[str, dict]:
    """Load the recognized-songs mapping from JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_file_hash(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Return SHA-256 hex digest of a file's content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def add_suffix_to_filename(path: str, n: int) -> str:
    """
    Add "_n" suffix to the filename (before extension) if n > 1, otherwise return as-is.

    Example:
      path=".../Song.mp3", n=1 -> ".../Song.mp3"
      path=".../Song.mp3", n=2 -> ".../Song_2.mp3"
    """
    if n <= 1:
        return path
    d = os.path.dirname(path)
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    return os.path.join(d, f"{stem}_{n}{ext}")


def compute_unique_destination(base_dest: str, start_n: int = 1) -> Tuple[str, int]:
    """
    Compute a unique destination path. If base_dest exists, increment the suffix
    (_n) until a non-existing filename is found.

    Returns:
      (unique_dest_path, final_n)
    """
    n = max(1, start_n)
    candidate = add_suffix_to_filename(base_dest, n)
    while os.path.exists(candidate):
        n += 1
        candidate = add_suffix_to_filename(base_dest, n)
    return candidate, n


def build_dest_rel_base(replacements: Dict[str, str], ext: str, pattern: str, keep_unknowns: bool = False) -> str:
    """
    Render the destination relative path (including extension) from a pattern.

    Behavior:
    - Replaces placeholders using the provided replacements mapping (e.g., %A, %L, %S, %Y, %G, %B, %I, %E, %e, %a, %T, %U)
    - Splits on "/" to form directories
    - Sanitizes each path component
    - Unless keep_unknowns is True, drops components that are 'Unknown' or 'Unknown ...'
      and trims dangling punctuation around removed values
    - Appends the original file extension to the final leaf name
    """
    # First, optionally blank-out unknown placeholder values to reduce noise inside parts
    # (This helps when a part is solely a placeholder such as "%L".)
    if not keep_unknowns:
        replacements = {
            k: ("" if is_unknown_text(v) else v)
            for k, v in replacements.items()
        }

    sub = pattern
    for k, v in replacements.items():
        sub = sub.replace(k, v)

    parts = [p for p in sub.split("/") if p not in ("", ".", "..")]

    safe_parts: List[str] = []
    for p in parts:
        comp = sanitize_component(p)
        if not keep_unknowns and is_unknown_text(comp):
            # Drop components that are just 'Unknown...' after sanitization
            continue
        if not keep_unknowns:
            # Trim dangling punctuation/hyphens introduced by removed unknowns
            comp = re.sub(r"^[\s\-_.(),;:]+|[\s\-_.(),;:]+$", "", comp)
            comp = re.sub(r"\s{2,}", " ", comp).strip()
        if comp:
            safe_parts.append(comp)

    if not safe_parts:
        # Fallback: use non-empty core triplet components if available; otherwise 'Unknown'
        core = []
        for key in ("%A", "%L", "%S"):
            v = sanitize_component(replacements.get(key, ""))
            if not keep_unknowns and is_unknown_text(v):
                v = ""
            if not keep_unknowns:
                v = re.sub(r"^[\s\-_.(),;:]+|[\s\-_.(),;:]+$", "", v).strip()
            if v:
                core.append(v)
        if not core:
            core = ["Unknown"]
        safe_parts = core

    file_stem = safe_parts[-1]
    dir_parts = safe_parts[:-1]
    if dir_parts:
        path_no_ext = os.path.join(*dir_parts, file_stem)
    else:
        path_no_ext = file_stem
    return path_no_ext + ext


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Organize recognized music files according to a path pattern using placeholders: "
            "%A (artist), %L (album), %S (song), %Y (year), %G (genre), %B (label), %I (ISRC), "
            "%E (Explicit|Clean), %e (true|false), %a (artist adamid), %T (Apple track id), %U (Apple album id).\n"
            "- Default action is COPY (dry-run unless --apply is provided).\n"
            "- Use --move to move instead of copy.\n"
            "- Identical files are skipped; differing contents mapping to the same path are named with the original source basename after the configured duplicate token (default: '_duplicate_'), with numeric suffix added only on conflict, and listed."
        )
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="Path to recognized-songs.json mapping file (required)",
    )
    parser.add_argument(
        "-d",
        "--dest-root",
        default=".",
        help="Destination root directory. Defaults to current directory '.'",
    )
    parser.add_argument(
        "-p",
        "--pattern",
        default="%A/%L/%S",
        help=("Output path pattern using placeholders: %%A=artist, %%L=album, %%S=song, "
              "%%Y=year, %%G=genre, %%B=label, %%I=ISRC, %%E=Explicit|Clean, %%e=true|false, "
              "%%a=artist adamid, %%T=Apple Music track id, %%U=Apple Music album id. "
              "Final filename gets source extension."),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the copy/move operations. Without this flag, runs in dry-run mode.",
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move files instead of copying them (default behavior is COPY).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Print per-file planned actions."
    )
    parser.add_argument(
        "--duplicates-json",
        default="duplicates.json",
        help="Path to write duplicates report JSON (default: duplicates.json)."
    )
    parser.add_argument(
        "--keep-unknowns",
        action="store_true",
        help="Keep 'Unknown' values (e.g., 'Unknown Album') in the rendered path. By default, unknown values are dropped."
    )
    parser.add_argument(
        "--duplicate-token",
        default="_duplicate_",
        help="Token inserted between base name and original source basename for duplicates (sanitized for filesystem safety). Default: '_duplicate_'."
    )
    args = parser.parse_args()

    # Load mapping
    try:
        mapping = load_mapping(args.input)
    except FileNotFoundError:
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(mapping, dict):
        print(
            "ERROR: Mapping must be a JSON object mapping absolute paths to metadata.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Destination root (defaults to current directory '.')
    dest_root = args.dest_root
    if args.verbose:
        print(f"Destination root: {os.path.abspath(dest_root)}")
        print(f"Pattern: {args.pattern}")
        print("Mode:", "MOVE" if args.move else "COPY")
        print("Apply:", "YES" if args.apply else "NO (dry-run)")
        print("Keep unknowns:", "YES" if args.keep_unknowns else "NO")

    sources = list(mapping.keys())

    # Group entries by destination relative path (case-insensitive)
    groups: Dict[str, List[dict]] = defaultdict(list)
    missing_sources: List[str] = []
    total_entries = 0

    for src, meta in mapping.items():
        # Skip non-dict entries and non-path keys like "$schema"
        if not isinstance(meta, dict) or (isinstance(src, str) and src.startswith("$")):
            continue
        total_entries += 1

        # Respect unknown flags if present in recognized.schema.json; fall back to text heuristic
        author_unknown = meta.get("author_unknown")
        album_unknown = meta.get("album_unknown")
        song_unknown = meta.get("song_unknown")

        author = meta.get("author")
        album = meta.get("album")
        song = meta.get("song")

        if author_unknown is True or is_unknown_text(author):
            author = "Unknown Artist"
        if album_unknown is True or is_unknown_text(album):
            album = "Unknown Album"
        if song_unknown is True or is_unknown_text(song):
            song = "Unknown Song"

        author_s = sanitize_component(author)
        album_s = sanitize_component(album)
        song_s = sanitize_component(song)

        ext = os.path.splitext(src)[1] or ""
        ext = ext.lower()

        # Enrich placeholders from optional metadata and build destination
        artist_adamid = meta.get("artist_adamid")
        release_year = meta.get("release_year")
        label = meta.get("label")
        genre_primary = meta.get("genre_primary")
        isrc = meta.get("isrc")
        explicit_bool = meta.get("explicit")
        applemusic_track_id = meta.get("applemusic_track_id")
        applemusic_album_id = meta.get("applemusic_album_id")

        artist_id_s = sanitize_component(artist_adamid or "Unknown")
        year_s = sanitize_component(str(release_year) if release_year is not None else "Unknown")
        genre_s = sanitize_component(genre_primary or "Unknown")
        label_s = sanitize_component(label or "Unknown")
        isrc_s = sanitize_component(isrc or "Unknown")
        explicit_str = "Explicit" if explicit_bool else "Clean"
        explicit_raw = "true" if explicit_bool else "false"
        am_track_s = sanitize_component(applemusic_track_id or "Unknown")
        am_album_s = sanitize_component(applemusic_album_id or "Unknown")

        replacements = {
            "%A": author_s,
            "%L": album_s,
            "%S": song_s,
            "%Y": year_s,
            "%G": genre_s,
            "%B": label_s,
            "%I": isrc_s,
            "%E": explicit_str,
            "%e": explicit_raw,
            "%a": artist_id_s,
            "%T": am_track_s,
            "%U": am_album_s,
        }

        # Build destination relative base path (including extension) from pattern
        dest_rel_base = build_dest_rel_base(replacements, ext, args.pattern, args.keep_unknowns)

        # Deduplicate case-insensitively on the final destination path
        key = dest_rel_base.lower()

        try:
            size = os.path.getsize(src)
        except OSError:
            size = -1
            missing_sources.append(src)


        groups[key].append(
            {
                "src": src,
                "size": size,
                "author_s": author_s,
                "album_s": album_s,
                "song_s": song_s,
                "ext": ext,
                "dest_rel_base": dest_rel_base,
            }
        )

    # Plan operations:
    # - Skip identical duplicates (bit-for-bit).
    # - For differing contents that map to same destination:
    #     First occurrence: Song.ext
    #     Subsequent occurrences: Song_<OriginalSourceBasename>.ext
    #     Only if that conflicts, add numeric suffix: Song_<Original>_2.ext, etc.
    ops = []  # planned operations for valid (non-missing) sources
    duplicates_report = {}  # key -> list of (src, assigned_rel) or (src, None, "skipped-identical")
    duplicate_groups = 0
    total_duplicates_kept = 0
    identical_duplicates_skipped = 0

    already_in_place = 0
    planned_copies = 0
    planned_moves = 0

    for key, items in groups.items():
        # Any group with more than one valid entry is a duplicates group
        # But we only count duplicates among files that actually exist
        valid_items = [i for i in items if i["size"] >= 0]
        if len(valid_items) > 1:
            duplicate_groups += 1

        seen_hashes = set()
        unique_count = 0

        for it in valid_items:
            # Compute content hash to detect identical files
            try:
                h = compute_file_hash(it["src"])
            except Exception:
                h = None  # Treat unreadable as unique to avoid accidental drops

            if h is not None and h in seen_hashes:
                identical_duplicates_skipped += 1
                if len(valid_items) > 1:
                    duplicates_report.setdefault(key, []).append((it["src"], None, "skipped-identical"))
                continue

            if h is not None:
                seen_hashes.add(h)

            unique_count += 1

            base_rel = it["dest_rel_base"]
            dest_abs_base = os.path.join(dest_root, base_rel)

            # Build candidate name:
            # - First unique keeps base name
            # - Subsequent uniques keep original source basename after "_"
            if unique_count == 1:
                candidate = dest_abs_base
            else:
                d = os.path.dirname(dest_abs_base)
                base_name = os.path.basename(dest_abs_base)
                stem, ext = os.path.splitext(base_name)
                src_stem = sanitize_component(os.path.splitext(os.path.basename(it["src"]))[0])
                safe_token = sanitize_component(args.duplicate_token)
                candidate = os.path.join(d, f"{stem}{safe_token}{src_stem}{ext}")

            # Ensure uniqueness against filesystem using numeric suffix only if needed
            unique_abs, final_n = compute_unique_destination(candidate, 1)
            # Rebuild relative path from final unique abs to keep reporting tidy
            unique_rel = os.path.relpath(unique_abs, dest_root)

            # Track duplicates report (list all in the group if group size > 1)
            if len(valid_items) > 1:
                duplicates_report.setdefault(key, []).append((it["src"], unique_rel))

            # Plan operation (skip no-op if source already equals destination)
            try:
                src_abs = os.path.abspath(it["src"])
                dest_abs = os.path.abspath(unique_abs)
            except Exception:
                src_abs = it["src"]
                dest_abs = unique_abs

            if src_abs == dest_abs:
                already_in_place += 1
                if args.verbose:
                    print(f"SKIP already in place: {it['src']}")
                continue

            action = "MOVE" if args.move else "COPY"
            if action == "MOVE":
                planned_moves += 1
            else:
                planned_copies += 1

            ops.append(
                {
                    "action": action,
                    "src": it["src"],
                    "dest_abs": unique_abs,
                    "dest_rel": unique_rel,
                    "size": it["size"],
                }
            )

        # Track how many distinct duplicates are kept for this group
        if len(valid_items) > 1 and unique_count > 1:
            total_duplicates_kept += unique_count - 1

    # Apply operations if requested
    copies_performed = 0
    moves_performed = 0

    if args.apply:
        for op in ops:
            src = op["src"]
            dest_abs = op["dest_abs"]
            dest_dir = os.path.dirname(dest_abs)

            # Ensure destination directory exists
            os.makedirs(dest_dir, exist_ok=True)

            if op["action"] == "MOVE":
                # Move is simpler; rename across filesystems handled by shutil.move
                shutil.move(src, dest_abs)
                moves_performed += 1
                if args.verbose:
                    print(f"MOVE {src} -> {dest_abs}")
            else:
                # COPY: copy2 to preserve metadata; never overwrite (dest is unique)
                # Use a temp file then atomic rename to avoid partial writes
                temp_path = dest_abs + ".incoming"
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                shutil.copy2(src, temp_path)
                os.replace(temp_path, dest_abs)
                copies_performed += 1
                if args.verbose:
                    print(f"COPY {src} -> {dest_abs}")
    else:
        # Dry run verbose output
        if args.verbose:
            for op in ops:
                print(f"PLAN {op['action']} {op['src']} -> {op['dest_abs']}")

    # Summary
    print("Summary:")
    print(f"  Total JSON entries:     {total_entries}")
    print(f"  Unique destinations:    {len(groups)}")
    print(f"  Missing source files:   {len(missing_sources)}")
    print(f"  Already in place:       {already_in_place}")
    print(f"  Duplicate groups:       {duplicate_groups}")
    print(f"  Distinct duplicates kept: {total_duplicates_kept}")
    print(f"  Identical duplicates skipped: {identical_duplicates_skipped}")
    print(f"  Will copy (planned):    {planned_copies}")
    print(f"  Will move (planned):    {planned_moves}")
    if args.apply:
        print(f"  Copied:                 {copies_performed}")
        print(f"  Moved:                  {moves_performed}")
    else:
        print("  Mode: DRY-RUN (use --apply to perform operations)")

    # Write duplicates report to JSON instead of printing to console
    duplicates_json_path = args.duplicates_json
    try:
        duplicates_dir = os.path.dirname(duplicates_json_path)
        if duplicates_dir:
            os.makedirs(duplicates_dir, exist_ok=True)
        groups_json = []
        for key in sorted(duplicates_report.keys()):
            entries_json = []
            for entry in duplicates_report[key]:
                if len(entry) == 3 and entry[2] == "skipped-identical":
                    entries_json.append({
                        "src": entry[0],
                        "status": "skipped-identical"
                    })
                else:
                    src, rel = entry[0], entry[1]
                    entries_json.append({
                        "src": src,
                        "planned_dest_rel": rel,
                        "planned_dest_abs": os.path.join(args.dest_root, rel),
                        "status": "planned"
                    })
            groups_json.append({"dest_key": key, "entries": entries_json})

        schema_ref = os.path.relpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas", "duplicates.schema.json"),
            start=os.path.dirname(os.path.abspath(duplicates_json_path))
        )
        report_json = {
            "$schema": schema_ref,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pattern": args.pattern,
            "dest_root": os.path.abspath(args.dest_root),
            "apply": bool(args.apply),
            "mode": "MOVE" if args.move else "COPY",
            "stats": {
                "duplicate_groups": duplicate_groups,
                "distinct_duplicates_kept": total_duplicates_kept,
                "identical_duplicates_skipped": identical_duplicates_skipped
            },
            "groups": groups_json,
        }
        with open(duplicates_json_path, "w", encoding="utf-8") as f:
            json.dump(report_json, f, indent=2, ensure_ascii=False)
        print(f"  Duplicates report JSON written to: {duplicates_json_path}")
    except Exception as e:
        print(f"WARNING: Failed to write duplicates report JSON: {e}", file=sys.stderr)

    # Optionally list missing sources
    if missing_sources:
        print("\nMissing sources (skipped):")
        for m in missing_sources[:50]:
            print(f"  - {m}")
        if len(missing_sources) > 50:
            print(f"  ... and {len(missing_sources) - 50} more")


if __name__ == "__main__":
    main()
