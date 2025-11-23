#!/usr/bin/env python3
import sys
import json
import asyncio
from shazamio import Shazam


async def _recognize_once(file_path: str) -> int:
    try:
        sh = Shazam()
        out = await sh.recognize(file_path)
    except Exception as e:
        # Let ffmpeg/shazam native stderr bubble up; provide a clear summary line too.
        print(f"[WARN] Recognize failed for {file_path}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # Emit only JSON on stdout; no extra prints here so the parent can parse cleanly.
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: recognize_one.py <audio_file>", file=sys.stderr)
        return 2
    file_path = sys.argv[1]
    return asyncio.run(_recognize_once(file_path))


if __name__ == "__main__":
    sys.exit(main())
