"""HTTP-загрузчик: вежливый темп по хостам, повторы с backoff, robots.txt, условные запросы.

Сознательно НЕ делает: ротацию прокси и отпечатков браузера, решение капч, подмену
User-Agent под реальный браузер. Если площадка отвечает защитой от ботов, загрузчик
останавливает работу с этим хостом и сообщает об этом (BlockedError).
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from arte.config import Settings, get_settings
from arte.fetch.robots import RobotsCache

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
# 439 — код файрвола Avito.
BLOCK_STATUSES = {401, 403, 439}
BLOCK_MARKERS = ("captcha", "капч", "are you a robot", "доступ ограничен", "access denied",
                 "проверка безопасности")


def looks_blocked(status: int, url: str, html: str) -> bool:
    """Страница защиты от ботов: по коду, по адресу редиректа (cian-captcha) или по короткому тексту-заглушке."""
    if status in BLOCK_STATUSES or "captcha" in url.lower():
        return True
    if status == 200 and len(html) < 20_000:
        lower = html.lower()
        return any(m in lower for m in BLOCK_MARKERS)
    return False


class DisallowedError(Exception):
    """URL запрещён robots.txt."""


class BlockedError(Exception):
    """Площадка показала защиту от ботов — дальнейшие запросы к хосту прекращены."""


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    not_modified: bool = False
    # Заполняются бэкендами, которые умеют больше, чем просто скачать HTML (Firecrawl).
    links: list[str] = field(default_factory=list)
    extracted: dict | None = None


class Fetcher:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings or get_settings()
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            },
            timeout=self.settings.request_timeout,
            follow_redirects=True,
            proxy=self.settings.proxy,
            transport=transport,
        )
        self._robots = RobotsCache(self._client, self.settings.user_agent)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._blocked_hosts: set[str] = set()
        self._validators: dict[str, dict[str, str]] = {}
        self._sem = asyncio.Semaphore(self.settings.concurrency)

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _wait_turn(self, host: str, url: str) -> None:
        delay = self.settings.per_host_delay
        if self.settings.respect_robots_txt:
            robots_delay = await self._robots.crawl_delay(url)
            if robots_delay:
                delay = max(delay, robots_delay)
        last = self._host_last.get(host)
        if last is not None:
            # Небольшой джиттер, чтобы запросы от нескольких задач не шли строем.
            wait = last + delay * random.uniform(1.0, 1.3) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        self._host_last[host] = time.monotonic()

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if not value:
            return None
        if value.isdigit():
            return float(value)
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None

    async def get(self, url: str, *, conditional: bool = True, **_) -> FetchResult:
        host = urlsplit(url).netloc
        if host in self._blocked_hosts:
            raise BlockedError(host)
        if self.settings.respect_robots_txt and not await self._robots.allowed(url):
            raise DisallowedError(url)

        headers = dict(self._validators.get(url, {})) if conditional else {}
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with self._sem:
            for attempt in range(self.settings.max_retries + 1):
                async with lock:
                    await self._wait_turn(host, url)
                    try:
                        resp = await self._client.get(url, headers=headers)
                    except httpx.TransportError as e:
                        if attempt == self.settings.max_retries:
                            raise
                        log.warning("network error %s on %s, retrying", e, url)
                        resp = None
                if resp is None:
                    await asyncio.sleep(2**attempt)
                    continue
                if resp.status_code in RETRY_STATUSES and attempt < self.settings.max_retries:
                    wait = self._retry_after(resp) or 2 ** (attempt + 1)
                    log.warning("HTTP %s on %s, retry in %.0fs", resp.status_code, url, wait)
                    await asyncio.sleep(wait)
                    continue
                break

        if looks_blocked(resp.status_code, str(resp.url), resp.text):
            self._blocked_hosts.add(host)
            raise BlockedError(f"{host}: HTTP {resp.status_code}, похоже на защиту от ботов")

        validators = {}
        if etag := resp.headers.get("ETag"):
            validators["If-None-Match"] = etag
        if modified := resp.headers.get("Last-Modified"):
            validators["If-Modified-Since"] = modified
        if validators:
            self._validators[url] = validators

        return FetchResult(
            url=str(resp.url),
            status=resp.status_code,
            text="" if resp.status_code == 304 else resp.text,
            headers=dict(resp.headers),
            not_modified=resp.status_code == 304,
        )
