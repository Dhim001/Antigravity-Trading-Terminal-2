import sys
import os
import asyncio

# Ensure backend directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.agent.copilot import agent_narrate_event
from app.database import init_db

async def main():
    init_db()
    print("Simulating RiskSentinel event...")
    await agent_narrate_event("RiskSentinel", {
        "action": "paused_all_bots",
        "reason": "Drawdown velocity breached 5.0% limit (TEST SIMULATION).",
        "bots_paused": 2,
        "current_drawdown": 5.2
    })
    
    await asyncio.sleep(2)
    
    print("Simulating AlphaDecay event...")
    await agent_narrate_event("AlphaDecay", {
        "action": "decay_detected",
        "bot_id": "bot-test-123",
        "symbol": "ETHUSDT",
        "reasons": ["Meta-Label Drift: Avg P(win) dropped to 0.40 (expected 0.65)"],
        "auto_paused": True,
        "auto_retrained": False
    })
    
    await asyncio.sleep(2)
    
    print("Simulating RegimeRotation event...")
    await agent_narrate_event("RegimeRotation", {
        "action": "rotated_strategy",
        "bot_id": "bot-test-456",
        "symbol": "BTCUSDT",
        "from_strategy": "MACD_RSI",
        "to_strategy": "SUPERTREND_ADX",
        "regime": "trending"
    })
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
