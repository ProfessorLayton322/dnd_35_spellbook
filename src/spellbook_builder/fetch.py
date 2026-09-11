from __future__ import annotations

import time
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class FetchError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Fetcher:
    timeout: float = 25.0
    delay: float = 0.15
    user_agent: str = "LocalDND35SpellbookBuilder/0.1 (personal offline indexing)"
    retries: int = 3

    def __post_init__(self) -> None:
        retry = Retry(
            total=self.retries,
            connect=self.retries,
            read=self.retries,
            status=self.retries,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            backoff_factor=0.6,
            respect_retry_after_header=True,
        )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self._last_fetch = 0.0

    def get(self, url: str) -> str:
        wait = self.delay - (time.monotonic() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)
        try:
            response = self.session.get(url, timeout=self.timeout)
            self._last_fetch = time.monotonic()
            response.raise_for_status()
        except requests.RequestException as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            raise FetchError(f"Could not fetch {url}: {exc}", status_code=status_code) from exc
        if not response.content:
            raise FetchError(f"Remote page was empty: {url}")
        response.encoding = response.apparent_encoding or response.encoding or "utf-8"
        return response.text
