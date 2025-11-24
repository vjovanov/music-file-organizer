# Testing

- Always output files in folder `build`
- For the music input dir use `tests/data`
- Never modify `tests/data` in tests: store test output to the `build` folder

## Test commands

The test suite uses pytest. Tests are self-contained and:
- Write all artifacts under `build/…`
- Use stubs (no network calls); `shazamio` is not required for tests
- Optionally validate JSON against schemas if `jsonschema` is installed

### 1) Create an isolated virtualenv (recommended, kept under build/)
```bash
python -m venv build/.venv
source build/.venv/bin/activate
python -m pip install --upgrade pip
pip install pytest jsonschema
```
Notes:
- `jsonschema` is optional; without it, schema-validation tests are skipped via `pytest.importorskip`.

### 2) Run the entire test suite
```bash
pytest -q
```

### 3) Useful invocations
- Verbose with live output:
  ```bash
  pytest -vv -s
  ```
- Run a single file:
  ```bash
  pytest tests/test_recognize_one.py -q
  pytest tests/test_batch_recognize.py -q
  pytest tests/test_organize_recognized.py -q
  ```
- Run a single test by keyword:
  ```bash
  pytest -k "pattern_placeholders" -q
  ```
- Stop on first failure / re-run last failed:
  ```bash
  pytest -x
  pytest --lf
  ```

### 4) Artifacts and test data
- All test outputs are written under `build/…` (e.g., `build/test_batch_recognize/…`, `build/test_organize_recognized/…`)
- Test inputs live in `tests/data`. Do not modify anything inside `tests/data` during tests.
- A snapshot file `tests/data/recognized_song.json` is used by some assertions; if missing, those parts are skipped. A copy is already present in this repository.

### 5) Environment notes
- No external services required; tests inject local stubs automatically:
  - `tests/test_recognize_one.py` prepends `tests/stubs` to `PYTHONPATH` so the local `shazamio` stub is used.
  - `tests/test_batch_recognize.py` passes `--recognizer-script tests/stubs/recognize_stub.py` to avoid network calls.
- Python 3.8+ is recommended to match project code and test expectations.
