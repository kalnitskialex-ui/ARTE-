"""Альтернативный LLM-извлекатель на ScrapeGraphAI (SmartScraperGraph).

Страницу скачивает наш загрузчик (с robots.txt и темпом), а ScrapeGraphAI получает
готовый HTML: так его собственные загрузчики и прокси-механизмы не используются.
Модель задаётся строкой провайдер/модель, по умолчанию — Claude через langchain-anthropic.
"""

import asyncio
import logging
import os

from arte.config import Settings, get_settings
from arte.extract.schema import EXTRACTION_PROMPT, ListingExtraction

log = logging.getLogger(__name__)


class ScrapeGraphExtractor:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _config(self) -> dict:
        model = self.settings.scrapegraph_model or f"anthropic/{self.settings.llm_model}"
        llm: dict = {"model": model, "model_tokens": 200_000, "max_tokens": 4096}
        if model.startswith("anthropic/") and os.environ.get("ANTHROPIC_API_KEY"):
            llm["api_key"] = os.environ["ANTHROPIC_API_KEY"]
        return {"llm": llm, "verbose": False, "headless": True}

    def _run(self, html: str) -> dict | None:
        from scrapegraphai.graphs import SmartScraperGraph

        graph = SmartScraperGraph(prompt=EXTRACTION_PROMPT, source=html, config=self._config(),
                                  schema=ListingExtraction)
        result = graph.run()
        return result if isinstance(result, dict) else None

    async def extract(self, url: str, page_text: str, html: str | None = None) -> ListingExtraction | None:
        try:
            raw = await asyncio.to_thread(self._run, html or page_text)
        except Exception as e:  # ScrapeGraphAI оборачивает ошибки провайдеров по-разному
            log.warning("ScrapeGraphAI не смог извлечь %s: %s", url, e)
            return None
        if not raw:
            return None
        # Ответ может прийти как {"content": {...}} в зависимости от версии графа.
        data = raw.get("content", raw) if isinstance(raw.get("content"), dict) else raw
        try:
            return ListingExtraction.model_validate(data)
        except ValueError:
            log.warning("ScrapeGraphAI вернул данные не по схеме для %s", url)
            return None
