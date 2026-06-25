import asyncio
import time
import logging
from collections import defaultdict

class DomainLimiter:
    def __init__(self, delay=2.5):
        self.delay = delay
        self.last_call = defaultdict(float)
        self.lock = asyncio.Lock()

    async def wait(self, domain: str):
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_call[domain]
            wait_time = max(0, self.delay - elapsed)
            self.last_call[domain] = now + wait_time

        if wait_time > 0:
            logging.info(f"[DomainLimiter] Aguardando {wait_time:.2f}s para o domínio: {domain}")
            await asyncio.sleep(wait_time)


class CircuitBreaker:
    def __init__(self, threshold=3, cooldown=60):
        self.failures = 0
        self.threshold = threshold
        self.cooldown = cooldown
        self.opened_at = None
        self.lock = asyncio.Lock()

    async def record_failure(self):
        async with self.lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.time()
                logging.warning("[CircuitBreaker] CIRCUITO ABERTO (FlareSolverr suspenso por falhas)")

    async def record_success(self):
        async with self.lock:
            self.failures = 0
            self.opened_at = None

    async def can_execute(self) -> bool:
        async with self.lock:
            if self.opened_at is None:
                return True

            if time.time() - self.opened_at > self.cooldown:
                logging.info("[CircuitBreaker] CIRCUITO MEIO-ABERTO → Testando FlareSolverr novamente")
                self.failures = 0
                self.opened_at = None
                return True

            return False
