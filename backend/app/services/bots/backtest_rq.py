import os
import asyncio
import logging
from app.database import init_db
from app.services.events.event_bus import create_event_bus
from app.services.events import channels
from app.services.candle_feed_stub import CandleFeedStub
from app.services.sim_oms import SimulatedOMSService
from app.services.bots.backtester import BacktestManager
from app.api.handlers.bots import _execute_backtest

logger = logging.getLogger(__name__)

def run_backtest_job_rq(req: dict) -> None:
    """Entrypoint for RQ backtest worker."""
    # Ensure database is initialized in this worker process
    init_db()

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        logger.error("REDIS_URL not set for RQ backtest worker.")
        return

    # Set up mock dependencies that _execute_backtest expects
    feed = CandleFeedStub()
    oms = SimulatedOMSService(feed)
    event_bus = create_event_bus(redis_url)

    async def broadcast_cb(payload: dict):
        # By publishing to WS_BROADCAST, the server will pick it up and broadcast to WebSockets
        await event_bus.publish(channels.WS_BROADCAST, payload)

    backtester = BacktestManager(feed, broadcast_cb)

    # Mock RequestContext
    class MockWebsocket:
        pass

    class MockContext:
        def __init__(self):
            self.websocket = MockWebsocket()
            self.oms = oms
            self.backtester = backtester

    ctx = MockContext()

    async def _run():
        await feed.start()
        await oms.initialize()
        await event_bus.start()
        try:
            await _execute_backtest(ctx, **req)
        except Exception as e:
            logger.exception("Failed to execute backtest in RQ worker")
        finally:
            await event_bus.stop()

    asyncio.run(_run())
