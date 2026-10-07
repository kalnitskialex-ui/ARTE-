"""Адаптеры крупных площадок РФ.

Шаблоны URL объявлений стабильны годами, поэтому адаптеры опираются на них, а
содержимое страниц разбирается общим многослойным извлекателем. Важно: эти сайты
закрыты от этой среды разработки сетевой политикой, поэтому разбор живых страниц
нужно проверить при первом запуске (см. README, раздел «Проверка адаптеров»).
"""

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from arte.extract import normalize as n
from arte.sources.base import Source


class Cian(Source):
    name = "cian"
    domains = ("cian.ru",)
    listing_re = re.compile(r"cian\.ru/(?:sale|rent)/(?:flat|suburban|commercial)/(?P<id>\d{5,})/?")
    page_param = "p"


class Avito(Source):
    name = "avito"
    domains = ("avito.ru",)
    listing_re = re.compile(
        r"avito\.ru/[\w-]+/(?:kvartiry|komnaty|doma_dachi_kottedzhi|zemelnye_uchastki|"
        r"garazhi_i_mashinomesta|kommercheskaya_nedvizhimost)/[\w.%-]*_(?P<id>\d{7,})(?:$|[?#/])"
    )
    page_param = "p"

    def site_fields(self, tree: HTMLParser, url: str) -> dict:
        # Заголовок Avito компактно кодирует ключевые параметры: «2-к. квартира, 54 м², 5/12 эт.»
        h1 = tree.css_first("h1")
        if not h1:
            return {}
        title = h1.text(strip=True)
        out: dict = {"title": title}
        if (rooms := n.parse_rooms(title)) is not None:
            out["rooms"] = rooms
        floor, total = n.parse_floor(title)
        out["floor"], out["floors_total"] = floor, total
        if m := re.search(r"([\d,.]+)\s*м²", title):
            out["area_total"] = n.to_float(m.group(1))
        return {k: v for k, v in out.items() if v is not None}


class Domclick(Source):
    name = "domclick"
    domains = ("domclick.ru",)
    listing_re = re.compile(r"domclick\.ru/card/(?:sale|rent)__\w+__(?P<id>\d{5,})")
    page_param = "offset"
    page_step = 20
    first_page = 0


class YandexRealty(Source):
    name = "yandex"
    domains = ("realty.yandex.ru", "realty.ya.ru")
    listing_re = re.compile(r"realty\.ya(?:ndex)?\.ru/offer/(?P<id>\d{8,})/?")
    page_param = "page"
    first_page = 0

    _HIGHLIGHT_FIELDS = {"общая": "area_total", "жилая": "area_living", "кухня": "area_kitchen"}

    def site_fields(self, tree: HTMLParser, url: str) -> dict:
        # Блок характеристик карточки: «62 м² общая · 25 м² жилая · 19 м² кухня · 21 этаж из 29».
        # Этих значений нет ни в JSON-LD, ни во встроенном стейте.
        out: dict = {}
        for item in tree.css('[class*="OfferCardHighlight__container"]'):
            label = item.css_first('[class*="OfferCardHighlight__label"]')
            value = item.css_first('[class*="OfferCardHighlight__value"]')
            if not (label and value):
                continue
            key = label.text(strip=True).lower()
            if field := self._HIGHLIGHT_FIELDS.get(key):
                out[field] = n.parse_area(value.text(strip=True))
        return {k: v for k, v in out.items() if v}
