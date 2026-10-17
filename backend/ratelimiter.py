import time
from collections import deque
from threading import Lock

from pandas._libs.tslibs import timestamps


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.lock = Lock()
        self.history = {}

    def is_allowed(self, client_key) -> bool:
        now = time.time()

        with self.lock:
            if client_key not in self.history:
                self.history[client_key] = deque()

        timestamps = self.history[client_key]

        while timestamps and timestamps[0] < now - self.window_seconds:
            timestamps.popleft()

        if len(timestamps) < self.max_requests:
            timestamps.append(now)
            return True

        return False


#In-Memory Sliding Window Rate Limiter.