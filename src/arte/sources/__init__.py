from arte.sources.base import Source
from arte.sources.sites import Avito, Cian, Domclick, YandexRealty

SOURCES: dict[str, type[Source]] = {s.name: s for s in (Cian, Avito, Domclick, YandexRealty, Source)}


def get_source(name: str, listing_pattern: str | None = None) -> Source:
    try:
        return SOURCES[name](listing_pattern)
    except KeyError:
        raise ValueError(f"Неизвестная площадка {name!r}. Есть: {', '.join(SOURCES)}") from None


def source_for_url(url: str) -> Source:
    for cls in SOURCES.values():
        src = cls()
        if src.matches(url):
            return src
    return Source()


__all__ = ["SOURCES", "Source", "get_source", "source_for_url"]
