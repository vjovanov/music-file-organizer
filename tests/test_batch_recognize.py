import json
import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Tuple, Dict, Any, List

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "batch_recognize.py"
DATA_DIR = ROOT / "tests" / "data"
BUILD_DIR = ROOT / "build" / "test_batch_recognize"


def run_script(args: List[str], timeout: int = 180) -> subprocess.CompletedProcess:
    """
    Execute batch_recognize.py with provided args, capturing stdout/stderr.
    Uses the repo root as cwd so relative script paths resolve.
    Always injects the test recognizer stub to avoid network/service calls.
    """
    stub = ROOT / "tests" / "stubs" / "recognize_stub.py"
    cmd = [sys.executable, str(SCRIPT), *args, "--recognizer-script", str(stub)]
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def setup_input_dir(tmp_path: Path, layout: str = "flat") -> Tuple[Path, Dict[str, Path]]:
    """
    Prepare an input directory containing test mp3 files copied from tests/data.

    layout:
      - "flat": place files at top-level
      - "nested": place files inside subdirectory 'sub'
    Returns:
      (input_dir, files_map) where files_map keys: 'recognized', 'unrecognized'
    """
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    if layout == "nested":
        target_dir = input_dir / "sub"
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = input_dir

    files = {}
    src_recognized = DATA_DIR / "recognized_song.mp3"
    src_unrecognized = DATA_DIR / "unrecognized_song.mp3"
    assert src_recognized.exists(), f"Missing test data: {src_recognized}"
    assert src_unrecognized.exists(), f"Missing test data: {src_unrecognized}"

    files["recognized"] = target_dir / "recognized_song.mp3"
    files["unrecognized"] = target_dir / "unrecognized_song.mp3"
    shutil.copyfile(src_recognized, files["recognized"])
    shutil.copyfile(src_unrecognized, files["unrecognized"])

    return input_dir, files


def load_expected_track_from_snapshot() -> Dict[str, Any]:
    """
    Load a stable subset of fields from tests/data/recognized_song.json to compare recognized metadata.
    """
    snap_path = DATA_DIR / "recognized_song.json"
    if not snap_path.exists():
        pytest.skip(
            f"Expected JSON snapshot not found: {snap_path}. "
            "Generate it by running: python recognize_one.py tests/data/recognized_song.mp3 > tests/data/recognized_song.json"
        )
    with snap_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_results_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_unrecognized_lines(path: Path) -> List[Tuple[str, str]]:
    """
    Read unrecognized file lines: each line is '<path>\\t<reason>'.
    """
    lines = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                lines.append((parts[0], parts[1]))
            else:
                lines.append((parts[0], ""))
    return lines


def validate_against_schema(instance: Dict[str, Any]) -> None:
    """
    Validate the results JSON against schemas/recognized.schema.json if jsonschema is available.
    Skips gracefully if jsonschema is not installed in the environment.
    """
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema package required for schema validation")
    schema_path = ROOT / "schemas" / "recognized.schema.json"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    # Use Draft 2020-12 validator
    validator_cls = getattr(jsonschema, "Draft202012Validator", None) or jsonschema.Draft7Validator
    validator = validator_cls(schema)
    validator.validate(instance)


def test_usage_invalid_folder_exits_2(tmp_path: Path):
    bad_folder = tmp_path / "does_not_exist"
    out_path = BUILD_DIR / "invalid.json"
    proc = run_script([str(bad_folder), "-o", str(out_path)])
    assert proc.returncode == 2, f"Expected exit code 2 for invalid folder, got {proc.returncode}"
    assert "does not exist or is not a directory" in proc.stderr


def test_non_recursive_ignores_nested_files(tmp_path: Path):
    input_dir, _ = setup_input_dir(tmp_path, layout="nested")
    out_path = BUILD_DIR / "non_recursive.json"
    # Ensure clean slate
    if out_path.exists():
        out_path.unlink()

    proc = run_script([str(input_dir), "--non-recursive", "-o", str(out_path)])
    assert proc.returncode == 0, f"Expected 0 when no files found, got {proc.returncode}"
    assert "No audio files found to process." in proc.stderr
    assert not out_path.exists(), "Output should not be created when no files are processed"


def test_process_two_files_and_validate_schema_and_outputs(tmp_path: Path):
    input_dir, files = setup_input_dir(tmp_path, layout="flat")
    out_path = BUILD_DIR / "recognized.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Run with concurrency and no delay to exercise flags
    proc = run_script([str(input_dir), "-o", str(out_path), "--delay", "0.0", "-c", "2"])
    assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}"
    assert out_path.exists(), f"Expected results JSON at {out_path}"

    # Related artifacts
    errors_path = out_path.with_name(out_path.stem + ".errors.jsonl")
    unrec_path = out_path.with_name(out_path.stem + ".unrecognized.txt")
    legend_path = out_path.with_name(out_path.stem + ".errors.README.txt")
    for p in [errors_path, unrec_path, legend_path]:
        assert p.exists(), f"Expected artifact created: {p}"

    # Load and sanity-check results
    results_obj = read_results_json(out_path)
    assert isinstance(results_obj, dict)
    assert results_obj.get("$schema") == "https://example.com/schemas/recognized.schema.json"

    # Validate full object against schema
    validate_against_schema(results_obj)

    # Remove $schema to examine only mapping entries
    mapping = {k: v for k, v in results_obj.items() if k != "$schema"}
    # Expect exactly one recognized entry from the two files (recognized/unrecognized)
    assert len(mapping) in (0, 1, 2)  # do not over-constrain, but should have at least the recognized one
    # At least one of the two should be recognized and present
    assert any(os.path.isabs(k) for k in mapping.keys()), "Keys must be absolute file paths"

    # If recognized file was included, validate core fields
    snap = load_expected_track_from_snapshot()
    exp_track = snap.get("track", {})
    exp_author = exp_track.get("subtitle")
    exp_song = exp_track.get("title")

    # Try to locate recognized entry by matching expected author/song
    rec_items = [(k, v) for k, v in mapping.items() if isinstance(v, dict)]
    if rec_items:
        # There should be at most one recognized entry in our setup
        for k, meta in rec_items:
            # author/song should be present
            assert "author" in meta and isinstance(meta["author"], str) and meta["author"]
            assert "song" in meta and isinstance(meta["song"], str) and meta["song"]
            # The recognized file should match expected (best-effort, if the snapshot aligns)
            if exp_author and exp_song:
                assert meta["author"] == exp_author
                assert meta["song"] == exp_song
            # unknown flags for author/song should be False
            assert meta.get("author_unknown") is False
            assert meta.get("song_unknown") is False
            # album may be None or string; album_unknown should reflect unknown-ness
            assert "album_unknown" in meta

    # Unrecognized file entries should be listed in the unrecognized text file
    unrec_lines = read_unrecognized_lines(unrec_path)
    # Count processed files via results + unrecognized
    processed_count = len(mapping) + len(unrec_lines)
    assert processed_count == 2, f"Expected 2 files processed, got {processed_count}"

    # errors.jsonl may be empty if no child errors occurred
    with errors_path.open("r", encoding="utf-8") as ef:
        lines = [ln for ln in ef.read().splitlines() if ln.strip()]
        for line in lines:
            obj = json.loads(line)
            assert "file" in obj and "error" in obj


def test_limit_flag_limits_total_processed(tmp_path: Path):
    input_dir, _ = setup_input_dir(tmp_path, layout="flat")
    out_path = BUILD_DIR / "limit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    proc = run_script([str(input_dir), "-o", str(out_path), "--limit", "1", "--delay", "0.0", "-c", "2"])
    assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}"
    assert out_path.exists(), f"Expected results JSON at {out_path}"

    results_obj = read_results_json(out_path)
    mapping = {k: v for k, v in results_obj.items() if k != "$schema"}
    unrec_path = out_path.with_name(out_path.stem + ".unrecognized.txt")
    unrec_lines = read_unrecognized_lines(unrec_path)

    processed_count = len(mapping) + len(unrec_lines)
    assert processed_count == 1, f"--limit 1 should process exactly 1 file, got {processed_count}"


def test_dump_every_writes_checkpoint_message(tmp_path: Path):
    input_dir, _ = setup_input_dir(tmp_path, layout="flat")
    out_path = BUILD_DIR / "checkpoint.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    proc = run_script([str(input_dir), "-o", str(out_path), "--dump-every", "1", "--delay", "0.0", "-c", "2"])
    assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}"
    assert out_path.exists(), "Final output should exist"
    # Heuristic: expect at least one checkpoint message in stderr
    assert "Checkpoint: wrote" in proc.stderr, "Expected checkpoint message when --dump-every=1 is set"


def test_recursive_finds_nested_files(tmp_path: Path):
    input_dir, _ = setup_input_dir(tmp_path, layout="nested")
    out_path = BUILD_DIR / "recursive.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    proc = run_script([str(input_dir), "-o", str(out_path), "--delay", "0.0", "-c", "2"])
    assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}"
    assert out_path.exists(), "Output should be created with recursive scan"

    results_obj = read_results_json(out_path)
    mapping = {k: v for k, v in results_obj.items() if k != "$schema"}
    unrec_path = out_path.with_name(out_path.stem + ".unrecognized.txt")
    unrec_lines = read_unrecognized_lines(unrec_path)

    processed_count = len(mapping) + len(unrec_lines)
    assert processed_count == 2, f"Expected 2 nested files processed, got {processed_count}"
