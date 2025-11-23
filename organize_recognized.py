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
- Duplicates (same sanitized author/album/song) are all kept by suffixing the
  filename with _n (e.g., "Song.mp3", "Song_2.mp3", "Song_3.mp3", ...).
- Prints a detailed list of detected duplicates.
- Skips missing sources and reports them.
- Produces stable, cross-platform-safe path components via sanitization.

Testing notes (from AGENTS.md):
- Default destination root is current directory '.'. Examples use './build' for safety in docs/tests.
- Never modifies the input music directory unless you explicitly set --dest-root.
"""

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from typing import Dict, List, Tuple


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


def load_mapping(path: str) -> Dict[str, dict]:
    """Load the recognized-songs mapping from JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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


def build_dest_rel_base(replacements: Dict[str, str], ext: str, pattern: str) -> str:
    """
    Render the destination relative path (including extension) from a pattern.

    - Replaces placeholders using the provided replacements mapping (e.g., %A, %L, %S, %Y, %G, %B, %I, %E, %e, %a, %T, %U)
    - Splits on "/" to form directories
    - Sanitizes each path component
    - Appends the original file extension to the final leaf name
    """
    sub = pattern
    for k, v in replacements.items():
        sub = sub.replace(k, v)
    parts = [p for p in sub.split("/") if p not in ("", ".", "..")]
    safe_parts = [sanitize_component(p) for p in parts]
    if not safe_parts:
        # Fallback to core triplet
        fallback = (
            replacements.get("%A", "Unknown"),
            replacements.get("%L", "Unknown"),
            replacements.get("%S", "Unknown"),
        )
        safe_parts = [sanitize_component(x) for x in fallback]
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
            "- Duplicates are kept with _n suffix and listed."
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
        help=("Output path pattern using placeholders: %A=artist, %L=album, %S=song, "
              "%Y=year, %G=genre, %B=label, %I=ISRC, %E=Explicit|Clean, %e=true|false, "
              "%a=artist adamid, %T=Apple Music track id, %U=Apple Music album id. "
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

    sources = list(mapping.keys())

    # Group entries by destination relative path (case-insensitive)
    groups: Dict[str, List[dict]] = defaultdict(list)
    missing_sources: List[str] = []
    total_entries = 0

    for src, meta in mapping.items():
        total_entries += 1
        if not isinstance(meta, dict):
            # Skip malformed meta entries
            continue

        author = meta.get("author") or "Unknown Artist"
        album = meta.get("album") or "Unknown Album"
        song = meta.get("song") or "Unknown Song"

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
        dest_rel_base = build_dest_rel_base(replacements, ext, args.pattern)

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

    # Plan operations: for each group, keep ALL occurrences
    # First occurrence: Song.ext
    # Subsequent occurrences: Song_2.ext, Song_3.ext, ...
    ops = []  # planned operations for valid (non-missing) sources
    duplicates_report = {}  # key -> list of (src, assigned_rel)
    duplicate_groups = 0
    total_duplicates = 0

    already_in_place = 0
    planned_copies = 0
    planned_moves = 0

    for key, items in groups.items():
        # Any group with more than one valid entry is a duplicates group
        # But we only count duplicates among files that actually exist
        valid_items = [i for i in items if i["size"] >= 0]
        if len(valid_items) > 1:
            duplicate_groups += 1
            total_duplicates += len(valid_items) - 1

        # index within the group (1 => base name, >=2 => suffix)
        group_index = 0
        for it in valid_items:
            group_index += 1

            base_rel = it["dest_rel_base"]
            dest_abs_base = os.path.join(dest_root, base_rel)

            # Pick starting suffix based on the ordinal within group
            # Then ensure uniqueness against filesystem (so we never overwrite)
            desired_n = group_index
            unique_abs, final_n = compute_unique_destination(dest_abs_base, desired_n)
            # Rebuild relative path from final unique abs to keep reporting tidy
            unique_rel = os.path.relpath(unique_abs, dest_root)

            # Track duplicates report (list all in the group if group size > 1)
            if len(valid_items) > 1:
                if key not in duplicates_report:
                    duplicates_report[key] = []
                duplicates_report[key].append((it["src"], unique_rel))

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
    print(f"  Total duplicate files:  {total_duplicates}")
    print(f"  Will copy (planned):    {planned_copies}")
    print(f"  Will move (planned):    {planned_moves}")
    if args.apply:
        print(f"  Copied:                 {copies_performed}")
        print(f"  Moved:                  {moves_performed}")
    else:
        print("  Mode: DRY-RUN (use --apply to perform operations)")

    # Print duplicates list (as requested)
    if duplicates_report:
        print("\nDuplicates detected and their planned destination names:")
        # Keyed by final destination base path (case-insensitive)
        for key, entries in duplicates_report.items():
            display_key = key
            print(f"  - {display_key}:")
            for idx, (src, rel) in enumerate(entries, start=1):
                print(f"      {idx}) {src} -> {os.path.join(args.dest_root, rel)}")

    # Optionally list missing sources
    if missing_sources:
        print("\nMissing sources (skipped):")
        for m in missing_sources[:50]:
            print(f"  - {m}")
        if len(missing_sources) > 50:
            print(f"  ... and {len(missing_sources) - 50} more")


if __name__ == "__main__":
    main()
