"""Tests for src/pipeline.py orchestration (steps are stubbed; no data needed)."""
import pytest

from src import pipeline


def test_runs_steps_in_order_and_stops_on_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "STEPS", {
        "verify": lambda: calls.append("verify") or True,
        "audit": lambda: calls.append("audit") or False,   # fails
        "clean": lambda: calls.append("clean") or True,
    })
    assert pipeline.run(["verify", "audit", "clean"]) == 1
    assert calls == ["verify", "audit"]                    # clean never ran


def test_main_orders_steps_and_rejects_unknown(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "STEPS", {k: (lambda k=k: calls.append(k) or True) for k in ["verify", "audit", "clean"]})
    with pytest.raises(SystemExit) as e:
        pipeline.main(["--steps", "clean,verify"])
    assert e.value.code == 0 and calls == ["verify", "clean"]
    with pytest.raises(SystemExit) as e:
        pipeline.main(["--steps", "nope"])
    assert "Unknown step" in str(e.value.code)
