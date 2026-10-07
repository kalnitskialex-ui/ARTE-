"""Оркестратор: выдача -> ссылки на объявления -> страницы -> извлечение -> БД -> уведомления."""

import asyncio
import logging
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from arte.config import Settings, get_settings
from arte.extract.adaptive import AdaptiveRules
from arte.extract.pipeline import extract_listing
from arte.fetch.http import BlockedError, DisallowedError, Fetcher
from arte.jobs import Job
from arte.sources import get_source, source_for_url
from arte.storage import repo
from arte.storage.db import Event, sessionmaker

log = logging.getLogger(__name__)


@dataclass
class CrawlStats:
    job: str
    search_pages: int = 0
    listing_urls: int = 0
    saved: int = 0
    unchanged: int = 0
    removed: int = 0
    errors: int = 0
    blocked: str | None = None
    events: list[Event] = field(default_factory=list)

    def summary(self) -> str:
        s = (f"[{self.job}] страниц выдачи: {self.search_pages}, ссылок: {self.listing_urls}, "
             f"сохранено: {self.saved}, без изменений: {self.unchanged}, снято: {self.removed}, "
             f"ошибок: {self.errors}, событий: {len(self.events)}")
        return s + (f"\n  ОСТАНОВЛЕНО: {self.blocked}" if self.blocked else "")


def make_fetcher(settings: Settings):
    backend = settings.fetch_backend
    if backend == "httpx":
        return Fetcher(settings)
    if backend == "firecrawl":
        from arte.fetch.firecrawl import FirecrawlFetcher
        return FirecrawlFetcher(settings)
    if backend == "scrapling":
        from arte.fetch.scrapling_backend import ScraplingFetcher
        return ScraplingFetcher(settings)
    if backend == "browser":
        from arte.fetch.browser import BrowserFetcher
        return BrowserFetcher(settings)
    raise ValueError(f"Неизвестный fetch_backend: {backend}")


def make_llm(settings: Settings):
    if not settings.llm_enabled:
        return None
    if settings.llm_backend == "scrapegraph":
        from arte.extract.scrapegraph import ScrapeGraphExtractor
        return ScrapeGraphExtractor(settings)
    from arte.extract.llm import ClaudeExtractor
    return ClaudeExtractor(settings)


async def extract_url(url: str, fetcher, llm=None, source_name: str | None = None,
                      selectors: dict | None = None, settings: Settings | None = None):
    """Скачать и разобрать одно объявление (используется и обходом, и командой `arte check`)."""
    settings = settings or get_settings()
    src = get_source(source_name) if source_name else source_for_url(url)
    res = await fetcher.get(url, extract=True)
    if res.status in (404, 410):
        return src, res, None
    if res.not_modified:
        return src, res, None
    tree = HTMLParser(res.text)
    overrides = src.site_fields(tree, res.url)
    if selectors:
        overrides = {**AdaptiveRules(src.name, selectors, settings.adaptive_storage).apply(res.text, url),
                     **overrides}
    listing = await extract_listing(src.name, src.external_id(url), src.canonical_url(url), res.text,
                                    prefilled=res.extracted, llm=llm, overrides=overrides)
    return src, res, listing


async def collect_listing_urls(job: Job, fetcher, stats: CrawlStats) -> list[str]:
    src = get_source(job.source, job.listing_pattern)
    urls: dict[str, None] = {}
    for search_url in job.search_urls:
        for page in range(1, job.max_pages + 1):
            res = await fetcher.get(src.page_url(search_url, page), extract=False, conditional=False)
            stats.search_pages += 1
            new = [u for u in src.listing_urls(res) if u not in urls]
            if not new:
                break
            urls.update(dict.fromkeys(new))
            if len(urls) >= job.max_listings:
                break
    return list(urls)[: job.max_listings]


async def run_job(job: Job, settings: Settings | None = None, fetcher=None, llm=None) -> CrawlStats:
    settings = settings or get_settings()
    stats = CrawlStats(job=job.name)
    own_fetcher = fetcher is None
    fetcher = fetcher or make_fetcher(settings)
    llm = llm if llm is not None else make_llm(settings)
    sm = sessionmaker()
    try:
        if own_fetcher and hasattr(fetcher, "__aenter__"):
            await fetcher.__aenter__()
        try:
            urls = await collect_listing_urls(job, fetcher, stats)
        except BlockedError as e:
            stats.blocked = str(e)
            return stats
        stats.listing_urls = len(urls)

        sem = asyncio.Semaphore(settings.concurrency)
        stop = asyncio.Event()

        async def process(url: str) -> None:
            if stop.is_set():
                return
            async with sem:
                try:
                    src, res, listing = await extract_url(url, fetcher, llm, job.source,
                                                          job.selectors, settings)
                except BlockedError as e:
                    stats.blocked = str(e)
                    stop.set()
                    return
                except DisallowedError:
                    log.info("robots.txt запрещает %s", url)
                    return
                except Exception:
                    log.exception("ошибка на %s", url)
                    stats.errors += 1
                    return
                async with sm() as session:
                    ext_id = src.external_id(url)
                    if listing is None and res.not_modified:
                        await repo.touch(session, src.name, ext_id)
                        stats.unchanged += 1
                    elif listing is None:
                        if ev := await repo.mark_removed(session, src.name, ext_id):
                            stats.events.append(ev)
                            stats.removed += 1
                    else:
                        stats.events.extend(await repo.upsert_listing(session, listing, settings))
                        stats.saved += 1
                    await session.commit()

        await asyncio.gather(*(process(u) for u in urls))
        if job.notify and stats.events:
            from arte.notify.telegram import notify_events
            await notify_events(stats.events, job, settings)
        return stats
    finally:
        if own_fetcher and hasattr(fetcher, "__aexit__"):
            await fetcher.__aexit__(None, None, None)
