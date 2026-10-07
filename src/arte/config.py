"""Настройки приложения. Читаются из переменных окружения с префиксом ARTE_ и из .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARTE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./arte.db"

    # Сетевая вежливость: представляемся честно и не перегружаем площадки.
    user_agent: str = "ARTE-bot/0.1 (+https://github.com/kalnitskialex-ui/ARTE-)"
    respect_robots_txt: bool = True
    # Минимальная пауза между запросами к одному домену, секунды.
    per_host_delay: float = 3.0
    request_timeout: float = 30.0
    max_retries: int = 3
    concurrency: int = 4
    # Прокси (одна точка выхода, например корпоративный шлюз). Ротация прокси
    # для обхода блокировок намеренно не поддерживается.
    proxy: str | None = None

    # Бэкенд загрузки: httpx (быстро, без JS), browser (Playwright), firecrawl (облачный рендер).
    fetch_backend: str = "httpx"  # httpx | browser | firecrawl | scrapling
    scrapling_mode: str = "http"  # http | dynamic
    firecrawl_api_key: str | None = None
    # Адрес self-hosted Firecrawl; пусто — облачный api.firecrawl.dev.
    firecrawl_api_url: str | None = None
    # Просить Firecrawl извлечь JSON по нашей схеме (дороже по кредитам, но работает на любой вёрстке).
    firecrawl_json_extract: bool = True
    # Разрешить отдавать кэш Firecrawl не старше N мс (экономит кредиты при повторных обходах).
    firecrawl_max_age_ms: int = 3_600_000

    # LLM-извлечение как запасной путь, когда структурированных данных на странице нет.
    llm_enabled: bool = False
    # Чем извлекать, если структурированных данных нет: claude | scrapegraph
    llm_backend: str = "claude"
    # Модель для ScrapeGraphAI в формате провайдер/модель; пусто — Claude из llm_model.
    scrapegraph_model: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "low"
    llm_max_html_chars: int = 60_000

    # Сбор персональных данных продавцов (имя, телефон) выключен: 152-ФЗ.
    collect_contacts: bool = False

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    jobs_file: str = "jobs.yaml"
    adaptive_storage: str = "adaptive.db"
    dedup_area_tolerance: float = Field(0.02, description="Допуск по площади при склейке дублей")
    dedup_geo_radius_m: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
