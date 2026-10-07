"""Задания на сбор: что обходить, как часто и о чём уведомлять. Описываются в jobs.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class NotifyFilter(BaseModel):
    kinds: list[str] = Field(default_factory=lambda: ["new", "price_down"])
    price_max: float | None = None
    price_min: float | None = None
    rooms: list[int] | None = None
    area_min: float | None = None

    def accepts(self, kind: str, price: float | None, rooms: int | None, area: float | None) -> bool:
        if kind not in self.kinds:
            return False
        if self.price_max is not None and (price is None or price > self.price_max):
            return False
        if self.price_min is not None and (price is None or price < self.price_min):
            return False
        if self.rooms is not None and rooms not in self.rooms:
            return False
        return not (self.area_min is not None and (area is None or area < self.area_min))


class Job(BaseModel):
    name: str
    source: str
    search_urls: list[str]
    max_pages: int = 3
    max_listings: int = 200
    interval_minutes: int = 120
    # Для source=generic: регулярное выражение URL объявления, группа (?P<id>...) — ID.
    listing_pattern: str | None = None
    # Самовосстанавливающиеся селекторы Scrapling: поле -> CSS.
    selectors: dict[str, str] = Field(default_factory=dict)
    notify: NotifyFilter | None = None


def load_jobs(path: str | Path) -> list[Job]:
    path = Path(path)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Job.model_validate(j) for j in data.get("jobs", [])]
