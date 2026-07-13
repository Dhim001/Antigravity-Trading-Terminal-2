import asyncio
import os
import sys

# Ensure backend is in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.services.market.oms import SimulatedOMS
from app.services.bots.manager import BotManager
from app.services.agent.copilot import _tool_scan_market

async def main():
    oms = SimulatedOMS()
    bot_manager = BotManager(oms, sqlite_path="trading-sim.db")
    res = await _tool_scan_market(bot_manager)
    print(res)

if __name__ == "__main__":
    asyncio.run(main())
