"""Tests for ML batch intelligent scheduling (ML Lab Phase 4).

Covers cost-based create-time ordering (``fifo`` / ``cost_asc`` /
``cost_desc``), parallel workers capped by ``resolve_ml_train_max_workers()``,
and transient error retry with exponential backoff vs permanent errors.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.database import ensure_ml_batch_tables
from app.db.connection import get_connection
from app.services.bots import ml_batch_runner as mbr
from app.services.bots.ml_job_store import reset_ml_job_store_for_tests

_TINY_BACKOFF = (0.001, 0.002)


def _strategy_items(strategies: list[str]) -> list[dict]:
    return [
        {"strategy": s, "config": {"timeframe": "5m"}, "validate_after": False}
        for s in strategies
    ]


class _BatchTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ensure_ml_batch_tables()
        reset_ml_job_store_for_tests()
        mbr.reset_ml_batch_runner_for_tests()
        self._wipe()

    def tearDown(self):
        self._wipe()
        mbr.reset_ml_batch_runner_for_tests()

    @staticmethod
    def _wipe():
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM ml_batch_items")
            cur.execute("DELETE FROM ml_batches")
            conn.commit()
        finally:
            conn.close()

    def _create(self, strategies, **kwargs):
        batch, created = mbr.create_batch("BTCUSDT", _strategy_items(strategies), **kwargs)
        assert created
        return batch


class CostOrderingTests(_BatchTestBase):
    MIXED = ["LSTM_DIRECTION", "ML_SIGNAL_BOOST", "RL_PPO_AGENT", "UNKNOWN_STAT", "TRANSFORMER_SIGNAL"]

    async def test_default_cost_asc_runs_heavy_last(self):
        batch = self._create(self.MIXED)
        self.assertEqual(batch["schedule"], "cost_asc")
        strategies = [i["strategy"] for i in batch["items"]]
        # Light first, GBM middle, deep/RL last; equal costs keep input order.
        self.assertEqual(
            strategies,
            ["UNKNOWN_STAT", "ML_SIGNAL_BOOST", "LSTM_DIRECTION", "RL_PPO_AGENT", "TRANSFORMER_SIGNAL"],
        )
        self.assertEqual([i["order"] for i in batch["items"]], list(range(len(self.MIXED))))
        self.assertEqual(
            [i["cost"] for i in batch["items"]],
            [mbr.TRAIN_COST_LOW, mbr.TRAIN_COST_MEDIUM, mbr.TRAIN_COST_HIGH, mbr.TRAIN_COST_HIGH, mbr.TRAIN_COST_HIGH],
        )

    async def test_cost_desc_runs_heavy_first(self):
        batch = self._create(self.MIXED, schedule="cost_desc")
        self.assertEqual(batch["schedule"], "cost_desc")
        strategies = [i["strategy"] for i in batch["items"]]
        self.assertEqual(
            strategies,
            ["LSTM_DIRECTION", "RL_PPO_AGENT", "TRANSFORMER_SIGNAL", "ML_SIGNAL_BOOST", "UNKNOWN_STAT"],
        )

    async def test_fifo_preserves_submission_order(self):
        batch = self._create(self.MIXED, schedule="fifo")
        self.assertEqual(batch["schedule"], "fifo")
        self.assertEqual([i["strategy"] for i in batch["items"]], self.MIXED)

    async def test_unknown_schedule_falls_back_to_cost_asc(self):
        batch = self._create(self.MIXED, schedule="bogus")
        self.assertEqual(batch["schedule"], "cost_asc")
        self.assertEqual(batch["items"][0]["strategy"], "UNKNOWN_STAT")

    async def test_execution_follows_scheduled_order(self):
        batch = self._create(self.MIXED)
        ran = []

        async def exec_ok(b, item):
            ran.append(item["strategy"])
            return {"ok": True}

        final = await mbr.run_batch(batch["batch_id"], item_executor=exec_ok)
        self.assertEqual(final["status"], "done")
        self.assertEqual(
            ran,
            ["UNKNOWN_STAT", "ML_SIGNAL_BOOST", "LSTM_DIRECTION", "RL_PPO_AGENT", "TRANSFORMER_SIGNAL"],
        )


class ErrorClassificationTests(unittest.TestCase):
    def test_transient_tokens(self):
        for msg in (
            "A child process terminated abruptly, the process pool is not usable anymore",
            "BrokenProcessPool: worker died",
            "HTTP 429 too many requests",
            "rate limit exceeded",
            "Request timed out after 120000ms",
            "connection reset by peer",
            "temporary network failure",
        ):
            self.assertTrue(mbr.is_transient_batch_error(msg), msg)

    def test_permanent_tokens(self):
        for msg in (
            "insufficient candles (42)",
            "Data-quality gate blocked training for BTCUSDT: missing_frac=0.9",
            "Lab Train is not supported for FOO",
            "No module named 'torch'",
            "missing dependency: sklearn",
            "Need >= 500 candles for 5m validation (got 120)",
            "invalid executor result",
        ):
            self.assertFalse(mbr.is_transient_batch_error(msg), msg)

    def test_unknown_errors_default_to_permanent(self):
        self.assertFalse(mbr.is_transient_batch_error("some novel failure"))
        self.assertFalse(mbr.is_transient_batch_error(""))
        self.assertFalse(mbr.is_transient_batch_error(None))

    def test_explicit_result_flags_win(self):
        self.assertTrue(mbr.is_transient_batch_error("insufficient candles", {"transient": True}))
        self.assertTrue(mbr.is_transient_batch_error("odd", {"error_kind": "transient"}))
        self.assertFalse(mbr.is_transient_batch_error("timeout", {"error_kind": "permanent"}))
        self.assertFalse(mbr.is_transient_batch_error("timeout", {"permanent": True}))


class TransientRetryTests(_BatchTestBase):
    async def test_transient_retry_succeeds_on_second_attempt(self):
        batch = self._create(["ML_SIGNAL_BOOST"])
        bid = batch["batch_id"]
        calls = []

        async def exec_flaky(b, item):
            calls.append(item["seq"])
            if len(calls) == 1:
                return {"ok": False, "error": "process pool is not usable anymore"}
            return {"ok": True}

        with (
            patch.object(mbr, "ITEM_RETRY_BACKOFF_SEC", _TINY_BACKOFF),
            patch(
                "app.services.bots.ml_train_executor.resolve_ml_train_max_workers",
                return_value=1,
            ),
        ):
            final = await mbr.run_batch(bid, item_executor=exec_flaky)

        self.assertEqual(len(calls), 2)
        self.assertEqual(final["status"], "done")
        self.assertEqual(final["completed"], 1)
        item = final["items"][0]
        self.assertEqual(item["status"], "done")
        self.assertIsNone(item["error"])
        self.assertEqual(item["retry_count"], 1)
        self.assertIn("process pool", item["last_error"])

    async def test_transient_retry_exhausts_budget_then_errors(self):
        batch = self._create(["ML_SIGNAL_BOOST"])
        bid = batch["batch_id"]
        calls = []

        async def exec_always_transient(b, item):
            calls.append(item["seq"])
            return {"ok": False, "error": "HTTP 429 too many requests"}

        with patch.object(mbr, "ITEM_RETRY_BACKOFF_SEC", _TINY_BACKOFF):
            final = await mbr.run_batch(bid, item_executor=exec_always_transient)

        self.assertEqual(len(calls), 1 + mbr.ITEM_MAX_RETRIES)
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["failed"], 1)
        item = final["items"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["error"], "HTTP 429 too many requests")
        self.assertEqual(item["retry_count"], mbr.ITEM_MAX_RETRIES)
        self.assertEqual(item["last_error"], "HTTP 429 too many requests")

    async def test_permanent_errors_are_not_retried(self):
        batch = self._create(["ML_SIGNAL_BOOST"])
        bid = batch["batch_id"]
        calls = []

        async def exec_permanent(b, item):
            calls.append(item["seq"])
            return {"ok": False, "error": "insufficient candles (42)"}

        with patch.object(mbr, "ITEM_RETRY_BACKOFF_SEC", _TINY_BACKOFF):
            final = await mbr.run_batch(bid, item_executor=exec_permanent)

        self.assertEqual(len(calls), 1)
        self.assertEqual(final["failed"], 1)
        item = final["items"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["retry_count"], 0)
        self.assertIsNone(item["last_error"])

    async def test_manual_retry_resets_retry_budget(self):
        batch = self._create(["ML_SIGNAL_BOOST"])
        bid = batch["batch_id"]

        async def exec_always_transient(b, item):
            return {"ok": False, "error": "timed out"}

        with patch.object(mbr, "ITEM_RETRY_BACKOFF_SEC", _TINY_BACKOFF):
            first = await mbr.run_batch(bid, item_executor=exec_always_transient)
        self.assertEqual(first["items"][0]["retry_count"], mbr.ITEM_MAX_RETRIES)

        retried = mbr.retry_batch(bid)
        self.assertEqual(retried["requeued"], 1)
        item = retried["items"][0]
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["retry_count"], 0)
        self.assertIsNone(item["last_error"])

    async def test_backoff_sleep_interrupts_on_cancel(self):
        batch = self._create(["ML_SIGNAL_BOOST"])
        bid = batch["batch_id"]

        async def exec_transient(b, item):
            return {"ok": False, "error": "timed out"}

        # Long backoff; cancel the batch as soon as the retry wait begins.
        orig_sleep = mbr._retry_backoff_sleep

        async def sleep_then_cancel(batch_id_, seconds):
            mbr.cancel_batch(batch_id_, cancel_job=lambda j: {"ok": True})
            return await orig_sleep(batch_id_, seconds)

        with (
            patch.object(mbr, "ITEM_RETRY_BACKOFF_SEC", (60.0, 120.0)),
            patch.object(mbr, "_retry_backoff_sleep", side_effect=sleep_then_cancel),
        ):
            final = await asyncio.wait_for(
                mbr.run_batch(bid, item_executor=exec_transient),
                timeout=10,
            )

        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(final["items"][0]["status"], "cancelled")


class ParallelWorkerTests(_BatchTestBase):
    async def test_parallel_execution_respects_concurrency_cap(self):
        batch = self._create(["ML_SIGNAL_BOOST"] * 6, concurrency=2)
        bid = batch["batch_id"]
        inflight = 0
        max_inflight = 0
        done_seqs = []

        async def exec_slow(b, item):
            nonlocal inflight, max_inflight
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            await asyncio.sleep(0.01)
            inflight -= 1
            done_seqs.append(item["seq"])
            return {"ok": True}

        with patch(
            "app.services.bots.ml_train_executor.resolve_ml_train_max_workers",
            return_value=3,
        ):
            final = await mbr.run_batch(bid, item_executor=exec_slow)

        self.assertEqual(final["status"], "done")
        self.assertEqual(final["completed"], 6)
        self.assertEqual(sorted(done_seqs), list(range(6)))
        self.assertEqual(max_inflight, 2)

    async def test_concurrency_capped_by_max_workers(self):
        batch = self._create(["ML_SIGNAL_BOOST"] * 5, concurrency=5)
        bid = batch["batch_id"]
        inflight = 0
        max_inflight = 0

        async def exec_slow(b, item):
            nonlocal inflight, max_inflight
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            await asyncio.sleep(0.01)
            inflight -= 1
            return {"ok": True}

        with patch(
            "app.services.bots.ml_train_executor.resolve_ml_train_max_workers",
            return_value=2,
        ):
            final = await mbr.run_batch(bid, item_executor=exec_slow)

        self.assertEqual(final["completed"], 5)
        self.assertEqual(max_inflight, 2)

    async def test_serial_default_remains_unchanged(self):
        batch = self._create(["ML_SIGNAL_BOOST"] * 3)
        bid = batch["batch_id"]
        self.assertEqual(batch["concurrency"], 1)
        inflight = 0
        max_inflight = 0
        order = []

        async def exec_ok(b, item):
            nonlocal inflight, max_inflight
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            await asyncio.sleep(0.005)
            order.append(item["seq"])
            inflight -= 1
            return {"ok": True}

        with patch(
            "app.services.bots.ml_train_executor.resolve_ml_train_max_workers",
            return_value=1,
        ):
            final = await mbr.run_batch(bid, item_executor=exec_ok)
        self.assertEqual(final["status"], "done")
        self.assertEqual(order, [0, 1, 2])
        self.assertEqual(max_inflight, 1)

    async def test_crash_recovery_marks_all_parallel_running_items(self):
        batch = self._create(["ML_SIGNAL_BOOST"] * 3, concurrency=2)
        bid = batch["batch_id"]
        # Simulate two parallel in-flight items orphaned by a restart.
        first = mbr.claim_next_pending_item(bid)
        second = mbr.claim_next_pending_item(bid)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        mbr._refresh_batch_status(bid)

        resumed = mbr.recover_interrupted_batches()
        self.assertIn(bid, resumed)
        after = mbr.get_batch(bid)
        self.assertEqual(after["status"], "queued")
        by_seq = {i["seq"]: i for i in after["items"]}
        self.assertEqual(by_seq[0]["status"], "error")
        self.assertEqual(by_seq[1]["status"], "error")
        self.assertEqual(by_seq[0]["error"], "server restarted")
        self.assertEqual(by_seq[1]["error"], "server restarted")
        self.assertEqual(by_seq[2]["status"], "pending")

        async def exec_ok(b, item):
            return {"ok": True}

        final = await mbr.run_batch(bid, item_executor=exec_ok)
        self.assertEqual(final["completed"], 1)
        self.assertEqual(final["failed"], 2)
        self.assertEqual(final["status"], "done")


if __name__ == "__main__":
    unittest.main()
