"""
Test stub module for 'shazamio' used by tests to avoid real network/service calls.

Provides a Shazam class with an async recognize(file_path) method that returns:
- The snapshot JSON from tests/data/recognized_song.json if the file name ends with 'recognized_song.mp3' and the snapshot exists.
- Otherwise, returns {"matches": []}.
"""

import json
from pathlib import Path
from typing import Any, Dict


class Shazam:
    async def recognize(self, file_path: str) -> Dict[str, Any]:
        root = Path(__file__).resolve().parents[2]  # repo root
        data_dir = root / "tests" / "data"
        snap = data_dir / "recognized_song.json"

        if Path(file_path).name == "recognized_song.mp3" and snap.exists():
            with snap.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {"matches": []}
