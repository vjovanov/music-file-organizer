#!/usr/bin/env python3
"""
Batch Shazam recognizer.

- Scans a folder for audio files and recognizes them via shazamio (Rust backend).
- Outputs a JSON mapping of "source file path" -> { author, album, song } for recognized tracks only.
- Supports limiting the number of processed files via --limit.
- Includes rate limiting to avoid being throttled or blocked.
- Prints defaults and effective configuration at start; writes an error log legend next to outputs.

Example usages:
  python batch_recognize.py /path/to/music
  python batch_recognize.py /path/to/music --limit 20        # process first 20 files
  python batch_recognize.py /path/to/music -o recognized-songs.json
  python batch_recognize.py /path/to/music --delay 1.5 --concurrency 1

Install dependency:
  pip install shazamio
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Any, List, Tuple

from shazamio import Shazam


AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wav",
    ".flac",
    ".wma",
    ".mp4",
    ".mkv",
}


# Defaults used across the CLI (also shown via --help)
DEFAULT_OUTPUT = "recognized-songs.json"
DEFAULT_DELAY = 1.5
DEFAULT_CONCURRENCY = 1
DEFAULT_NON_RECURSIVE = False
DEFAULT_LIMIT: Optional[int] = None
DEFAULT_DUMP_EVERY: Optional[int] = None

class RateLimiter:
    """
    Ensures a minimum time gap between calls across all workers.
    Works with any concurrency by serializing access to the 'wait' window.
    """

    def __init__(self, min_interval_seconds: float):
        self.min_interval = float(min_interval_seconds)
        self._lock = asyncio.Lock()
        self._last_ts = 0.0

    async def wait(self) -> None:
        loop = asyncio.get_event_loop()
        async with self._lock:
            now = loop.time()
            wait_for = self.min_interval - (now - self._last_ts)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            # Record the moment we release the limiter
            self._last_ts = loop.time()


def list_audio_files(root: Path, recursive: bool) -> List[Path]:
    if recursive:
        it = root.rglob("*")
    else:
        it = root.iterdir()
    return [
        p
        for p in it
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    ]


def extract_metadata(out: Dict[str, Any]) -> Optional[Dict[str, Optional[str]]]:
    """
    Try to extract {author, album, song} from shazamio response.
    Returns None if essential fields are missing.
    """
    track = (out or {}).get("track") or {}
    author = track.get("subtitle")
    song = track.get("title")

    album: Optional[str] = None
    for section in track.get("sections", []):
        if section.get("type") == "SONG":
            for md in section.get("metadata", []):
                if md.get("title") == "Album":
                    album = md.get("text")
                    break
        if album:
            break

    if not author or not song:
        return None

    return {"author": author, "album": album, "song": song}


async def recognize_file(shazam: Shazam, file_path: Path, limiter: RateLimiter, song_nr: int, total_songs: int) -> Tuple[Optional[Dict[str, Optional[str]]], Optional[str]]:
    # Respect global rate limiter
    await limiter.wait()

    # Run single-file recognizer in a subprocess to capture and prefix its stderr (ffmpeg/shazam native output)
    script_path = Path(__file__).parent / "recognize_one.py"
    cmd = [sys.executable, "-u", str(script_path), str(file_path)]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _forward_prefixed(reader: asyncio.StreamReader) -> None:
        file_name = file_path.name
        file_abs = str(file_path)
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                text = line.decode(errors="replace").rstrip("\n")
            except Exception:
                text = str(line).rstrip("\n")
            print(f"[WARNING] {song_nr}/{total_songs} {file_abs}: {text}", file=sys.stderr)

    # Forward child's stderr concurrently so messages are attributed live
    forward_task = asyncio.create_task(_forward_prefixed(proc.stderr))

    # Read full stdout (JSON) from child
    stdout_bytes = await proc.stdout.read()
    returncode = await proc.wait()
    await forward_task

    out_obj: Optional[Dict[str, Any]] = None
    if stdout_bytes:
        try:
            out_obj = json.loads(stdout_bytes.decode(errors="replace"))
        except Exception as e:
            # Could not parse JSON; treat as error
            err = f"ParseError: {type(e).__name__}: {e}"
            return None, err

    if returncode != 0:
        # Child indicated an error; we may still have out_obj, but treat as error for robustness
        return None, f"ChildExit {returncode}"

    info = extract_metadata(out_obj or {})
    return info, None


def atomic_write_json(path: Path, data: Any) -> None:
    """
    Atomically write JSON to 'path' via a temp file + replace.
    Ensures parent directories exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(path))


async def run(args) -> int:
    folder = Path(args.folder).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"[ERROR] Folder does not exist or is not a directory: {folder}", file=sys.stderr)
        return 2

    recursive = not args.non_recursive
    files = list_audio_files(folder, recursive=recursive)

    # Apply limit if provided
    limit = args.limit

    # Resolve output path and display defaults/effective configuration
    output_path = Path(args.output).expanduser().resolve()
    print(
        f"[INFO] Defaults -> output={DEFAULT_OUTPUT}, limit={DEFAULT_LIMIT}, dump_every={DEFAULT_DUMP_EVERY}, delay={DEFAULT_DELAY}, "
        f"concurrency={DEFAULT_CONCURRENCY}, recursive={not DEFAULT_NON_RECURSIVE}",
        file=sys.stderr,
    )
    print(
        f"[INFO] Effective -> folder={folder}, output={output_path}, limit={limit}, dump_every={args.dump_every}, delay={args.delay}, "
        f"concurrency={args.concurrency}, recursive={recursive}",
        file=sys.stderr,
    )

    if limit is not None:
        files = files[: max(0, int(limit))]

    if not files:
        print("[INFO] No audio files found to process.", file=sys.stderr)
        return 0

    # Initialize client & rate limiter
    shazam = Shazam()
    limiter = RateLimiter(args.delay)

    total = len(files)
    results: Dict[str, Dict[str, Optional[str]]] = {}
    errors: List[Dict[str, str]] = []
    unrecognized: List[Tuple[str, str]] = []
    processed = 0
    recognized_count = 0
    error_count = 0
    progress_lock = asyncio.Lock()
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))

    async def worker(idx: int, p: Path):
        nonlocal processed, recognized_count, error_count
        async with sem:
            meta, err = await recognize_file(shazam, p, limiter, idx, total)
            async with progress_lock:
                if meta:
                    results[str(p)] = meta
            if err:
                async with progress_lock:
                    errors.append({"file": str(p), "error": err})
            # Update progress counters and print progress
            async with progress_lock:
                processed += 1
                if meta:
                    recognized_count += 1
                else:
                    # Track unrecognized files (either due to error or no match)
                    unrecognized.append((str(p), err or "NO_MATCH"))
                if err:
                    error_count += 1
                percent = (processed / total) * 100 if total else 100.0
                status = "OK" if meta else ("ERROR" if err else "NO_MATCH")
                if meta:
                    details = f" -> {meta.get('author','?')} - {meta.get('song','?')}"
                elif err:
                    details = f" - {err}"
                else:
                    details = " - NO_MATCH"
                print(f"[PROGRESS] {processed}/{total} ({percent:.1f}%) {status}: {p}{details}", file=sys.stderr)
                if args.dump_every and int(args.dump_every) > 0 and (processed % int(args.dump_every) == 0):
                    try:
                        atomic_write_json(output_path, results)
                        print(f"[INFO] Checkpoint: wrote {output_path} with {len(results)} entries after {processed}/{total} processed.", file=sys.stderr)
                    except Exception as e:
                        print(f"[WARNING] Failed to write checkpoint to {output_path}: {type(e).__name__}: {e}", file=sys.stderr)

    await asyncio.gather(*(worker(i, p) for i, p in enumerate(files, start=1)))

    # Write output JSON
    atomic_write_json(output_path, results)

    # Write errors log (JSON Lines: one object per line: {"file": "...", "error": "..."})
    errors_path = output_path.with_name(output_path.stem + ".errors.jsonl")
    errors_path.parent.mkdir(parents=True, exist_ok=True)
    with errors_path.open("w", encoding="utf-8") as ef:
        for entry in errors:
            json.dump(entry, ef, ensure_ascii=False)
            ef.write("\n")

    # Write unrecognized text file (one line per file: "<path>\t<reason>")
    unrec_path = output_path.with_name(output_path.stem + ".unrecognized.txt")
    unrec_path.parent.mkdir(parents=True, exist_ok=True)
    with unrec_path.open("w", encoding="utf-8") as uf:
        for path_str, reason in unrecognized:
            uf.write(f"{path_str}\t{reason}\n")

    # Write error log explanation/legend
    legend_path = output_path.with_name(output_path.stem + ".errors.README.txt")
    legend_path.parent.mkdir(parents=True, exist_ok=True)
    legend_text = (
        "Error logs explanation (JSON Lines): one JSON object per line with keys 'file' and 'error'.\n"
        "Legend:\n"
        " - ChildExit <code>: recognize_one.py (Shazam/ffmpeg) exited non-zero. Check the per-file\n"
        "   stderr lines (prefixed with the source file path) printed during processing for details.\n"
        "   Common causes: network/transient API errors, decoding issues, or service throttling.\n"
        " - ParseError <Type>: could not parse JSON from the recognizer subprocess; this typically means\n"
        "   unexpected output was produced. Inspect the prefixed stderr lines for context.\n"
        " - NO_MATCH: no track match was found for the file.\n"
        "\n"
        "Artifacts produced by this run:\n"
        f" - Results JSON: {output_path}\n"
        f" - Errors JSONL: {errors_path}\n"
        f" - Unrecognized TXT: {unrec_path}\n"
        "\n"
        "Each stderr line from the recognizer subprocess is prefixed with the source file path in square brackets,\n"
        "so you can correlate low-level messages with entries in the logs. Use --delay and --concurrency to tune\n"
        "rate limiting and throughput.\n"
    )
    with legend_path.open("w", encoding="utf-8") as lf:
        lf.write(legend_text)

    print(
        f"[INFO] Recognized {recognized_count} / {len(files)} files. Errors: {error_count}. "
        f"Wrote results to: {output_path}. Unrecognized: {len(unrecognized)} -> {unrec_path}. "
        f"Error log: {errors_path}. Legend: {legend_path}"
    )
    return 0


def parse_args():
    ap = argparse.ArgumentParser(
        description="Batch recognize songs in a folder using Shazam (shazamio).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("folder", help="Folder containing audio files to scan")
    ap.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT,
        help="Output JSON file",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N files",
    )
    ap.add_argument(
        "--dump-every",
        type=int,
        default=DEFAULT_DUMP_EVERY,
        metavar="N",
        help="Write partial results JSON every N processed files (atomic). Disabled if not set or <= 0.",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="Minimum seconds between Shazam requests across all workers",
    )
    ap.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Max concurrent recognitions (still throttled by --delay).",
    )
    ap.add_argument(
        "--non-recursive",
        action="store_true",
        help="Scan only the top-level of the folder (default scans recursively)",
    )
    return ap.parse_args()


def main():
    args = parse_args()
    try:
        code = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("[INFO] Interrupted by user.", file=sys.stderr)
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
