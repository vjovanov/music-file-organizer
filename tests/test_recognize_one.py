import json
import sys
import subprocess
import os
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "recognize_one.py"
DATA_DIR = ROOT / "tests" / "data"


def run_script(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    """
    Execute recognize_one.py with provided args, capturing stdout/stderr.
    Uses the current repo root as cwd to ensure relative paths resolve.
    Injects tests/stubs into PYTHONPATH so recognize_one.py imports the test 'shazamio' stub.
    """
    cmd = [sys.executable, str(SCRIPT), *args]
    stub_dir = ROOT / "tests" / "stubs"
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{stub_dir}{os.pathsep}{existing}" if existing else str(stub_dir)
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def test_usage_no_args():
    # When no args are provided, script should exit with code 2 and print usage to stderr.
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2, f"Expected exit code 2 for missing args, got {proc.returncode}"
    assert "Usage:" in proc.stderr and "recognize_one.py <audio_file>" in proc.stderr, (
        f"Expected usage message on stderr, got: {proc.stderr!r}"
    )
    # Ensure nothing leaks to stdout in usage case
    assert proc.stdout.strip() == ""


def test_unrecognized_file_returns_nonzero_and_warns():
    audio_path = DATA_DIR / "unrecognized_song.mp3"
    assert audio_path.exists(), f"Missing test data: {audio_path}"

    proc = run_script([str(audio_path)])

    # shazamio returns a successful response with empty matches when nothing is recognized
    assert proc.returncode == 0, f"Expected exit code 0 for unrecognized audio, got {proc.returncode}"

    # No warnings expected on stderr
    assert proc.stderr.strip() == "", f"Expected no stderr, got: {proc.stderr!r}"

    # JSON should be emitted on stdout and contain an empty matches array
    assert proc.stdout.strip() != "", "Expected JSON on stdout"
    payload = json.loads(proc.stdout)
    assert "matches" in payload, "Expected 'matches' key in JSON"
    assert isinstance(payload["matches"], list), "'matches' should be a list"
    assert len(payload["matches"]) == 0, f"Expected no matches, got {len(payload['matches'])}"


def test_recognized_file_matches_expected_json():
    audio_path = DATA_DIR / "recognized_song.mp3"
    expected_json_path = DATA_DIR / "recognized_song.json"

    assert audio_path.exists(), f"Missing test data: {audio_path}"

    if not expected_json_path.exists():
        pytest.skip(
            f"Expected JSON snapshot not found: {expected_json_path}. "
            "Generate it by running: python recognize_one.py tests/data/recognized_song.mp3 > tests/data/recognized_song.json"
        )

    proc = run_script([str(audio_path)])

    # Success should return 0 and emit JSON only to stdout
    assert proc.returncode == 0, f"Expected exit code 0 for recognized audio, got {proc.returncode}"
    assert proc.stderr.strip() == "", f"Expected no stderr on success, got: {proc.stderr!r}"
    assert proc.stdout.strip() != "", "Expected JSON on stdout, got empty output"

    # Compare a stable subset from snapshot to avoid volatile fields (timestamp, tagid, etc.)
    actual_obj = json.loads(proc.stdout)
    with expected_json_path.open("r", encoding="utf-8") as f:
        expected_obj = json.load(f)

    exp_track = expected_obj.get("track", {})
    act_track = actual_obj.get("track", {})

    # Core scalar fields expected to be stable
    for key in ["key", "title", "subtitle", "url", "layout", "type"]:
        assert act_track.get(key) == exp_track.get(key), f"track.{key} mismatch"

    # Artists (compare adamid set)
    def artist_adamids(track):
        return {a.get("adamid") for a in track.get("artists", []) if a.get("adamid")}
    assert artist_adamids(act_track) == artist_adamids(exp_track), "Artist adamids mismatch"

    # Ensure at least one match is present (count may vary)
    assert isinstance(actual_obj.get("matches"), list), "'matches' should be a list"
    assert len(actual_obj["matches"]) >= 1, "Expected at least one match"
