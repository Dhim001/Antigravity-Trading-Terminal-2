import time
import asyncio
import logging
from functools import wraps
from app.services.notifications.dispatcher import emit_notification
from app.services.notifications.events import NotificationEvent
from app.services.notifications import types as ntypes

logger = logging.getLogger(__name__)

class CircuitOpenException(Exception):
    pass

class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: float = 30.0, expected_exception: type = Exception):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF-OPEN

    def _trip(self):
        self.state = "OPEN"
        self.last_failure_time = time.time()
        logger.error("Circuit breaker '%s' tripped! Pausing for %s seconds.", self.name, self.recovery_timeout)
        
        # Emit a system notification asynchronously
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                emit_notification(
                    NotificationEvent(
                        event_type=ntypes.SYSTEM_ALERT,
                        title=f"API Circuit Breaker Tripped: {self.name}",
                        body=f"The circuit breaker detected multiple consecutive failures and is now OPEN. Traffic will be blocked for {self.recovery_timeout} seconds.",
                        severity="error",
                        payload={"circuit_name": self.name}
                    )
                )
            )
        except RuntimeError:
            pass # No running loop

    def _success(self):
        if self.state != "CLOSED":
            logger.info("Circuit breaker '%s' fully RECOVERED. State is now CLOSED.", self.name)
        self.failure_count = 0
        self.state = "CLOSED"

    def __call__(self, func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await self._execute_async(func, *args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            return self._execute_sync(func, *args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    async def _execute_async(self, func, *args, **kwargs):
        self._check_state()
        try:
            result = await func(*args, **kwargs)
            if self.state == "HALF-OPEN":
                self._success()
            return result
        except self.expected_exception as e:
            self._handle_failure()
            raise e

    def _execute_sync(self, func, *args, **kwargs):
        self._check_state()
        try:
            result = func(*args, **kwargs)
            if self.state == "HALF-OPEN":
                self._success()
            return result
        except self.expected_exception as e:
            self._handle_failure()
            raise e

    def _check_state(self):
        if self.state == "OPEN":
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = "HALF-OPEN"
                logger.info("Circuit breaker '%s' entering HALF-OPEN state to test connection.", self.name)
            else:
                raise CircuitOpenException(f"Circuit breaker '{self.name}' is OPEN. Failing fast.")

    def _handle_failure(self):
        self.failure_count += 1
        if self.state == "HALF-OPEN" or self.failure_count >= self.failure_threshold:
            self._trip()

# Default global circuit breakers for external API groups
alpaca_breaker = CircuitBreaker("AlpacaAPI", failure_threshold=3, recovery_timeout=60.0)
finnhub_breaker = CircuitBreaker("FinnhubAPI", failure_threshold=5, recovery_timeout=30.0)
