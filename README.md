# Music File Organizer (Shazam-based) 🎵

Recognize your music files using Shazam-based recognition, then copy or move files into a clean Artist/Album/Song (or similar) structure.

## 🚀 Quick Start

You will need the `shazamio` python package:

```bash
pip install shazamio
```

### 1) 🔎 Recognize your music library into JSON

- Default output: `recognized-songs.json` (current directory)
- Recurses into subfolders by default

Examples:

```bash
# Recognize everything
python batch_recognize.py "/path/to/music"
```

```bash
# Limit to first 50 files
python batch_recognize.py "/path/to/music" --limit 50
```

```bash
# Non-recursive (top-level only)
python batch_recognize.py "/path/to/music" --non-recursive
```

```bash
# Periodic checkpoints
python batch_recognize.py "/path/to/music" --dump-every 50
```

Performance and API friendliness:

- `--delay SECONDS`  Minimum seconds between Shazam requests (default: `1.5`)
- `-c N`             Max concurrent recognitions (default: `1`). Calls are still throttled by `--delay`.

---

### 2) 🗂️ Organize files by Artist/Album/Song (dry-run by default)

- Copies recognized files into a folder layout you control using a pattern
- Default destination root is current directory `.` (copy mode and dry-run by default)
- Use `--apply` to actually copy/move files

Examples:

```bash
# Plan COPY (dry-run)
python organize_recognized.py -i recognized-songs.json -d . -p "%A/%L/%S" -v
```

```bash
# Apply COPY
python organize_recognized.py -i recognized-songs.json -d . -p "%A/%L/%S" --apply
```

```bash
# Apply MOVE instead of copy
python organize_recognized.py -i recognized-songs.json -d . -p "%A/%L/%S" --apply --move
```

Pattern placeholders 🧩:

- `%A` artist, `%L` album, `%S` song
- `%Y` year, `%G` genre, `%B` label, `%I` ISRC
- `%E` Explicit|Clean, `%e` true|false
- `%a` artist adamid, `%T` Apple Music track id, `%U` Apple Music album id

---

## ⚙️ Install

Requires: Python 3.8+ and `shazamio`

Recommended (virtualenv):

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install shazamio
```

---

## 💾 What gets written

- Results JSON (default): `recognized-songs.json`
- Next to it:
  - `recognized.errors.jsonl` — one JSON object per line: `{ "file", "error" }`
  - `recognized.unrecognized.txt` — one line per path: `"<absolute_path>\t<reason>"`
  - `recognized.errors.README.txt` — short legend for interpreting errors

Notes ℹ️

- Only recognized files are included in the results JSON.
- The program prints progress to `stderr`, including live warnings from the recognizer backend.

---

## 🎧 Supported audio formats

`.mp3`, `.m4a`, `.aac`, `.ogg`, `.opus`, `.wav`, `.flac`, `.wma`, `.mp4`, `.mkv`

---

## 🧭 CLI reference (concise)

### batch_recognize.py

- Positional:
  - `folder` — Folder containing audio files to scan

- Options:

```text
-o, --output PATH              Output JSON file (default: recognized-songs.json)
--limit N                      Process only the first N files
--dump-every N                 Write partial results every N processed files
--delay SECONDS                Min seconds between Shazam requests (default: 1.5)
-c, --concurrency N            Max concurrent recognitions (default: 1)
--non-recursive                Scan only the top-level (default scans recursively)
```

### organize_recognized.py

- Required:
  - `-i, --input FILE` — Path to recognized JSON (e.g., `recognized-songs.json`)
- Options:

```text
-d, --dest-root DIR            Destination root (default: .)
-p, --pattern STR              Pattern (default: "%A/%L/%S")
--apply                        Perform the copy/move (otherwise dry-run)
--move                         Move instead of copy
-v, --verbose                  Print per-file actions
```

---

## ✅ Validate output (optional)

A JSON Schema is provided in `recognized.schema.json`.

Quick check:

```bash
pip install jsonschema
python - << 'PY'
import json
from jsonschema import validate, Draft202012Validator
with open("recognized-songs.json","r",encoding="utf-8") as f: data = json.load(f)
with open("recognized.schema.json","r",encoding="utf-8") as f: schema = json.load(f)
Draft202012Validator.check_schema(schema)
validate(instance=data, schema=schema)
print("OK: recognized-songs.json validates against recognized.schema.json")
PY
```

## 🧪 Validate Shazam input (optional)

A JSON Schema is provided in `shazam_input.schema.json`. A sample Shazam payload is included at `build/shazam.sample.json`.

Quick check:

```bash
pip install jsonschema
python - << 'PY'
import json
from jsonschema import validate, Draft202012Validator
with open("build/shazam.sample.json","r",encoding="utf-8") as f: data = json.load(f)
with open("shazam_input.schema.json","r",encoding="utf-8") as f: schema = json.load(f)
Draft202012Validator.check_schema(schema)
validate(instance=data, schema=schema)
print("OK: build/shazam.sample.json validates against shazam_input.schema.json")
PY
```

---

## 🐞 Troubleshooting

- `ModuleNotFoundError: No module named 'shazamio'`

  ```bash
  pip install shazamio
  ```

- Few or no matches:
  - Increase `--delay`, keep `-c` low (e.g., `1`), and re-run later.
  - Network or service throttling can affect results.

---

## 🛡️ Safety defaults

- `batch_recognize.py` writes to the current directory by default: `recognized-songs.json` and companion files.
- `organize_recognized.py` defaults to COPY and to `dest-root=.`.
- It never modifies your source library unless you use `--apply` (and even then COPY is the default; use `--move` to relocate files).
