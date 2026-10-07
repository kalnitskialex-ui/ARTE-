"""Бэкенды сторонних сервисов проверяются на подставных клиентах: сеть до них из CI закрыта."""

from types import SimpleNamespace

import pytest

from arte.extract.adaptive import AdaptiveRules
from arte.extract.schema import ListingExtraction
from arte.fetch.firecrawl import FirecrawlFetcher
from tests.conftest import fixture


class FakeFirecrawl:
    def __init__(self):
        self.calls = []

    async def scrape(self, url, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            raw_html=fixture("bare.html"), html=None,
            links=["https://www.cian.ru/sale/flat/312345678/"],
            json={"price": 7_000_000, "area_total": 40, "rooms": 1, "floor": 4, "address": "Москва"},
            metadata=SimpleNamespace(status_code=200, source_url=url, url=url),
        )

    async def map(self, url, **kwargs):
        return SimpleNamespace(links=[SimpleNamespace(url=url + "/a")])


async def test_firecrawl_fetcher(settings):
    fc = FakeFirecrawl()
    async with FirecrawlFetcher(settings, client=fc) as f:
        res = await f.get("https://www.cian.ru/sale/flat/312345678/")
        listing_page = fc.calls[-1]
        await f.get("https://www.cian.ru/cat.php", extract=False)
        assert await f.map_site("https://agency.example") == ["https://agency.example/a"]
    assert res.extracted["price"] == 7_000_000
    assert res.links == ["https://www.cian.ru/sale/flat/312345678/"]
    assert any(isinstance(x, dict) and x["type"] == "json" for x in listing_page["formats"])
    assert all(not isinstance(x, dict) for x in fc.calls[-1]["formats"])  # выдача — без LLM
    assert listing_page["location"]["country"] == "RU"


def test_firecrawl_requires_key(settings):
    with pytest.raises(RuntimeError):
        FirecrawlFetcher(settings)


def test_scrapling_adaptive_selectors_survive_redesign(settings):
    rules = AdaptiveRules("agency", {"price": "#price", "area_total": ".area"}, settings.adaptive_storage)
    v1 = """<html><body><div class="card"><span id="price">12 500 000 ₽</span>
            <span class="area">54,2 м²</span></div></body></html>"""
    assert rules.apply(v1, "https://agency.example/1") == {"price": 12_500_000, "area_total": 54.2}
    # Сайт сменил вёрстку: id и классы другие, структура и текст похожи.
    v2 = """<html><body><div class="card"><span id="cost-value">9 900 000 ₽</span>
            <span class="square">54,2 м²</span></div></body></html>"""
    assert rules.apply(v2, "https://agency.example/1")["price"] == 9_900_000


async def test_scrapegraph_extractor(settings, monkeypatch):
    from arte.extract.scrapegraph import ScrapeGraphExtractor

    ex = ScrapeGraphExtractor(settings)
    assert ex._config()["llm"]["model"] == "anthropic/claude-opus-5-5"
    monkeypatch.setattr(ex, "_run", lambda html: {"content": {"price": 5.5e6, "rooms": 2}})
    out = await ex.extract("https://x.example", "text", html="<html></html>")
    assert out == ListingExtraction(price=5.5e6, rooms=2)

    def boom(html):
        raise RuntimeError("provider down")
    monkeypatch.setattr(ex, "_run", boom)
    assert await ex.extract("https://x.example", "text") is None


class FakeAnthropic:
    def __init__(self, stop_reason="end_turn"):
        self.kwargs = None
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(stop_reason=self.stop_reason,
                               parsed_output=ListingExtraction(price=1.0, rooms=1))


async def test_claude_extractor_request_shape(settings):
    from arte.extract.llm import ClaudeExtractor

    client = FakeAnthropic()
    out = await ClaudeExtractor(settings, client=client).extract("https://x.example", "текст" * 10)
    assert out.price == 1.0
    k = client.kwargs
    assert k["model"] == "claude-opus-5-5"
    assert k["output_format"] is ListingExtraction
    assert k["fallbacks"] == "default" and k["betas"] == ["server-side-fallback-2026-07-01"]
    assert k["output_config"] == {"effort": "low"}

    refused = FakeAnthropic(stop_reason="refusal")
    assert await ClaudeExtractor(settings, client=refused).extract("https://x.example", "t") is None
