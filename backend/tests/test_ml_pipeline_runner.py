"""Durable ML full-pipeline runner."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.database import ensure_ml_pipeline_tables
from app.db.connection import get_connection
from app.services.bots import ml_pipeline_runner as mpr


class _PipelineTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ensure_ml_pipeline_tables()
        mpr.reset_ml_pipeline_runner_for_tests()
        self._wipe()

    def tearDown(self):
        self._wipe()
        mpr.reset_ml_pipeline_runner_for_tests()

    @staticmethod
    def _wipe():
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM ml_pipeline_events")
            cur.execute("DELETE FROM ml_pipeline_runs")
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def _create(self, **kwargs):
        return mpr.create_pipeline_run(
            symbol=kwargs.pop("symbol", "ETHUSDT"),
            strategy=kwargs.pop("strategy", "ML_SIGNAL_BOOST"),
            timeframe=kwargs.pop("timeframe", "1m"),
            **kwargs,
        )


class CreateAndFlowTests(_PipelineTestBase):
    def test_research_starts_at_search(self):
        run = self._create(profile="research")
        self.assertEqual(run["stage"], "SEARCH")
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["profile"], "research")

    def test_retrain_skips_search(self):
        run = self._create(profile="retrain")
        self.assertEqual(run["stage"], "TRAINING")
        self.assertEqual(mpr.first_stage("retrain"), "TRAINING")
        self.assertNotIn("SEARCH", mpr.stage_flow("retrain"))

    def test_stop_after_validate_flow_ends_at_validating(self):
        flow = mpr.stage_flow("research", stop_after_validate=True)
        self.assertEqual(flow[-1], "VALIDATING")
        self.assertIsNone(mpr.next_stage("research", "VALIDATING", stop_after_validate=True))

    async def test_stage_order_research(self):
        seen = []

        async def executor(run, stage):
            seen.append(stage)
            if stage == "GATE_CHECK":
                return {"ok": True, "result": {"blocking": False, "passed": True}}
            if stage == "READY_TO_DEPLOY":
                return {"ok": True, "deployed": True, "bot_id": "bot-1"}
            return {"ok": True, "result": {"ok": True, "stage": stage}, "job_id": f"j-{stage}"}

        run = self._create(profile="research")
        out = await mpr.run_pipeline(run["pipeline_id"], stage_executor=executor)
        self.assertEqual(
            seen,
            ["SEARCH", "TRAINING", "VALIDATING", "BACKTESTING", "GATE_CHECK", "READY_TO_DEPLOY"],
        )
        self.assertEqual(out["stage"], "DEPLOYED")
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["bot_id"], "bot-1")

    async def test_retrain_skips_search_in_runner(self):
        seen = []

        async def executor(run, stage):
            seen.append(stage)
            if stage == "READY_TO_DEPLOY":
                return {"ok": True, "deployed": True, "bot_id": "b"}
            if stage == "GATE_CHECK":
                return {"ok": True, "result": {"blocking": False}}
            return {"ok": True, "result": {"ok": True}}

        run = self._create(profile="retrain")
        await mpr.run_pipeline(run["pipeline_id"], stage_executor=executor)
        self.assertEqual(seen[0], "TRAINING")
        self.assertNotIn("SEARCH", seen)

    async def test_stop_after_validate_completes(self):
        seen = []

        async def executor(run, stage):
            seen.append(stage)
            return {"ok": True, "result": {"ok": True}}

        run = self._create(profile="research", stop_after_validate=True)
        out = await mpr.run_pipeline(run["pipeline_id"], stage_executor=executor)
        self.assertEqual(seen, ["SEARCH", "TRAINING", "VALIDATING"])
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["stage"], "VALIDATING")
        self.assertIsNotNone(out["completed_at"])

    async def test_cancel_stops_runner(self):
        entered = asyncio_event = None
        import asyncio as _aio

        entered = _aio.Event()

        async def executor(run, stage):
            entered.set()
            await _aio.sleep(0.05)
            if mpr.is_pipeline_cancel_requested(run["pipeline_id"]):
                return {"ok": False, "cancelled": True, "error": "cancelled"}
            return {"ok": True, "result": {"ok": True}}

        run = self._create()
        task = _aio.create_task(
            mpr.run_pipeline(run["pipeline_id"], stage_executor=executor)
        )
        await entered.wait()
        cancelled = mpr.cancel_pipeline(run["pipeline_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        out = await task
        self.assertEqual(out["status"], "cancelled")

    async def test_approval_pauses_then_approve_deploys(self):
        async def executor(run, stage):
            if stage == "GATE_CHECK":
                return {"ok": True, "result": {"blocking": False, "passed": True}}
            if stage == "READY_TO_DEPLOY":
                approved = bool((run.get("config") or {}).get("deploy_approved"))
                if not approved:
                    return {"ok": True, "awaiting_approval": True}
                return {"ok": True, "deployed": True, "bot_id": "approved-bot"}
            return {"ok": True, "result": {"ok": True}}

        run = self._create(auto_deploy_mode="approval", profile="retrain")
        out = await mpr.run_pipeline(run["pipeline_id"], stage_executor=executor)
        self.assertEqual(out["status"], "waiting_approval")
        self.assertTrue(out["pending_approval"])
        self.assertEqual(out["stage"], "READY_TO_DEPLOY")

        approved = mpr.approve_pipeline(run["pipeline_id"])
        self.assertFalse(approved["pending_approval"])
        out = await mpr.run_pipeline(run["pipeline_id"], stage_executor=executor)
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["bot_id"], "approved-bot")

    async def test_gate_failure(self):
        async def executor(run, stage):
            if stage == "GATE_CHECK":
                return {"ok": False, "blocking": True, "error": "too few trades", "result": {"blocking": True}}
            return {"ok": True, "result": {"ok": True}}

        run = self._create(profile="retrain")
        out = await mpr.run_pipeline(run["pipeline_id"], stage_executor=executor)
        self.assertEqual(out["stage"], "GATE_FAILED")
        self.assertEqual(out["status"], "gate_failed")
        self.assertIn("trades", out["last_error"])

    async def test_stage_failure_is_retryable(self):
        calls = {"n": 0}

        async def executor(run, stage):
            if stage == "TRAINING":
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"ok": False, "error": "boom"}
            if stage == "GATE_CHECK":
                return {"ok": True, "result": {"blocking": False}}
            if stage == "READY_TO_DEPLOY":
                return {"ok": True, "deployed": True, "bot_id": "x"}
            return {"ok": True, "result": {"ok": True}}

        run = self._create(profile="retrain")
        out = await mpr.run_pipeline(run["pipeline_id"], stage_executor=executor)
        self.assertEqual(out["status"], "failed")
        retried = mpr.retry_pipeline(run["pipeline_id"])
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried.get("requeued"), 1)
        out = await mpr.run_pipeline(run["pipeline_id"], stage_executor=executor)
        self.assertEqual(out["status"], "done")

    async def test_crash_resume_without_child_job_requeues(self):
        run = self._create(profile="retrain")
        mpr._patch_run(run["pipeline_id"], status="running", stage="TRAINING")
        resumed = mpr.recover_interrupted_pipelines()
        self.assertIn(run["pipeline_id"], resumed)
        live = mpr.get_pipeline(run["pipeline_id"])
        self.assertEqual(live["status"], "queued")

    async def test_crash_with_orphaned_job_marks_failed(self):
        run = self._create(profile="retrain")
        mpr._patch_run(run["pipeline_id"], status="running", stage="TRAINING", train_job_id="missing-job")
        with patch("app.services.bots.ml_job_store.get_ml_job", return_value=None):
            with patch("app.services.bots.ml_job_store.load_ml_job_checkpoint", return_value=None):
                mpr.recover_interrupted_pipelines()
        live = mpr.get_pipeline(run["pipeline_id"])
        self.assertEqual(live["status"], "failed")
        self.assertEqual(live["last_error"], "server restarted")

    def test_public_snapshot_aliases(self):
        run = self._create()
        pub = mpr.public_pipeline(run)
        self.assertEqual(pub["pipelineId"], run["pipeline_id"])
        self.assertTrue(pub["ownedByServer"])
        self.assertEqual(pub["autoAdvance"], True)

    def test_latest_active_filters_symbol(self):
        a = self._create(symbol="ETHUSDT")
        self._create(symbol="BTCUSDT")
        hit = mpr.latest_active_pipeline("ETHUSDT")
        self.assertEqual(hit["pipeline_id"], a["pipeline_id"])

    def test_paper_execution_helper(self):
        self.assertTrue(mpr.is_paper_execution(execution_mode="paper"))
        self.assertFalse(mpr.is_paper_execution(execution_mode="broker"))


class DrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_drain_creates_retrain_pipeline(self):
        from app.services.bots.ml_retrain_scheduler import MlRetrainScheduler, drain_one_pending_retrain

        sched = MlRetrainScheduler()
        sched.request_retrain("ML_SIGNAL_BOOST", "ETHUSDT", "stale", "test", timeframe="5m")
        created = {}

        def _create(**kwargs):
            created.update(kwargs)
            return {"pipeline_id": "pipe-drain", "stage": "TRAINING", "status": "queued"}

        with patch(
            "app.services.bots.ml_retrain_scheduler.get_retrain_scheduler",
            return_value=sched,
        ):
            with patch(
                "app.services.bots.ml_pipeline_runner.create_pipeline_run",
                side_effect=_create,
            ):
                with patch(
                    "app.services.bots.ml_pipeline_runner.start_pipeline_runner",
                    return_value=True,
                ) as start:
                    out = await drain_one_pending_retrain(None)
        self.assertTrue(out["ok"])
        self.assertEqual(out["pipeline_id"], "pipe-drain")
        self.assertEqual(created.get("profile"), "retrain")
        start.assert_called_once()


class ResearchConfigTests(unittest.TestCase):
    def test_research_stage_config_forces_calendar_and_pbo(self):
        cfg = mpr.research_stage_config({"dq_train_gate": "warn"}, profile="research")
        self.assertTrue(cfg["ml_calendar_holdout"])
        self.assertEqual(cfg["dq_train_gate"], "block")
        self.assertEqual(cfg["pbo_profile"], "research")
        self.assertTrue(cfg["force_pbo"])

    def test_retrain_config_warm_starts(self):
        cfg = mpr.research_stage_config({}, profile="retrain")
        self.assertTrue(cfg.get("retrain_from_live_model"))


class ValidateJobBindTests(_PipelineTestBase):
    async def test_execute_validate_records_job_id_before_submit(self):
        from app.services.bots.ml_job_store import get_ml_job, reset_ml_job_store_for_tests

        reset_ml_job_store_for_tests()
        run = self._create(strategy="LSTM_DIRECTION", symbol="SOLUSDT")
        seen: dict = {}

        async def fake_fetch(state, symbol, strategy, cfg, *, purpose):
            live = mpr.get_pipeline(run["pipeline_id"])
            seen["id_during_fetch"] = live.get("validate_job_id")
            return [], cfg

        async def fake_submit(*_a, **kwargs):
            live = mpr.get_pipeline(run["pipeline_id"])
            seen["id_during_submit"] = live.get("validate_job_id")
            seen["passed_job_id"] = kwargs.get("job_id")
            return {"ok": True, "job_id": kwargs.get("job_id")}

        try:
            with patch.object(mpr, "_fetch_and_enrich", fake_fetch):
                with patch(
                    "app.services.bots.ml_train_executor.submit_validate_job",
                    fake_submit,
                ):
                    out = await mpr._execute_validate(
                        None, run, {"timeframe": "5m"},
                    )
            self.assertTrue(seen["id_during_fetch"])
            self.assertEqual(seen["id_during_fetch"], seen["id_during_submit"])
            self.assertEqual(seen["id_during_submit"], seen["passed_job_id"])
            self.assertEqual(out["job_id"], seen["passed_job_id"])
            self.assertIsNotNone(get_ml_job(out["job_id"]))
        finally:
            reset_ml_job_store_for_tests()
