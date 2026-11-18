"""Tests for src/manifest.py on tiny SYNTHETIC files (test data only)."""
import hashlib
import json

import pytest

from src import config, manifest

ROWS = {"2019-Oct.csv": 3, "2019-Nov.csv": 2, "2019-Dec.csv": 1, "2020-Jan.csv": 2}


@pytest.fixture
def env(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    for name, n in ROWS.items():
        (raw / name).write_bytes(b"h1,h2\n" + b"".join(b"%d,x\n" % i for i in range(n)))
    monkeypatch.setattr(config, "EXPECTED_ROWS", dict(ROWS))
    monkeypatch.setattr(manifest, "MANIFEST_JSON", tmp_path / "data_manifest.json")
    monkeypatch.setattr(manifest, "MANIFEST_MD", tmp_path / "data_manifest.md")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    return tmp_path, raw


def test_fingerprint(tmp_path):
    p = tmp_path / "f.csv"
    p.write_bytes(b"a,b\n1,2\n3,4")          # no trailing newline
    fp = manifest.fingerprint(p)
    assert fp["rows"] == 2 and fp["size_bytes"] == 11
    assert fp["sha256"] == hashlib.sha256(b"a,b\n1,2\n3,4").hexdigest()


def test_create_then_verify(env):
    root, raw = env
    assert manifest.create(raw, force=False) == 0
    data = json.loads((root / "data_manifest.json").read_text())
    assert [e["rows"] for e in data["files"]] == list(ROWS.values())
    assert "| `2019-Oct.csv` |" in (root / "data_manifest.md").read_text()
    assert manifest.verify(raw) == 0


def test_verify_detects_changed_file(env):
    root, raw = env
    manifest.create(raw, force=False)
    (raw / "2019-Dec.csv").write_bytes(b"h1,h2\n0,y\n")   # same rows, different bytes
    assert manifest.verify(raw) == 1


def test_create_refuses_wrong_row_counts(env, monkeypatch):
    root, raw = env
    monkeypatch.setattr(config, "EXPECTED_ROWS", {**ROWS, "2019-Oct.csv": 999})
    assert manifest.create(raw, force=False) == 1
    assert not (root / "data_manifest.json").exists()


def test_create_does_not_overwrite_without_force(env):
    root, raw = env
    assert manifest.create(raw, force=False) == 0
    assert manifest.create(raw, force=False) == 1
    assert manifest.create(raw, force=True) == 0
