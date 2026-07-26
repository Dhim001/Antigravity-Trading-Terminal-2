"""Early-stop helper contracts for deep ML trainers."""

from app.services.bots.ml_early_stop import early_stop_patience, mark_early_stop


def test_early_stop_patience_clamps():
    assert early_stop_patience({}) == 10
    assert early_stop_patience({"early_stop_patience": 3}) == 3
    # Bare ``patience`` must NOT steer early-stop (LR scheduler collision).
    assert early_stop_patience({"patience": 7}) == 10
    assert early_stop_patience({"early_stop_patience": 0}) == 1
    assert early_stop_patience({"early_stop_patience": 999}) == 100


def test_mark_early_stop_fields(tmp_path):
    progress = tmp_path / "prog.json"
    out = mark_early_stop(
        epoch_1based=13,
        epochs_budget=60,
        patience=10,
        progress_path=str(progress),
        strategy="GNN_CROSS_ASSET",
    )
    assert out["early_stopped"] is True
    assert out["epochs_trained"] == 13
    assert out["epochs_budget"] == 60
    assert "13/60" in out["early_stop_reason"]
    assert progress.is_file()
