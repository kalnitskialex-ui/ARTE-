"""Самовосстанавливающиеся CSS-селекторы на базе Scrapling.

Пользователь задаёт в jobs.yaml селекторы для полей конкретной площадки. При первом
успешном срабатывании Scrapling запоминает «отпечаток» элемента (тег, атрибуты, текст,
соседи, путь) в SQLite. Если площадка поменяла вёрстку и селектор перестал находить
элемент, Scrapling ищет на новой странице самый похожий элемент по отпечатку.
"""

import logging
from pathlib import Path

from arte.extract import normalize as n

log = logging.getLogger(__name__)

_PARSERS = {
    "price": n.parse_price,
    "area_total": n.parse_area, "area_living": n.parse_area, "area_kitchen": n.parse_area,
    "rooms": n.parse_rooms,
    "floor": lambda t: n.parse_floor(t)[0] or n.to_int(t),
    "floors_total": lambda t: n.parse_floor(t)[1] or n.to_int(t),
    "year_built": n.to_int,
}


class AdaptiveRules:
    def __init__(self, source: str, selectors: dict[str, str], storage_file: str = "adaptive.db"):
        self.source = source
        self.selectors = selectors
        self.storage_file = str(Path(storage_file).resolve())

    def apply(self, html: str, url: str) -> dict:
        from scrapling.parser import Selector

        page = Selector(html, url=url, adaptive=True,
                        storage_args={"storage_file": self.storage_file, "url": url})
        out: dict = {}
        for field, css in self.selectors.items():
            ident = f"{self.source}:{field}"
            found = page.css(css, identifier=ident, auto_save=True)
            if not found:
                found = page.css(css, identifier=ident, adaptive=True)
                if found:
                    log.info("%s: селектор %r для %s восстановлен адаптивно", self.source, css, field)
            if not found:
                continue
            text = str(found[0].get_all_text(strip=True) or found[0].text).strip()
            parser = _PARSERS.get(field)
            value = parser(text) if parser else text
            if value not in (None, ""):
                out[field] = value
        return out
