"""Адаптер площадки: распознаёт URL объявлений, достаёт ID, листает выдачу.

Разбор самих страниц у всех адаптеров общий (arte.extract.pipeline) — адаптер может
лишь добавить точные правила для своей площадки через site_fields().
"""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from arte.fetch.http import FetchResult


class Source:
    name: str = "generic"
    domains: tuple[str, ...] = ()
    listing_re: re.Pattern | None = None
    page_param: str = "p"
    page_step: int = 1
    first_page: int = 1

    def __init__(self, listing_pattern: str | None = None):
        if listing_pattern:
            self.listing_re = re.compile(listing_pattern)

    def matches(self, url: str) -> bool:
        host = urlsplit(url).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in self.domains)

    def is_listing_url(self, url: str) -> bool:
        return bool(self.listing_re and self.listing_re.search(url))

    def external_id(self, url: str) -> str:
        if self.listing_re and (m := self.listing_re.search(url)) and m.groupdict().get("id"):
            return m.group("id")
        return hashlib.sha1(self.canonical_url(url).encode()).hexdigest()[:16]

    def canonical_url(self, url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, "", ""))

    def page_url(self, search_url: str, page: int) -> str:
        """URL n-й страницы выдачи (page начинается с 1)."""
        if page == 1:
            return search_url
        parts = urlsplit(search_url)
        query = dict(parse_qsl(parts.query))
        query[self.page_param] = str(self.first_page + (page - 1) * self.page_step)
        return urlunsplit(parts._replace(query=urlencode(query)))

    def listing_urls(self, page: FetchResult) -> list[str]:
        links = page.links
        if not links and page.text:
            tree = HTMLParser(page.text)
            links = [a.attributes.get("href") or "" for a in tree.css("a[href]")]
        seen: dict[str, None] = {}
        for href in links:
            absolute = urljoin(page.url, href)
            if self.is_listing_url(absolute):
                seen.setdefault(self.canonical_url(absolute), None)
        return list(seen)

    def site_fields(self, tree: HTMLParser, url: str) -> dict:
        """Точные правила площадки поверх общего разбора. По умолчанию — нет."""
        return {}
