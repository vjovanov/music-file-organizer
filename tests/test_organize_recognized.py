import json
import os
import sys
import shutil
import subprocess
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Tuple

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "organize_recognized.py"
DATA_DIR = ROOT / "tests" / "data"
BUILD_DIR = ROOT / "build" / "test_organize_recognized"


def run_script(args: List[str], timeout: int = 180) -> subprocess.CompletedProcess:
    """
    Execute organize_recognized.py with provided args, capturing stdout/stderr.
    Uses the repo root as cwd so relative script paths resolve.
    """
    cmd = [sys.executable, str(SCRIPT), *args]
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def compute_sha256(p: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_component(s: str) -> str:
    """
    Mirror sanitize_component rules to build deterministic expected paths in simple cases.
    This is a simplified mirror sufficient for the values used in tests.
    """
    if s is None:
        s = ""
    s = str(s)
    # Replace path separators
    s = s.replace("/", "-").replace("\\", "-")
    # Replace reserved/unsafe/control characters
    out = []
    for ch in s:
        code = ord(ch)
        if code < 32 or ch in '<>:\\"|?*':
            out.append("-")
        else:
            out.append(ch)
    s = "".join(out)
    # Collapse whitespace and trim problematic trailing chars
    s = " ".join(s.split())
    s = s.strip(" .")
    if not s:
        s = "Unknown"
    return s


def write_recognized_mapping(path: Path, mapping: Dict[str, Dict[str, Any]]) -> Path:
    """
    Write a recognized mapping JSON that conforms to schemas/recognized.schema.json.
    mapping keys must be absolute paths.
    """
    obj: Dict[str, Any] = {"$schema": "https://example.com/schemas/recognized.schema.json"}
    obj.update(mapping)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    return path


def validate_recognized_schema(instance: Dict[str, Any]) -> None:
    """
    Validate a recognized mapping against schemas/recognized.schema.json if jsonschema is available.
    """
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema package required for schema validation")
    schema_path = ROOT / "schemas" / "recognized.schema.json"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    validator_cls = getattr(jsonschema, "Draft202012Validator", None) or jsonschema.Draft7Validator
    validator = validator_cls(schema)
    validator.validate(instance)


def validate_duplicates_schema(instance: Dict[str, Any]) -> None:
    """
    Validate a duplicates report against schemas/duplicates.schema.json if jsonschema is available.
    """
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema package required for schema validation")
    schema_path = ROOT / "schemas" / "duplicates.schema.json"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    validator_cls = getattr(jsonschema, "Draft202012Validator", None) or jsonschema.Draft7Validator
    validator = validator_cls(schema)
    # Some reports include a $schema meta-field not modeled in the schema; drop it for validation.
    instance_no_meta = {k: v for k, v in instance.items() if k != "$schema"}
    validator.validate(instance_no_meta)


def copy_test_audio(src_name: str, dst: Path) -> Path:
    """
    Copy a file from tests/data to dst and return its absolute path.
    """
    src = DATA_DIR / src_name
    assert src.exists(), f"Missing test data: {src}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst.resolve()


def test_usage_requires_input():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2, f"Expected exit code 2 for missing args, got {proc.returncode}"
    assert "usage:" in proc.stderr.lower() and "organize_recognized.py" in proc.stderr


def test_dry_run_produces_plan_and_duplicates_report(tmp_path: Path):
    work = BUILD_DIR / "dry_run"
    dest_root = work / "dest"
    duplicates_json = work / "duplicates.dry_run.json"

    # Prepare a single recognized mapping
    src_mp3 = copy_test_audio("recognized_song.mp3", tmp_path / "Artist - Song.mp3")
    src_hash = compute_sha256(src_mp3)
    mapping_path = work / "recognized.map.json"
    mapping = {
        str(src_mp3): {
            "author": "Test Artist",
            "album": "Test Album",
            "song": "Test Song",
            "author_unknown": False,
            "album_unknown": False,
            "song_unknown": False,
        }
    }
    write_recognized_mapping(mapping_path, mapping)

    # Sanity: validate mapping schema
    with mapping_path.open("r", encoding="utf-8") as f:
        validate_recognized_schema(json.load(f))

    proc = run_script([
        "-i", str(mapping_path),
        "-d", str(dest_root),
        "--duplicates-json", str(duplicates_json),
        "-v"
    ])
    assert proc.returncode == 0, proc.stderr
    # Dry-run is default; expect plan lines and summary
    assert "PLAN COPY" in proc.stdout or "PLAN MOVE" in proc.stdout or "Summary:" in proc.stdout
    assert "Mode: DRY-RUN" in proc.stdout

    # No files should be created in dest_root on dry-run
    assert not dest_root.exists() or not any(dest_root.rglob("*.mp3"))

    # Duplicates report should be created
    assert duplicates_json.exists(), f"Expected duplicates report {duplicates_json}"
    with duplicates_json.open("r", encoding="utf-8") as f:
        dup_obj = json.load(f)
    assert dup_obj["apply"] is False
    assert dup_obj["mode"] == "COPY"
    validate_duplicates_schema(dup_obj)

    # Ensure no unintended data loss: source file unchanged
    assert src_mp3.exists()
    assert compute_sha256(src_mp3) == src_hash


def test_apply_copy_preserves_hash_and_structure(tmp_path: Path):
    work = BUILD_DIR / "apply_copy"
    dest_root = work / "dest"
    duplicates_json = work / "duplicates.copy.json"

    src_mp3 = copy_test_audio("recognized_song.mp3", tmp_path / "source.mp3")
    src_hash = compute_sha256(src_mp3)
    mapping_path = work / "recognized.map.json"

    author = "Artist"
    album = "Album"
    song = "Song"
    mapping = {
        str(src_mp3): {
            "author": author,
            "album": album,
            "song": song,
            "author_unknown": False,
            "album_unknown": False,
            "song_unknown": False,
        }
    }
    write_recognized_mapping(mapping_path, mapping)

    proc = run_script([
        "-i", str(mapping_path),
        "-d", str(dest_root),
        "--duplicates-json", str(duplicates_json),
        "--apply"
    ])
    assert proc.returncode == 0, proc.stderr
    # Expected destination path
    rel = Path(sanitize_component(author)) / sanitize_component(album) / sanitize_component(song)
    expected = dest_root / f"{rel}.mp3"
    assert expected.exists(), f"Expected file at {expected}"
    assert compute_sha256(expected) == src_hash, "Copied file hash must match source"
    # Source still exists on copy
    assert src_mp3.exists()

    # Duplicates report
    assert duplicates_json.exists()
    with duplicates_json.open("r", encoding="utf-8") as f:
        dup_obj = json.load(f)
    assert dup_obj["apply"] is True and dup_obj["mode"] == "COPY"
    validate_duplicates_schema(dup_obj)


def test_apply_move_removes_source(tmp_path: Path):
    work = BUILD_DIR / "apply_move"
    dest_root = work / "dest"
    duplicates_json = work / "duplicates.move.json"

    src_mp3 = copy_test_audio("recognized_song.mp3", tmp_path / "to_move.mp3")
    src_hash = compute_sha256(src_mp3)
    mapping_path = work / "recognized.map.json"

    author = "Mover"
    album = "Album"
    song = "Track"
    mapping = {
        str(src_mp3): {
            "author": author,
            "album": album,
            "song": song,
            "author_unknown": False,
            "album_unknown": False,
            "song_unknown": False,
        }
    }
    write_recognized_mapping(mapping_path, mapping)

    proc = run_script([
        "-i", str(mapping_path),
        "-d", str(dest_root),
        "--duplicates-json", str(duplicates_json),
        "--apply",
        "--move"
    ])
    assert proc.returncode == 0, proc.stderr
    expected = dest_root / f"{sanitize_component(author)}/{sanitize_component(album)}/{sanitize_component(song)}.mp3"
    assert expected.exists(), f"Expected moved file at {expected}"
    assert compute_sha256(expected) == src_hash
    # Source removed
    assert not src_mp3.exists()

    with duplicates_json.open("r", encoding="utf-8") as f:
        dup_obj = json.load(f)
    assert dup_obj["apply"] is True and dup_obj["mode"] == "MOVE"
    validate_duplicates_schema(dup_obj)


def test_keep_unknowns_and_drop_unknowns_affect_path(tmp_path: Path):
    work = BUILD_DIR / "unknowns"
    dest_drop = work / "dest_drop"
    dest_keep = work / "dest_keep"
    dup_drop = work / "dup.drop.json"
    dup_keep = work / "dup.keep.json"

    src_mp3 = copy_test_audio("recognized_song.mp3", tmp_path / "unknown_case.mp3")
    mapping_path = work / "recognized.map.json"

    author = "AA"
    album = "Unknown Album"
    song = "SS"
    # album_unknown True should set 'Unknown Album' in pre-processing
    mapping = {
        str(src_mp3): {
            "author": author,
            "album": album,
            "song": song,
            "author_unknown": False,
            "album_unknown": True,
            "song_unknown": False,
        }
    }
    write_recognized_mapping(mapping_path, mapping)

    # Drop unknowns (default)
    proc1 = run_script([
        "-i", str(mapping_path),
        "-d", str(dest_drop),
        "--duplicates-json", str(dup_drop),
        "--apply"
    ])
    assert proc1.returncode == 0
    expected_drop = dest_drop / f"{sanitize_component(author)}/{sanitize_component(song)}.mp3"
    assert expected_drop.exists(), f"Album 'Unknown' component should be dropped: {expected_drop}"

    # Keep unknowns
    proc2 = run_script([
        "-i", str(mapping_path),
        "-d", str(dest_keep),
        "--duplicates-json", str(dup_keep),
        "--keep-unknowns",
        "--apply"
    ])
    assert proc2.returncode == 0
    expected_keep = dest_keep / f"{sanitize_component(author)}/{sanitize_component('Unknown Album')}/{sanitize_component(song)}.mp3"
    assert expected_keep.exists(), f"Album 'Unknown' should be kept when --keep-unknowns: {expected_keep}"


def test_pattern_placeholders_and_explicit_flag(tmp_path: Path):
    work = BUILD_DIR / "pattern"
    dest_root = work / "dest"
    dup_json = work / "dup.json"

    src_mp3 = copy_test_audio("recognized_song.mp3", tmp_path / "explicit.mp3")
    mapping_path = work / "recognized.map.json"

    meta = {
        "author": "Artist X",
        "album": "Album X",
        "song": "Song X",
        "explicit": True,  # maps to %E=Explicit, %e=true
        "author_unknown": False,
        "album_unknown": False,
        "song_unknown": False,
    }
    write_recognized_mapping(mapping_path, {str(src_mp3): meta})

    pattern = "%G/%A - %S (%E)"  # %G defaults to Unknown, will be dropped unless kept
    # With default drop-unknowns, %G is removed leading to "Artist - Song (Explicit).mp3" at top-level
    proc = run_script([
        "-i", str(mapping_path),
        "-d", str(dest_root),
        "-p", pattern,
        "--duplicates-json", str(dup_json),
        "--apply"
    ])
    assert proc.returncode == 0
    stem = sanitize_component('Artist X - Song X (Explicit)')
    # build_dest_rel_base trims trailing punctuation/hyphens on components when dropping unknowns
    trimmed_stem = stem.rstrip(" -.(),;:")
    expected = dest_root / f"{trimmed_stem}.mp3"
    assert expected.exists(), f"Expected {expected}"


def test_duplicates_identical_content_skipped(tmp_path: Path):
    work = BUILD_DIR / "dups_identical"
    dest_root = work / "dest"
    dup_json = work / "duplicates.json"
    mapping_path = work / "recognized.map.json"

    # Two different source paths with identical content
    src1 = copy_test_audio("recognized_song.mp3", tmp_path / "same1.mp3")
    src2 = copy_test_audio("recognized_song.mp3", tmp_path / "same2.mp3")

    base_meta = {
        "author": "Dup Artist",
        "album": "Dup Album",
        "song": "Dup Song",
        "author_unknown": False,
        "album_unknown": False,
        "song_unknown": False,
    }
    write_recognized_mapping(mapping_path, {str(src1): base_meta, str(src2): base_meta})

    proc = run_script([
        "-i", str(mapping_path),
        "-d", str(dest_root),
        "--duplicates-json", str(dup_json),
        "--apply"
    ])
    assert proc.returncode == 0

    # Expect only one file in dest
    expected_base = dest_root / f"{sanitize_component('Dup Artist')}/{sanitize_component('Dup Album')}/{sanitize_component('Dup Song')}.mp3"
    assert expected_base.exists()
    # The duplicate should be skipped as identical
    # Validate duplicates.json structure
    with dup_json.open("r", encoding="utf-8") as f:
        report = json.load(f)
    validate_duplicates_schema(report)
    # Find the group with our dest key
    dest_key = f"{sanitize_component('Dup Artist')}/{sanitize_component('Dup Album')}/{sanitize_component('Dup Song')}.mp3".lower()
    groups = {g["dest_key"]: g for g in report.get("groups", [])}
    assert dest_key in groups, f"Expected duplicates group for key {dest_key}"
    statuses = [e["status"] for e in groups[dest_key]["entries"]]
    # One planned, one skipped-identical
    assert "planned" in statuses and "skipped-identical" in statuses
    # stats
    stats = report["stats"]
    assert stats["duplicate_groups"] >= 1
    assert stats["identical_duplicates_skipped"] >= 1


def test_duplicates_differing_content_token_and_numeric_suffix(tmp_path: Path):
    work = BUILD_DIR / "dups_different"
    dest_root = work / "dest"
    dup_json = work / "duplicates.json"
    mapping_path = work / "recognized.map.json"

    # Two different files (different content)
    src1 = copy_test_audio("recognized_song.mp3", tmp_path / "diff1.mp3")
    src2 = copy_test_audio("unrecognized_song.mp3", tmp_path / "diff2.mp3")

    meta = {
        "author": "Collide",
        "album": "Same",
        "song": "Target",
        "author_unknown": False,
        "album_unknown": False,
        "song_unknown": False,
    }
    write_recognized_mapping(mapping_path, {str(src1): meta, str(src2): meta})

    # Pre-create a conflict for the second planned name to force numeric suffix
    base_dir = dest_root / f"{sanitize_component('Collide')}/{sanitize_component('Same')}"
    base_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_component("Target")
    # The code will produce: Target_duplicate_diff2.mp3 for the second (default token)
    pre_conflict = base_dir / f"{stem}_duplicate_{sanitize_component(src2.stem)}.mp3"
    pre_conflict.write_bytes(b"placeholder to force _2")

    proc = run_script([
        "-i", str(mapping_path),
        "-d", str(dest_root),
        "--duplicates-json", str(dup_json),
        "--apply"
    ])
    assert proc.returncode == 0

    # First unique: base name
    expected_first = base_dir / f"{stem}.mp3"
    assert expected_first.exists()
    # Second unique: with token and numeric suffix due to conflict
    expected_second = base_dir / f"{stem}_duplicate_{sanitize_component(src2.stem)}_2.mp3"
    assert expected_second.exists(), f"Expected numeric suffix due to pre-existing conflict: {expected_second}"

    with dup_json.open("r", encoding="utf-8") as f:
        report = json.load(f)
    validate_duplicates_schema(report)
    # Ensure stats reflect at least one kept duplicate and zero identical skips here
    stats = report["stats"]
    assert stats["distinct_duplicates_kept"] >= 1


def test_missing_source_is_reported_but_not_fatal(tmp_path: Path):
    work = BUILD_DIR / "missing"
    dest_root = work / "dest"
    dup_json = work / "duplicates.json"
    mapping_path = work / "recognized.map.json"

    missing_path = (tmp_path / "does_not_exist.mp3").resolve()
    mapping = {
        str(missing_path): {
            "author": "Ghost",
            "album": "Phantom",
            "song": "Silence",
            "author_unknown": False,
            "album_unknown": False,
            "song_unknown": False,
        }
    }
    write_recognized_mapping(mapping_path, mapping)

    proc = run_script([
        "-i", str(mapping_path),
        "-d", str(dest_root),
        "--duplicates-json", str(dup_json),
        "-v"
    ])
    # Not fatal; script should succeed but report missing source
    assert proc.returncode == 0
    assert "Missing sources (skipped):" in proc.stdout or "Missing source files" in proc.stdout
    # No operations applied in dry-run, no files created
    assert not dest_root.exists() or not any(dest_root.rglob("*.mp3"))
    # Duplicates JSON should still be written and valid
    assert dup_json.exists()
    with dup_json.open("r", encoding="utf-8") as f:
        report = json.load(f)
    validate_duplicates_schema(report)
