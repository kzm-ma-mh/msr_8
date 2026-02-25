"""
مدیریت محدودیت نرخ API گیت‌هاب
"""

import time
from datetime import datetime, timezone

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from config.settings import GITHUB_HEADERS, GITHUB_API_BASE
from utils.logger import log


class RateLimitExceeded(Exception):
    pass


class GitHubRateLimiter:
    """مدیریت هوشمند Rate Limit"""

    def __init__(self):
        self.remaining: int = 5000
        self.limit: int = 5000
        self.reset_time: float = 0
        self.search_remaining: int = 30
        self.search_reset_time: float = 0
        self._session = requests.Session()
        self._session.headers.update(GITHUB_HEADERS)

    def update_from_headers(self, headers: dict) -> None:
        self.remaining = int(headers.get("X-RateLimit-Remaining", self.remaining))
        self.limit = int(headers.get("X-RateLimit-Limit", self.limit))
        self.reset_time = float(headers.get("X-RateLimit-Reset", self.reset_time))

    def wait_if_needed(self, is_search: bool = False) -> None:
        """صبر هوشمند تا بازنشانی سهمیه"""
        threshold = 5 if is_search else 10
        remaining = self.search_remaining if is_search else self.remaining
        reset = self.search_reset_time if is_search else self.reset_time

        if remaining > threshold:
            return

        now = time.time()
        wait_seconds = max(0, reset - now) + 5

        if wait_seconds > 0:
            reset_dt = datetime.fromtimestamp(
                reset, tz=timezone.utc
            ).strftime("%H:%M:%S UTC")
            kind = "Search" if is_search else "Core"
            log.warning(
                f"⏳ {kind} Rate Limit: remaining={remaining} | "
                f"صبر {wait_seconds:.0f}s تا {reset_dt}"
            )
            time.sleep(wait_seconds)

    def check_rate_limit(self) -> dict:
        """بررسی وضعیت فعلی"""
        resp = self._session.get(f"{GITHUB_API_BASE}/rate_limit")
        if resp.status_code == 200:
            data = resp.json()
            core = data.get("resources", {}).get("core", {})
            search = data.get("resources", {}).get("search", {})

            self.remaining = core.get("remaining", self.remaining)
            self.search_remaining = search.get("remaining", self.search_remaining)
            self.reset_time = core.get("reset", self.reset_time)
            self.search_reset_time = search.get("reset", self.search_reset_time)

            log.info(
                f"📊 Rate Limit — Core: {core.get('remaining')}/{core.get('limit')} | "
                f"Search: {search.get('remaining')}/{search.get('limit')}"
            )
            return data
        return {}

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=120),
        retry=retry_if_exception_type((requests.ConnectionError, RateLimitExceeded)),
        before_sleep=lambda rs: log.warning(
            f"🔄 تلاش مجدد ({rs.attempt_number}/5)..."
        ),
    )
    def request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json_data: dict | None = None,
        is_search: bool = False,
    ) -> requests.Response:
        """ارسال درخواست با مدیریت Rate Limit"""

        self.wait_if_needed(is_search=is_search)

        full_url = url if url.startswith("http") else f"{GITHUB_API_BASE}{url}"

        response = self._session.request(
            method=method, url=full_url,
            params=params, json=json_data, timeout=30,
        )

        self.update_from_headers(response.headers)

        if response.status_code == 403:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                time.sleep(int(retry_after) + 1)
                raise RateLimitExceeded("Secondary rate limit")
            if self.remaining == 0:
                raise RateLimitExceeded("Primary rate limit exceeded")

        if response.status_code == 429:
            raise RateLimitExceeded("Too many requests")

        return response

    def get(
        self, url: str, params: dict | None = None, is_search: bool = False
    ) -> requests.Response:
        return self.request("GET", url, params=params, is_search=is_search)