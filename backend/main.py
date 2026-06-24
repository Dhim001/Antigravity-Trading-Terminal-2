import importlib.metadata
import asyncio
import logging
from app.server import main, _shutdown

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopping (graceful shutdown)...")
        try:
            asyncio.run(_shutdown())
        except Exception:
            pass
        logging.info("Server stopped.")
