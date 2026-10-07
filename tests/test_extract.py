from selectolax.lexbor import LexborHTMLParser

from arte.extract.pipeline import extract_listing
from arte.extract.schema import ListingExtraction
from arte.models import DealType, PropertyType
from arte.sources import get_source
from tests.conftest import fixture


async def test_jsonld_page():
    item = await extract_listing("generic", "1", "https://agency.example/sale/1", fixture("jsonld_agency.html"))
    assert item.price == 12_500_000
    assert item.area_total == 54.2
    assert (item.rooms, item.floor) == (2, 5)
    assert item.address.lat == 55.751
    assert "Ленина" in item.address.full
    assert len(item.photos) == 2
    assert "jsonld" in item.extraction_methods


async def test_embedded_state_picks_main_offer_not_similar():
    item = await extract_listing("cian", "312345678", "https://www.cian.ru/sale/flat/312345678/",
                              fixture("cian_like.html"))
    assert item.price == 21_900_000  # а не цены из similarOffers
    assert item.area_total == 78.4
    assert (item.rooms, item.floor, item.floors_total) == (3, 7, 16)
    assert item.year_built == 2012
    assert item.address.full.startswith("Москва, Пресненская")
    assert (item.address.lat, item.address.lon) == (55.7489, 37.5395)
    assert item.deal_type == DealType.SALE
    assert item.property_type == PropertyType.FLAT
    assert "embedded_json" in item.extraction_methods


async def test_urlencoded_state_and_site_rules():
    src = get_source("avito")
    url = "https://www.avito.ru/sankt-peterburg/kvartiry/2-k._kvartira_54m_512et._4123456789"
    html = fixture("avito_like.html")
    item = await extract_listing("avito", src.external_id(url), url, html,
                              overrides=src.site_fields(LexborHTMLParser(html), url))
    assert item.external_id == "4123456789"
    assert item.price == 11_200_000
    assert (item.rooms, item.area_total, item.floor, item.floors_total) == (2, 54, 5, 12)
    assert item.address.lat == 59.931
    assert item.extraction_methods[0] == "site_rules"


class FakeLLM:
    def __init__(self):
        self.calls = 0

    async def extract(self, url, page_text, html=None):
        self.calls += 1
        assert "Уютная студия" in page_text and "trackPageView" not in page_text
        return ListingExtraction(price=6_100_000, area_total=24.5, rooms=0, floor=3, floors_total=9,
                                 address="Казань, ул. Баумана, 5", deal_type="sale", property_type="flat")


async def test_llm_fallback_only_when_needed():
    llm = FakeLLM()
    item = await extract_listing("generic", "77", "https://x.example/77", fixture("bare.html"), llm=llm)
    assert llm.calls == 1
    assert (item.price, item.rooms, item.address.full) == (6_100_000, 0, "Казань, ул. Баумана, 5")
    assert "llm" in item.extraction_methods

    await extract_listing("generic", "1", "https://agency.example/sale/1",
                          fixture("jsonld_agency.html"), llm=llm)
    assert llm.calls == 1  # данных хватило — LLM не звали


async def test_firecrawl_prefilled_json_is_used():
    item = await extract_listing("generic", "77", "https://x.example/77", fixture("bare.html"),
                              prefilled={"price": 5_000_000, "area_total": 30, "rooms": 1, "floor": 2,
                                         "address": "Тверь, ул. Советская, 1", "title": "1-к"})
    assert item.price == 5_000_000
    assert "firecrawl_json" in item.extraction_methods
