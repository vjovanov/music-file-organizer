#!/usr/bin/env python3
"""
Test stub for recognize_one.py used by batch_recognize tests.

Behavior:
- If the input file name ends with 'recognized_song.mp3' and tests/data/recognized_song.json exists,
  emit the snapshot JSON to stdout.
- Otherwise, emit {"matches": []}.
- Exit code 0 on success; no extra stdout noise so the parent can parse cleanly.
"""

import sys
import json
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: recognize_stub.py <audio_file>", file=sys.stderr)
        return 2

    file_path = sys.argv[1]
    try:
        root = Path(__file__).resolve().parents[2]  # repo root
        data_dir = root / "tests" / "data"
        recognized_snap = data_dir / "recognized_song.json"

        if file_path.endswith("recognized_song.mp3") and recognized_snap.exists():
            with recognized_snap.open("r", encoding="utf-8") as f:
                sys.stdout.write(f.read())
            sys.stdout.flush()
            return 0
        else:
            sys.stdout.write(json.dumps({"matches": []}, ensure_ascii=False))
            sys.stdout.flush()
            return 0
    except Exception as e:
        print(f"[WARN] recognize_stub failed for {file_path}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
