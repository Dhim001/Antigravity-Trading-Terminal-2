import os
import sys
import logging
from redis import Redis
from rq import Worker, Queue, Connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] RQ Worker: %(message)s")

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
listen = ['default', 'backtest']

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    conn = Redis.from_url(redis_url)
    with Connection(conn):
        worker = Worker(map(Queue, listen))
        worker.work()
