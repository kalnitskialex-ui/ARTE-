"""Бэкенд загрузки через Scrapling.

Режимы:
- http    — AsyncFetcher (curl_cffi, HTTP/2–3), быстрый, без JavaScript;
- dynamic — DynamicFetcher (Playwright/Chromium) для страниц, отрисовываемых JS.

StealthyFetcher (подмена отпечатков, автоматическое прохождение Cloudflare) здесь
намеренно не подключён: см. README, раздел «Правовые рамки». Мы представляемся
своим User-Agent, соблюдаем robots.txt и темп; при защите от ботов — останавливаемся.
"""

import asyncio
import time
from urllib.parse import urlsplit

import httpx

from arte.config import Settings, get_settings
from arte.fetch.http import BLOCK_MARKERS, BlockedError, DisallowedError, FetchResult
from arte.fetch.robots import RobotsCache


class ScraplingFetcher:
    def __init__(self, settings: Settings | None = None, mode: str | None = None):
        self.settings = settings or get_settings()
        self.mode = mode or self.settings.scrapling_mode
        if self.mode not in ("http", "dynamic"):
            raise ValueError("scrapling_mode: http | dynamic")
        self._http = httpx.AsyncClient(headers={"User-Agent": self.settings.user_agent}, timeout=15)
        self._robots = RobotsCache(self._http, self.settings.user_agent)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._blocked_hosts: set[str] = set()

    async def __aenter__(self) -> "ScraplingFetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _fetch(self, url: str):
        s = self.settings
        if self.mode == "http":
            from scrapling.fetchers import AsyncFetcher

            return await AsyncFetcher.get(
                url,
                headers={"User-Agent": s.user_agent, "Accept-Language": "ru-RU,ru;q=0.9"},
                stealthy_headers=False,
                impersonate=None,
                timeout=s.request_timeout,
                retries=s.max_retries,
                proxy=s.proxy,
            )
        from scrapling.fetchers import DynamicFetcher

        return await DynamicFetcher.async_fetch(
            url,
            headless=True,
            useragent=s.user_agent,
            locale="ru-RU",
            network_idle=True,
            disable_resources=True,
            block_ads=True,
            timeout=s.request_timeout * 1000,
            retries=s.max_retries,
            proxy=s.proxy,
        )

    async def get(self, url: str, **_) -> FetchResult:
        host = urlsplit(url).netloc
        if host in self._blocked_hosts:
            raise BlockedError(host)
        if self.settings.respect_robots_txt and not await self._robots.allowed(url):
            raise DisallowedError(url)
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._host_last.get(host)
            if last is not None and (wait := last + self.settings.per_host_delay - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()
            page = await self._fetch(url)

        html = page.html_content if hasattr(page, "html_content") else str(page.body)
        lower = html[:20_000].lower() if len(html) < 20_000 else ""
        if page.status in (401, 403) or any(m in lower for m in BLOCK_MARKERS):
            self._blocked_hosts.add(host)
            raise BlockedError(f"{host}: HTTP {page.status}, похоже на защиту от ботов")
        return FetchResult(url=str(page.url or url), status=page.status, text=html,
                           headers=dict(page.headers or {}))
