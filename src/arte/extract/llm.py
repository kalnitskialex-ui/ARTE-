"""Запасное извлечение через Claude, когда машиночитаемых данных на странице нет.

На вход идёт не сырой HTML, а очищенный видимый текст, чтобы экономить токены.
Ответ строго валидируется по схеме ListingExtraction (structured outputs).
"""

import logging

from arte.config import Settings, get_settings
from arte.extract.schema import EXTRACTION_PROMPT, ListingExtraction

log = logging.getLogger(__name__)


class ClaudeExtractor:
    def __init__(self, settings: Settings | None = None, client=None):
        self.settings = settings or get_settings()
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic()
        self._client = client

    async def extract(self, url: str, page_text: str, html: str | None = None) -> ListingExtraction | None:
        import anthropic

        text = page_text[: self.settings.llm_max_html_chars]
        try:
            response = await self._client.beta.messages.parse(
                model=self.settings.llm_model,
                max_tokens=4096,
                # Если основная модель откажет по политике, сервер повторит запрос на запасной.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": self.settings.llm_effort},
                system=EXTRACTION_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"URL: {url}\n\nТекст страницы:\n<page>\n{text}\n</page>",
                }],
                output_format=ListingExtraction,
            )
        except anthropic.RateLimitError:
            log.warning("Claude rate limit, пропускаю LLM-извлечение для %s", url)
            return None
        except anthropic.APIStatusError as e:
            log.warning("Claude API error %s для %s", e.status_code, url)
            return None
        except anthropic.APIConnectionError:
            log.warning("Нет связи с Claude API для %s", url)
            return None

        if response.stop_reason == "refusal":
            log.warning("Claude отказал в извлечении для %s", url)
            return None
        return response.parsed_output
