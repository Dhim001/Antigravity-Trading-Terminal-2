"""Tests for the durable ML batch training runner (ML Lab Phase 2)."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.database import ensure_ml_batch_tables, init_db
from app.db.connection import get_connection
from app.services.bots import ml_batch_runner as mbr
from app.services.bots.ml_job_store import reset_ml_job_store_for_tests
from app.services.bots.ml_train_runs import list_ml_train_runs, record_ml_train_run_from_job


def _items(n: int, *, validate_after: bool = False) -> list[dict]:
    return [
        {
            "strategy": "ML_SIGNAL_BOOST",
            "config": {"timeframe": "5m", "seq": i},
            "validate_after": validate_after,
        }
        for i in range(n)
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

    def _create(self, n=3, **kwargs):
        batch, created = mbr.create_batch("BTCUSDT", _items(n), **kwargs)
        assert created
        return batch


class CreateBatchTests(_BatchTestBase):
    async def test_create_batch_persists_items(self):
        batch = self._create(2, fail_fast=True, concurrency=2, idempotency_key="k-1")
        self.assertEqual(batch["status"], "queued")
        self.assertEqual(batch["total"], 2)
        self.assertTrue(batch["fail_fast"])
        self.assertEqual(batch["concurrency"], 2)
        self.assertEqual(len(batch["items"]), 2)
        first = batch["items"][0]
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["strategy"], "ML_SIGNAL_BOOST")
        self.assertEqual(first["config"].get("seq"), 0)

    async def test_create_batch_idempotency_returns_existing(self):
        batch, created = mbr.create_batch(
            "BTCUSDT", _items(2), idempotency_key="dup-key",
        )
        self.assertTrue(created)
        again, created_again = mbr.create_batch(
            "BTCUSDT", _items(5), idempotency_key="dup-key",
        )
        self.assertFalse(created_again)
        self.assertEqual(again["batch_id"], batch["batch_id"])
        self.assertEqual(again["total"], 2)

        other, created_other = mbr.create_batch(
            "BTCUSDT", _items(1), idempotency_key="other-key",
        )
        self.assertTrue(created_other)
        self.assertNotEqual(other["batch_id"], batch["batch_id"])


class RunBatchTests(_BatchTestBase):
    async def test_serial_execution_and_status_transitions(self):
        batch = self._create(3)
        bid = batch["batch_id"]
        order = []
        mid_status = []

        async def exec_ok(b, item):
            order.append(item["seq"])
            mid_status.append(mbr.get_batch(bid)["status"])
            return {"ok": True}

        with patch(
            "app.services.bots.ml_train_executor.resolve_ml_train_max_workers",
            return_value=1,
        ):
            final = await mbr.run_batch(bid, item_executor=exec_ok)

        self.assertEqual(order, [0, 1, 2])
        self.assertEqual(final["status"], "done")
        self.assertEqual(final["completed"], 3)
        self.assertEqual(final["failed"], 0)
        self.assertTrue(all(s == "running" for s in mid_status))
        self.assertTrue(
            all(i["status"] == "done" for i in final["items"]),
            msg=str([(i["seq"], i["status"]) for i in final["items"]]),
        )

    async def test_failure_isolation_without_fail_fast(self):
        batch = self._create(3, fail_fast=False)
        bid = batch["batch_id"]

        async def exec_flaky(b, item):
            if item["seq"] == 1:
                return {"ok": False, "error": "boom"}
            return {"ok": True}

        final = await mbr.run_batch(bid, item_executor=exec_flaky)
        self.assertEqual(final["completed"], 2)
        self.assertEqual(final["failed"], 1)
        self.assertEqual(final["status"], "done")
        by_seq = {i["seq"]: i for i in final["items"]}
        self.assertEqual(by_seq[1]["status"], "error")
        self.assertEqual(by_seq[1]["error"], "boom")
        self.assertEqual(by_seq[0]["status"], "done")
        self.assertEqual(by_seq[2]["status"], "done")

    async def test_fail_fast_skips_remaining(self):
        batch = self._create(3, fail_fast=True)
        bid = batch["batch_id"]
        calls = []

        async def exec_fail_first(b, item):
            calls.append(item["seq"])
            if item["seq"] == 0:
                return {"ok": False, "error": "first failed"}
            return {"ok": True}

        final = await mbr.run_batch(bid, item_executor=exec_fail_first)
        self.assertEqual(calls, [0])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["failed"], 1)
        self.assertEqual(final["cancelled"], 2)
        by_seq = {i["seq"]: i for i in final["items"]}
        self.assertEqual(by_seq[0]["status"], "error")
        self.assertEqual(by_seq[1]["status"], "skipped")
        self.assertEqual(by_seq[2]["status"], "skipped")

    async def test_retry_requeues_only_failed_items(self):
        batch = self._create(2, fail_fast=False)
        bid = batch["batch_id"]

        async def exec_a_fails(b, item):
            if item["seq"] == 0:
                return {"ok": False, "error": "transient", "job_id": "job-a1"}
            return {"ok": True, "job_id": "job-b1"}

        first = await mbr.run_batch(bid, item_executor=exec_a_fails)
        self.assertEqual(first["failed"], 1)
        job_b1 = {i["seq"]: i["job_id"] for i in first["items"]}[1]

        retried = mbr.retry_batch(bid)
        self.assertEqual(retried["requeued"], 1)
        self.assertEqual(retried["status"], "queued")
        by_seq = {i["seq"]: i for i in retried["items"]}
        self.assertEqual(by_seq[0]["status"], "pending")
        self.assertIsNone(by_seq[0]["job_id"])
        self.assertIsNone(by_seq[0]["error"])
        self.assertEqual(by_seq[1]["status"], "done")

        ran = []

        async def exec_ok(b, item):
            ran.append(item["seq"])
            return {"ok": True, "job_id": "job-a2"}

        final = await mbr.run_batch(bid, item_executor=exec_ok)
        self.assertEqual(ran, [0])
        self.assertEqual(final["status"], "done")
        self.assertEqual(final["completed"], 2)
        final_by_seq = {i["seq"]: i for i in final["items"]}
        self.assertEqual(final_by_seq[1]["job_id"], job_b1)

    async def test_retry_on_terminal_batch_without_failures_is_noop(self):
        batch = self._create(1)

        async def exec_ok(b, item):
            return {"ok": True}

        await mbr.run_batch(batch["batch_id"], item_executor=exec_ok)
        retried = mbr.retry_batch(batch["batch_id"])
        self.assertEqual(retried["requeued"], 0)
        self.assertEqual(retried["status"], "done")


class CancelBatchTests(_BatchTestBase):
    async def test_cancel_cancels_active_job_and_skips_remaining(self):
        batch = self._create(3)
        bid = batch["batch_id"]
        started = asyncio.Event()
        release = asyncio.Event()
        cancelled_jobs = []
        executed = []

        async def exec_block(b, item):
            executed.append(item["seq"])
            mbr.set_item_job_id(item["item_id"], "job-1")
            started.set()
            await release.wait()
            return {"ok": False, "cancelled": True, "error": "cancelled", "job_id": "job-1"}

        def fake_cancel(job_id):
            cancelled_jobs.append(job_id)
            release.set()
            return {"ok": True, "cancelled": True}

        runner = asyncio.create_task(mbr.run_batch(bid, item_executor=exec_block))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            out = mbr.cancel_batch(bid, cancel_job=fake_cancel)
            self.assertIsNotNone(out)
            final = await asyncio.wait_for(runner, timeout=5)
        finally:
            release.set()
            if not runner.done():
                runner.cancel()

        self.assertEqual(cancelled_jobs, ["job-1"])
        self.assertEqual(executed, [0])
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(final["cancelled"], 3)
        by_seq = {i["seq"]: i for i in final["items"]}
        self.assertEqual(by_seq[0]["status"], "cancelled")
        self.assertEqual(by_seq[1]["status"], "skipped")
        self.assertEqual(by_seq[2]["status"], "skipped")

    async def test_cancel_terminal_batch_is_noop(self):
        batch = self._create(1)

        async def exec_ok(b, item):
            return {"ok": True}

        await mbr.run_batch(batch["batch_id"], item_executor=exec_ok)
        out = mbr.cancel_batch(batch["batch_id"], cancel_job=lambda j: {"ok": True})
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["completed"], 1)

    async def test_cancel_unknown_batch_returns_none(self):
        self.assertIsNone(mbr.cancel_batch("nope"))


class RecoveryTests(_BatchTestBase):
    async def test_recovery_marks_interrupted_item_and_resumes_pending(self):
        batch = self._create(2)
        bid = batch["batch_id"]
        claimed = mbr.claim_next_pending_item(bid)
        self.assertIsNotNone(claimed)
        mbr.set_item_job_id(claimed["item_id"], "job-x")
        mbr._refresh_batch_status(bid)
        self.assertEqual(mbr.get_batch(bid)["status"], "running")

        resumed = mbr.recover_interrupted_batches()
        self.assertIn(bid, resumed)
        after = mbr.get_batch(bid)
        self.assertEqual(after["status"], "queued")
        by_seq = {i["seq"]: i for i in after["items"]}
        self.assertEqual(by_seq[0]["status"], "error")
        self.assertEqual(by_seq[0]["error"], "server restarted")
        self.assertEqual(by_seq[1]["status"], "pending")

        # Resumed batch completes remaining work.
        async def exec_ok(b, item):
            return {"ok": True}

        final = await mbr.run_batch(bid, item_executor=exec_ok)
        self.assertEqual(final["completed"], 1)
        self.assertEqual(final["failed"], 1)
        self.assertEqual(final["status"], "done")

    async def test_recovery_terminal_when_no_pending_left(self):
        batch = self._create(1)
        bid = batch["batch_id"]
        claimed = mbr.claim_next_pending_item(bid)
        mbr._refresh_batch_status(bid)

        resumed = mbr.recover_interrupted_batches()
        self.assertNotIn(bid, resumed)
        after = mbr.get_batch(bid)
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["items"][0]["error"], "server restarted")

    async def test_recovery_done_train_job_with_validate_after_marked_error(self):
        from app.services.bots.ml_job_store import create_ml_job, finish_ml_job

        batch, created = mbr.create_batch("BTCUSDT", _items(1, validate_after=True))
        assert created
        bid = batch["batch_id"]
        claimed = mbr.claim_next_pending_item(bid)
        job_id = create_ml_job(kind="train", strategy="ML_SIGNAL_BOOST", symbol="BTCUSDT")
        mbr.set_item_job_id(claimed["item_id"], job_id)
        finish_ml_job(job_id, status="done", result={"ok": True})

        resumed = mbr.recover_interrupted_batches()
        # No pending work left — the lone item terminal-errors, no resume.
        self.assertNotIn(bid, resumed)
        item = mbr.get_batch(bid)["items"][0]
        self.assertEqual(item["status"], "error")
        self.assertIn("interrupted before validation", item["error"])

    async def test_recovery_done_validate_job_with_validate_after_marks_done(self):
        from app.services.bots.ml_job_store import create_ml_job, finish_ml_job

        batch, created = mbr.create_batch("BTCUSDT", _items(1, validate_after=True))
        assert created
        bid = batch["batch_id"]
        claimed = mbr.claim_next_pending_item(bid)
        job_id = create_ml_job(kind="validate", strategy="ML_SIGNAL_BOOST", symbol="BTCUSDT")
        mbr.set_item_job_id(claimed["item_id"], job_id)
        finish_ml_job(job_id, status="done", result={"ok": True})

        mbr.recover_interrupted_batches()
        item = mbr.get_batch(bid)["items"][0]
        self.assertEqual(item["status"], "done")


class ValidateAfterTests(_BatchTestBase):
    @staticmethod
    def _candles(n=450):
        base = 1_700_000_000
        return [
            {"time": base + i * 300, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for i in range(n)
        ]

    async def _run_item(self, *, validate_ok: bool):
        batch, created = mbr.create_batch("BTCUSDT", _items(1, validate_after=True))
        assert created
        item = batch["items"][0]
        candles = self._candles()
        with (
            patch(
                "app.api.http.app._fetch_training_candles",
                new=AsyncMock(return_value=candles),
            ),
            patch(
                "app.api.http.app._enrich_training_candles",
                side_effect=lambda symbol, c, strategy, cfg: c,
            ),
            patch(
                "app.services.bots.ml_train_executor.submit_train_job",
                new=AsyncMock(return_value={"ok": True}),
            ) as train,
            patch(
                "app.api.http.app._fetch_validate_candles_enough",
                new=AsyncMock(return_value=(candles, 3, len(candles))),
            ),
            patch(
                "app.services.bots.ml_train_executor.submit_validate_job",
                new=AsyncMock(
                    return_value=(
                        {"ok": True, "mean_accuracy": 0.57}
                        if validate_ok
                        else {"ok": False, "error": "overfit"}
                    )
                ),
            ) as validate,
        ):
            out = await mbr.execute_batch_item(object(), batch, item)
        return out, train, validate, batch

    async def test_validate_after_success_marks_validation(self):
        out, train, validate, _batch = await self._run_item(validate_ok=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["validation"]["mean_accuracy"], 0.57)
        train.assert_awaited_once()
        validate.assert_awaited_once()
        # Pre-generated job id lets cancel find the in-flight job.
        job_id = out.get("job_id")
        self.assertTrue(job_id)
        _, kwargs = train.await_args
        self.assertEqual(kwargs.get("job_id"), job_id)

    async def test_validate_after_failure_marks_item_error(self):
        out, _, validate, _batch = await self._run_item(validate_ok=False)
        validate.assert_awaited_once()
        self.assertFalse(out["ok"])
        self.assertIn("validate_after failed", out["error"])
        self.assertIn("overfit", out["error"])

    async def test_validate_job_linked_to_item_before_submission(self):
        out, train, validate, batch = await self._run_item(validate_ok=True)
        self.assertTrue(out["ok"])
        _, vkwargs = validate.await_args
        validate_job_id = vkwargs.get("job_id")
        self.assertTrue(validate_job_id)
        _, tkwargs = train.await_args
        self.assertNotEqual(tkwargs.get("job_id"), validate_job_id)
        # The item row points at the validate job so reconciliation and crash
        # recovery track the walk-forward phase, not just the train job.
        item = mbr.get_batch(batch["batch_id"])["items"][0]
        self.assertEqual(item["job_id"], validate_job_id)


class CamelCaseItemConfigTests(_BatchTestBase):
    """Legacy Lab knob snapshots (camelCase) must reach trainers as snake_case."""

    @staticmethod
    def _candles(n=450):
        base = 1_700_000_000
        return [
            {"time": base + i * 300, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for i in range(n)
        ]

    async def test_camelcase_knobs_normalized_for_train_and_validate(self):
        camel = {
            "timeframe": "5m",
            "training_window_months": 3,
            "gbmMaxIter": "450",
            "gbmMaxDepth": 8,
            "nFolds": "4",
            "validateMaxBars": "50000",
            "pboSegments": "6",
            "pboMaxCombos": 4,
        }
        batch, created = mbr.create_batch(
            "BTCUSDT",
            [{"strategy": "ML_SIGNAL_BOOST", "config": camel, "validate_after": True}],
        )
        assert created
        item = batch["items"][0]
        candles = self._candles()
        with (
            patch(
                "app.api.http.app._fetch_training_candles",
                new=AsyncMock(return_value=candles),
            ),
            patch(
                "app.api.http.app._enrich_training_candles",
                side_effect=lambda symbol, c, strategy, cfg: c,
            ),
            patch(
                "app.services.bots.ml_train_executor.submit_train_job",
                new=AsyncMock(return_value={"ok": True}),
            ) as train,
            patch(
                "app.api.http.app._fetch_validate_candles_enough",
                new=AsyncMock(return_value=(candles, 3, len(candles))),
            ),
            patch(
                "app.services.bots.ml_train_executor.submit_validate_job",
                new=AsyncMock(return_value={"ok": True, "mean_accuracy": 0.57}),
            ) as validate,
        ):
            out = await mbr.execute_batch_item(object(), batch, item)
        self.assertTrue(out["ok"])
        train_cfg = train.await_args.args[3]
        self.assertEqual(train_cfg["gbm_max_iter"], "450")
        self.assertEqual(train_cfg["gbm_max_depth"], 8)
        self.assertNotIn("gbmMaxIter", train_cfg)
        # validate_after reads validate_folds / validate_max_bars / pbo_segments
        _, vkwargs = validate.await_args
        self.assertEqual(vkwargs.get("n_folds"), 4)
        self.assertEqual(vkwargs.get("pbo_segments"), 6)

    def test_normalize_prefers_snake_and_drops_camel(self):
        out = mbr._normalize_item_config_keys(
            {"gbmMaxIter": 100, "gbm_max_iter": 300, "timeframe": "1m"}
        )
        self.assertEqual(out["gbm_max_iter"], 300)
        self.assertNotIn("gbmMaxIter", out)
        self.assertEqual(out["timeframe"], "1m")


class _FakeRunsRequest:
    """Minimal stand-in for the Starlette request ml_list_runs_handler reads."""

    def __init__(self, params: dict):
        self.query_params = params


class RunsBatchFilterTests(_BatchTestBase):
    """GET /api/v1/ml/runs?batch_id=… filter (ML Lab Phase 3)."""

    def setUp(self):
        super().setUp()
        init_db()  # guarantee ml_train_runs exists regardless of test order

    @staticmethod
    def _record_run(job_id: str, strategy: str = "ML_SIGNAL_BOOST", symbol: str = "TESTSYM_BATCH"):
        return record_ml_train_run_from_job({
            "job_id": job_id,
            "kind": "train",
            "strategy": strategy,
            "symbol": symbol,
            "status": "done",
            "started_at": "2026-08-01T10:00:00Z",
            "finished_at": "2026-08-01T10:01:00Z",
            "result": {"ok": True},
        })

    def _batch_with_jobs(self, n: int = 2) -> dict:
        batch = self._create(n)
        for i, item in enumerate(batch["items"]):
            mbr.set_item_job_id(item["item_id"], f"job-batch-{batch['batch_id'][:8]}-{i}")
        return mbr.get_batch(batch["batch_id"])

    async def test_runs_filter_by_batch_id(self):
        batch = self._batch_with_jobs(2)
        job_ids = [i["job_id"] for i in batch["items"]]
        for jid in job_ids:
            self.assertTrue(self._record_run(jid))
        self.assertTrue(self._record_run("job-unrelated-batch-filter"))

        runs = list_ml_train_runs(batch_id=batch["batch_id"], limit=10)
        self.assertEqual(sorted(r["job_id"] for r in runs), sorted(job_ids))

    async def test_runs_filter_unknown_batch_returns_empty(self):
        self.assertTrue(self._record_run("job-unknown-batch"))
        runs = list_ml_train_runs(batch_id="batch-does-not-exist", limit=10)
        self.assertEqual(runs, [])

    async def test_runs_filter_skips_items_without_job_id(self):
        batch = self._create(2)  # pending items — job_id is NULL
        runs = list_ml_train_runs(batch_id=batch["batch_id"], limit=10)
        self.assertEqual(runs, [])

    async def test_runs_batch_filter_composes_with_symbol_and_strategy(self):
        batch = self._batch_with_jobs(1)
        jid = batch["items"][0]["job_id"]
        self.assertTrue(self._record_run(jid, strategy="LSTM_DIRECTION"))

        runs = list_ml_train_runs(
            symbol="TESTSYM_BATCH", strategy="LSTM_DIRECTION",
            batch_id=batch["batch_id"], limit=10,
        )
        self.assertEqual([r["job_id"] for r in runs], [jid])

        wrong_symbol = list_ml_train_runs(
            symbol="NOSUCHSYM", batch_id=batch["batch_id"], limit=10,
        )
        self.assertEqual(wrong_symbol, [])
        wrong_strategy = list_ml_train_runs(
            strategy="TRANSFORMER_SIGNAL", batch_id=batch["batch_id"], limit=10,
        )
        self.assertEqual(wrong_strategy, [])

    async def test_runs_handler_accepts_batch_id_query_param(self):
        from app.api.http.app import ml_list_runs_handler

        batch = self._batch_with_jobs(1)
        jid = batch["items"][0]["job_id"]
        self.assertTrue(self._record_run(jid))

        resp = await ml_list_runs_handler(_FakeRunsRequest({"batch_id": batch["batch_id"]}))
        payload = json.loads(resp.body)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["runs"][0]["job_id"], jid)

        empty = await ml_list_runs_handler(_FakeRunsRequest({"batch_id": "batch-gone"}))
        empty_payload = json.loads(empty.body)
        self.assertTrue(empty_payload["ok"])
        self.assertEqual(empty_payload["count"], 0)


class EnsureBatchRunnerTests(_BatchTestBase):
    """Self-healing: a runner task that dies mid-process must be respawned."""

    async def test_respawns_dead_runner_for_queued_batch(self):
        batch = self._create(2)
        bid = batch["batch_id"]

        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        with patch.object(mbr, "run_batch", side_effect=_never):
            self.assertTrue(mbr.ensure_batch_runner(bid, None))
            # A live task makes further ensure calls a no-op.
            self.assertFalse(mbr.ensure_batch_runner(bid, None))
            task = mbr._runner_tasks.get(bid)
            self.assertIsNotNone(task)
            # Let the task actually start — cancelling a never-started task
            # closes the coroutine without running its finally (no pop).
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            # Task exited (cancel path pops it) — ensure respawns again.
            self.assertNotIn(bid, mbr._runner_tasks)
            # Step outside the respawn cooldown window first.
            mbr._last_spawn_attempt[bid] -= mbr.RESPAWN_COOLDOWN_SEC + 1
            self.assertTrue(mbr.ensure_batch_runner(bid, None))
            task2 = mbr._runner_tasks.get(bid)
            await asyncio.sleep(0)
            task2.cancel()
            try:
                await task2
            except asyncio.CancelledError:
                pass

    async def test_noop_for_terminal_batch(self):
        batch = self._create(1)
        bid = batch["batch_id"]

        async def exec_ok(b, item):
            return {"ok": True}

        with patch(
            "app.services.bots.ml_train_executor.resolve_ml_train_max_workers",
            return_value=1,
        ):
            await mbr.run_batch(bid, item_executor=exec_ok)
        self.assertEqual(mbr.get_batch(bid)["status"], "done")
        self.assertFalse(mbr.ensure_batch_runner(bid, None))
        self.assertNotIn(bid, mbr._runner_tasks)

    async def test_noop_for_missing_batch(self):
        self.assertFalse(mbr.ensure_batch_runner("batch-does-not-exist", None))

    async def test_orphaned_running_item_without_job_marked_retryable(self):
        batch = self._create(2)
        bid = batch["batch_id"]
        first = batch["items"][0]
        mbr.set_item_status(first["item_id"], "running")

        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        with patch.object(mbr, "run_batch", side_effect=_never):
            self.assertTrue(mbr.ensure_batch_runner(bid, None))
            task = mbr._runner_tasks.get(bid)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        item0 = mbr.get_batch(bid)["items"][0]
        self.assertEqual(item0["status"], "error")
        self.assertIn("runner lost", item0["error"])

    async def test_orphaned_running_item_with_terminal_job_finalized(self):
        from app.services.bots.ml_job_store import create_ml_job, finish_ml_job

        batch = self._create(2)
        bid = batch["batch_id"]
        first = batch["items"][0]
        job_id = create_ml_job(kind="train", strategy="ML_SIGNAL_BOOST", symbol="BTCUSDT")
        mbr.set_item_status(first["item_id"], "running", job_id=job_id)
        finish_ml_job(job_id, status="done", result={"ok": True})

        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        with patch.object(mbr, "run_batch", side_effect=_never):
            self.assertTrue(mbr.ensure_batch_runner(bid, None))
            task = mbr._runner_tasks.get(bid)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        item0 = mbr.get_batch(bid)["items"][0]
        self.assertEqual(item0["status"], "done")

    async def test_orphaned_item_done_train_job_validate_after_marked_error(self):
        from app.services.bots.ml_job_store import create_ml_job, finish_ml_job

        batch, created = mbr.create_batch("BTCUSDT", _items(2, validate_after=True))
        assert created
        bid = batch["batch_id"]
        first = batch["items"][0]
        job_id = create_ml_job(kind="train", strategy="ML_SIGNAL_BOOST", symbol="BTCUSDT")
        mbr.set_item_status(first["item_id"], "running", job_id=job_id)
        finish_ml_job(job_id, status="done", result={"ok": True})

        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        with patch.object(mbr, "run_batch", side_effect=_never):
            self.assertTrue(mbr.ensure_batch_runner(bid, None))
            task = mbr._runner_tasks.get(bid)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        item0 = mbr.get_batch(bid)["items"][0]
        self.assertEqual(item0["status"], "error")
        self.assertIn("interrupted before validation", item0["error"])

    async def test_orphaned_item_done_validate_job_finalized_done(self):
        from app.services.bots.ml_job_store import create_ml_job, finish_ml_job

        batch, created = mbr.create_batch("BTCUSDT", _items(2, validate_after=True))
        assert created
        bid = batch["batch_id"]
        first = batch["items"][0]
        job_id = create_ml_job(kind="validate", strategy="ML_SIGNAL_BOOST", symbol="BTCUSDT")
        mbr.set_item_status(first["item_id"], "running", job_id=job_id)
        finish_ml_job(job_id, status="done", result={"ok": True})

        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        with patch.object(mbr, "run_batch", side_effect=_never):
            self.assertTrue(mbr.ensure_batch_runner(bid, None))
            task = mbr._runner_tasks.get(bid)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        item0 = mbr.get_batch(bid)["items"][0]
        self.assertEqual(item0["status"], "done")

    async def test_reconcile_leaves_item_running_while_validate_pending(self):
        from app.services.bots.ml_job_store import create_ml_job, finish_ml_job

        batch, created = mbr.create_batch("BTCUSDT", _items(1, validate_after=True))
        assert created
        bid = batch["batch_id"]
        first = batch["items"][0]
        job_id = create_ml_job(kind="train", strategy="ML_SIGNAL_BOOST", symbol="BTCUSDT")
        mbr.set_item_status(first["item_id"], "running", job_id=job_id)
        finish_ml_job(job_id, status="done", result={"ok": True})

        # Train done but walk-forward still ahead of the live runner.
        mbr.reconcile_batch_items(bid)
        item0 = mbr.get_batch(bid)["items"][0]
        self.assertEqual(item0["status"], "running")

        # Once the item links a finished *validate* job, reconcile finalizes.
        vjob = create_ml_job(kind="validate", strategy="ML_SIGNAL_BOOST", symbol="BTCUSDT")
        mbr.set_item_job_id(first["item_id"], vjob)
        finish_ml_job(vjob, status="done", result={"ok": True})
        mbr.reconcile_batch_items(bid)
        item0 = mbr.get_batch(bid)["items"][0]
        self.assertEqual(item0["status"], "done")

    async def test_guarded_cancel_logs_and_leaves_batch_resumable(self):
        batch = self._create(1)
        bid = batch["batch_id"]
        with patch.object(
            mbr, "run_batch", new=AsyncMock(side_effect=asyncio.CancelledError()),
        ):
            with self.assertLogs(mbr.logger, level="WARNING") as logs:
                with self.assertRaises(asyncio.CancelledError):
                    await mbr._run_batch_guarded(bid, None)
        self.assertTrue(any("cancelled mid-flight" in m for m in logs.output))
        # Not marked failed — the batch stays resumable for ensure/recovery.
        self.assertEqual(mbr.get_batch(bid)["status"], "queued")
        self.assertNotIn(bid, mbr._runner_tasks)

    async def test_respawn_cooldown_blocks_immediate_second_spawn(self):
        batch = self._create(2)
        bid = batch["batch_id"]

        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        with patch.object(mbr, "run_batch", side_effect=_never):
            self.assertTrue(mbr.ensure_batch_runner(bid, None))
            task = mbr._runner_tasks.get(bid)
            # Let the task start so its finally runs on cancel (pops the entry).
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            # Task gone, pending work remains — but the cooldown window
            # refuses an immediate respawn (crash-loop protection).
            self.assertNotIn(bid, mbr._runner_tasks)
            self.assertFalse(mbr.ensure_batch_runner(bid, None))
            # After the cooldown the heal path works again.
            mbr._last_spawn_attempt[bid] -= mbr.RESPAWN_COOLDOWN_SEC + 1
            self.assertTrue(mbr.ensure_batch_runner(bid, None))
            task = mbr._runner_tasks.get(bid)
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_resume_retries_after_failed_recovery_scan(self):
        batch = self._create(1)
        bid = batch["batch_id"]

        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        with (
            patch.object(mbr, "run_batch", side_effect=_never),
            patch.object(
                mbr, "recover_interrupted_batches", side_effect=RuntimeError("db busy"),
            ),
        ):
            self.assertEqual(mbr.resume_incomplete_batches(None), [])
        # Guard reopened — a later call runs recovery for real.
        with patch.object(mbr, "run_batch", side_effect=_never):
            self.assertEqual(mbr.resume_incomplete_batches(None), [bid])
            task = mbr._runner_tasks.get(bid)
            self.assertIsNotNone(task)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class IsBatchStalledTests(unittest.TestCase):
    def _batch(self, **over):
        base = {
            "status": "queued",
            "cancel_requested": False,
            "updated_at": "2026-08-19T20:00:00Z",
            "items": [
                {"status": "done"},
                {"status": "pending"},
                {"status": "pending"},
            ],
        }
        base.update(over)
        return base

    def test_stalled_when_queued_past_threshold(self):
        import datetime as _dt

        updated = "2026-08-19T20:00:00Z"
        now = _dt.datetime(2026, 8, 19, 20, 10, tzinfo=_dt.timezone.utc).timestamp()
        self.assertTrue(mbr.is_batch_stalled(self._batch(), now=now))
        self.assertEqual(
            mbr.is_batch_stalled(self._batch(updated_at=updated), now=now, threshold_sec=300),
            True,
        )

    def test_not_stalled_within_threshold(self):
        import datetime as _dt

        now = _dt.datetime(2026, 8, 19, 20, 4, tzinfo=_dt.timezone.utc).timestamp()
        self.assertFalse(mbr.is_batch_stalled(self._batch(), now=now))

    def test_not_stalled_when_item_running(self):
        import datetime as _dt

        now = _dt.datetime(2026, 8, 19, 21, 0, tzinfo=_dt.timezone.utc).timestamp()
        batch = self._batch(items=[{"status": "done"}, {"status": "running"}, {"status": "pending"}])
        self.assertFalse(mbr.is_batch_stalled(batch, now=now))

    def test_not_stalled_when_terminal_or_cancelled(self):
        import datetime as _dt

        now = _dt.datetime(2026, 8, 19, 21, 0, tzinfo=_dt.timezone.utc).timestamp()
        self.assertFalse(mbr.is_batch_stalled(self._batch(status="done"), now=now))
        self.assertFalse(mbr.is_batch_stalled(self._batch(cancel_requested=True), now=now))
        self.assertFalse(mbr.is_batch_stalled(None, now=now))
        self.assertFalse(mbr.is_batch_stalled(self._batch(updated_at="garbage"), now=now))


class LatestActiveBatchTests(_BatchTestBase):
    async def test_returns_latest_non_terminal_batch(self):
        old = self._create(1)
        async def exec_ok(b, item):
            return {"ok": True}

        with patch(
            "app.services.bots.ml_train_executor.resolve_ml_train_max_workers",
            return_value=1,
        ):
            await mbr.run_batch(old["batch_id"], item_executor=exec_ok)
        self.assertEqual(mbr.get_batch(old["batch_id"])["status"], "done")

        live = self._create(2)
        found = mbr.latest_active_batch()
        self.assertIsNotNone(found)
        self.assertEqual(found["batch_id"], live["batch_id"])

    async def test_symbol_filter_and_none_when_all_terminal(self):
        batch, created = mbr.create_batch("ETHUSDT", _items(1))
        assert created
        found = mbr.latest_active_batch("ETHUSDT")
        self.assertEqual(found["batch_id"], batch["batch_id"])
        self.assertIsNone(mbr.latest_active_batch("BTCUSDT"))

        async def exec_ok(b, item):
            return {"ok": True}

        with patch(
            "app.services.bots.ml_train_executor.resolve_ml_train_max_workers",
            return_value=1,
        ):
            await mbr.run_batch(batch["batch_id"], item_executor=exec_ok)
        self.assertIsNone(mbr.latest_active_batch())


class ItemStatusGuardTests(_BatchTestBase):
    async def test_guarded_update_never_clobbers_terminal_state(self):
        batch = self._create(1)
        bid = batch["batch_id"]
        item = batch["items"][0]

        mbr.set_item_status(item["item_id"], "done")
        mbr.set_item_status_if_not_terminal(item["item_id"], "cancelled", error="cancelled")
        after = mbr.get_batch(bid)["items"][0]
        self.assertEqual(after["status"], "done")

        mbr.set_item_status(item["item_id"], "running")
        mbr.set_item_status_if_not_terminal(item["item_id"], "cancelled", error="cancelled")
        after = mbr.get_batch(bid)["items"][0]
        self.assertEqual(after["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
