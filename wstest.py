import asyncio
import websockets
import json

async def test():
    try:
        async with websockets.connect('ws://127.0.0.1:8765') as ws:
            print('Connected. Waiting for copilot messages...')
            for _ in range(5000):
                msg = await asyncio.wait_for(ws.recv(), timeout=40)
                try:
                    if isinstance(msg, bytes):
                        continue # ignore msgpack for this test
                    parsed = json.loads(msg)
                    if parsed.get("type") == "copilot_agent_message":
                        print("RECEIVED COPILOT MESSAGE:", parsed)
                        return
                except Exception:
                    pass
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
