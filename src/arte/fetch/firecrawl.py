"""Бэкенд загрузки через Firecrawl (облачный api.firecrawl.dev или self-hosted).

Firecrawl сам рендерит JavaScript, поэтому один вызов отдаёт и исходный HTML (для
нашего детерминированного разбора JSON-LD/встроенного стейта), и ссылки (для
обхода поисковой выдачи), и — опционально — JSON по нашей схеме, извлечённый их LLM.

Интерфейс совместим с Fetcher: get(url) -> FetchResult. robots.txt и темп по хостам
соблюдаются на нашей стороне так же, как в HTTP-загрузчике.
"""

import asyncio
import logging
import time
from urllib.parse import urlsplit

from arte.config import Settings, get_settings
from arte.extract.schema import EXTRACTION_PROMPT, ListingExtraction
from arte.fetch.http import BlockedError, DisallowedError, FetchResult, looks_blocked
from arte.fetch.robots import RobotsCache

log = logging.getLogger(__name__)


class FirecrawlFetcher:
    def __init__(self, settings: Settings | None = None, client=None):
        import httpx
        from firecrawl import AsyncFirecrawl

        self.settings = settings or get_settings()
        if client is None and not self.settings.firecrawl_api_key and not self.settings.firecrawl_api_url:
            raise RuntimeError("Не задан ARTE_FIRECRAWL_API_KEY (или ARTE_FIRECRAWL_API_URL для self-hosted)")
        self._fc = client or AsyncFirecrawl(
            api_key=self.settings.firecrawl_api_key,
            api_url=self.settings.firecrawl_api_url or "https://api.firecrawl.dev",
            timeout=self.settings.request_timeout * 3,
        )
        self._http = httpx.AsyncClient(headers={"User-Agent": self.settings.user_agent}, timeout=15)
        self._robots = RobotsCache(self._http, self.settings.user_agent)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._blocked_hosts: set[str] = set()

    async def __aenter__(self) -> "FirecrawlFetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    def _formats(self, extract: bool) -> list:
        formats: list = ["rawHtml", "links"]
        if extract and self.settings.firecrawl_json_extract:
            formats.append({
                "type": "json",
                "prompt": EXTRACTION_PROMPT,
                "schema": ListingExtraction.model_json_schema(),
            })
        return formats

    async def get(self, url: str, *, extract: bool = True, **_) -> FetchResult:
        from firecrawl.v2.utils.error_handler import RateLimitError, WebsiteNotSupportedError

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

        for attempt in range(self.settings.max_retries + 1):
            try:
                doc = await self._fc.scrape(
                    url,
                    formats=self._formats(extract),
                    only_main_content=False,
                    location={"country": "RU", "languages": ["ru-RU"]},
                    max_age=self.settings.firecrawl_max_age_ms,
                    block_ads=True,
                )
                break
            except RateLimitError:
                if attempt == self.settings.max_retries:
                    raise
                await asyncio.sleep(2 ** (attempt + 2))
            except WebsiteNotSupportedError as e:
                self._blocked_hosts.add(host)
                raise BlockedError(f"{host}: Firecrawl не поддерживает сайт ({e})") from e

        meta = doc.metadata
        status = (meta.status_code if meta else None) or 200
        final_url = (meta.url or meta.source_url or url) if meta else url
        html = doc.raw_html or doc.html or ""
        # ЦИАН отвечает капчей с кодом 200 и редиректом на /cian-captcha/, Avito — кодом 439.
        if looks_blocked(status, final_url, html):
            self._blocked_hosts.add(host)
            raise BlockedError(f"{host}: HTTP {status}, защита от ботов ({final_url})")

        links = [link if isinstance(link, str) else getattr(link, "url", "") for link in (doc.links or [])]
        return FetchResult(
            url=final_url,
            status=status,
            text=html,
            links=[x for x in links if x],
            extracted=doc.json if isinstance(doc.json, dict) else None,
        )

    async def map_site(self, url: str, search: str | None = None, limit: int = 500) -> list[str]:
        """Быстрое получение URL сайта (sitemap + индекс Firecrawl) — для сайтов без поиска."""
        data = await self._fc.map(url, search=search, limit=limit)
        return [link.url for link in data.links]
