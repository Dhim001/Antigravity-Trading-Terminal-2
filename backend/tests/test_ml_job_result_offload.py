"""MEMORY_CENTRIC_REVIEW #31 — terminal ML job results: large payloads are
offloaded to disk and slimmed in RAM; small ones stay hot."""

import json

import pytest

from app.services.bots import ml_job_store
from app.services.bots.ml_job_store import (
    create_ml_job,
    finish_ml_job,
    get_ml_job,
    public_ml_job,
    reset_ml_job_store_for_tests,
)


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    # Keep run-history persistence out of the dev DB during these tests.
    monkeypatch.setattr(
        "app.services.bots.ml_train_runs.record_ml_train_run_from_job",
        lambda job: None,
    )
    reset_ml_job_store_for_tests()
    yield
    reset_ml_job_store_for_tests()


@pytest.fixture
def offload_dir(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp and force a tiny offload threshold."""
    monkeypatch.setattr("app.config.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.config.ML_JOB_RESULT_OFFLOAD_BYTES", 200)
    return tmp_path


def _big_result(size=2048):
    return {
        "ok": True,
        "metrics": {"val_accuracy": 0.61},
        "version_id": "v1",
        "walk_forward": {"folds": [{"data": "x" * size}]},
        "_wf_bundle": {"torch": "never-serialize"},
    }


def test_large_terminal_result_is_slimmed_and_offloaded(offload_dir):
    jid = create_ml_job(kind="train", strategy="RL_PPO_AGENT", symbol="AAPL")
    finish_ml_job(jid, "done", result=_big_result())

    job = get_ml_job(jid)
    result = job["result"]
    # RAM holds a slim headline with offload markers.
    assert result["_slimmed"] is True
    assert result["metrics"] == {"val_accuracy": 0.61}
    assert result["version_id"] == "v1"
    assert "walk_forward" not in result

    # The full payload (minus _wf_bundle) is on disk.
    with open(result["_result_file"], "r", encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["walk_forward"]["folds"][0]["data"]
    assert "_wf_bundle" not in on_disk

    # The public API hydrates the full result transparently.
    pub = public_ml_job(job, include_result=True)
    assert pub["result"]["walk_forward"]["folds"][0]["data"]
    assert "_slimmed" not in pub["result"]
    assert "_result_file" not in pub["result"]


def test_small_terminal_result_stays_in_ram(offload_dir):
    jid = create_ml_job(kind="validate", strategy="LSTM_DIRECTION", symbol="AAPL")
    finish_ml_job(jid, "done", result={"ok": True, "metrics": {"val_accuracy": 0.5}})

    job = get_ml_job(jid)
    assert not job["result"].get("_slimmed")
    pub = public_ml_job(job, include_result=True)
    assert pub["result"]["metrics"] == {"val_accuracy": 0.5}
    # No result file written for small payloads.
    assert not (offload_dir / "ml_job_results" / f"{jid}.json").exists()


def test_missing_offload_file_falls_back_to_slim_headline(offload_dir):
    jid = create_ml_job(kind="train", strategy="RL_PPO_AGENT", symbol="AAPL")
    finish_ml_job(jid, "done", result=_big_result())

    job = get_ml_job(jid)
    import os

    os.remove(job["result"]["_result_file"])
    pub = public_ml_job(job, include_result=True)
    # Slim headline served without internal markers — never a 500.
    assert pub["result"]["metrics"] == {"val_accuracy": 0.61}
    assert "_result_file" not in pub["result"]


def test_job_eviction_deletes_offload_file(offload_dir):
    jid = create_ml_job(kind="train", strategy="RL_PPO_AGENT", symbol="AAPL")
    finish_ml_job(jid, "done", result=_big_result())
    path = get_ml_job(jid)["result"]["_result_file"]

    import os

    assert os.path.exists(path)
    # Push the store past _MAX_JOBS with newer terminal jobs → oldest evicted.
    for i in range(ml_job_store._MAX_JOBS + 5):
        other = create_ml_job(kind="validate", strategy="LSTM_DIRECTION", symbol="AAPL")
        finish_ml_job(other, "done", result={"ok": True, "i": i})
    assert get_ml_job(jid) is None
    assert not os.path.exists(path)
