"""Сборка объявления из слоёв извлечения: от самого надёжного к самому дорогому."""

import logging
from typing import Any

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from arte.extract import normalize as n
from arte.extract import structured as s
from arte.extract.schema import ListingExtraction
from arte.models import Address, DealType, Listing, PropertyType

log = logging.getLogger(__name__)

# Если после детерминированных слоёв заполнено меньше этой доли ключевых полей — зовём LLM.
LLM_THRESHOLD = 0.67


def _merge(base: dict[str, Any], extra: dict[str, Any], method: str, methods: list[str]) -> None:
    added = False
    for k, v in extra.items():
        if v not in (None, "", []) and base.get(k) in (None, "", []):
            base[k] = v
            added = True
    if added:
        methods.append(method)


def _currency(value) -> str:
    code = str(value or "RUB").upper()
    # RUR — устаревший код рубля, площадки до сих пор его отдают.
    return {"RUR": "RUB", "РУБ": "RUB", "₽": "RUB"}.get(code, code)


def _enum(enum_cls, value):
    try:
        return enum_cls(value) if value else None
    except ValueError:
        return None


def build_listing(source: str, external_id: str, url: str, data: dict[str, Any],
                  methods: list[str]) -> Listing:
    title = data.get("title")
    desc = data.get("description") or data.get("meta_description")
    return Listing(
        source=source,
        external_id=external_id,
        url=url,
        title=title,
        deal_type=_enum(DealType, data.get("deal_type")) or n.infer_deal_type(url, title),
        property_type=_enum(PropertyType, data.get("property_type")) or n.infer_property_type(url, title),
        price=data.get("price"),
        currency=_currency(data.get("currency")),
        area_total=data.get("area_total"),
        area_living=data.get("area_living"),
        area_kitchen=data.get("area_kitchen"),
        rooms=data.get("rooms"),
        floor=data.get("floor"),
        floors_total=data.get("floors_total"),
        year_built=data.get("year_built"),
        address=Address(full=data.get("address"), metro=data.get("metro"),
                        lat=data.get("lat"), lon=data.get("lon")),
        description=desc,
        photos=[p for p in data.get("photos", []) if p][:30],
        seller_type=data.get("seller_type"),
        extraction_methods=methods,
    )


async def extract_listing(source: str, external_id: str, url: str, html: str, *,
                          prefilled: dict | None = None, llm=None,
                          overrides: dict[str, Any] | None = None) -> Listing:
    """overrides — поля, которые адаптер площадки достал своими точными правилами."""
    tree = HTMLParser(html)
    data: dict[str, Any] = {}
    methods: list[str] = []

    _merge(data, overrides or {}, "site_rules", methods)
    _merge(data, s.from_jsonld(tree), "jsonld", methods)
    _merge(data, s.from_embedded_json(tree), "embedded_json", methods)
    _merge(data, s.from_meta(tree), "meta", methods)
    if prefilled:
        _merge(data, ListingExtraction.model_validate(prefilled).model_dump(), "firecrawl_json", methods)
    title_text = " ".join(filter(None, [data.get("title"), data.get("meta_description")]))
    _merge(data, s.from_text(title_text), "text", methods)

    listing = build_listing(source, external_id, url, data, methods)
    if llm is not None and listing.completeness() < LLM_THRESHOLD:
        extracted = await llm.extract(url, s.visible_text(tree), html=html)
        if extracted:
            _merge(data, extracted.model_dump(), "llm", methods)
            listing = build_listing(source, external_id, url, data, methods)
    return listing
