"""Загрузка страниц, которые отрисовываются JavaScript'ом, через Playwright.

Используется только когда в HTML нет данных. Браузер не маскируется под человека:
тот же честный User-Agent и тот же темп, что у HTTP-загрузчика.
"""

from arte.config import Settings, get_settings
from arte.fetch.http import FetchResult


class BrowserFetcher:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._pw = None
        self._browser = None

    async def __aenter__(self) -> "BrowserFetcher":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def get(self, url: str, wait_selector: str | None = None, **_) -> FetchResult:
        context = await self._browser.new_context(user_agent=self.settings.user_agent, locale="ru-RU")
        page = await context.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded",
                                   timeout=self.settings.request_timeout * 1000)
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=10_000)
            html = await page.content()
            return FetchResult(url=page.url, status=resp.status if resp else 0, text=html)
        finally:
            await context.close()
