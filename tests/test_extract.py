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


async def test_domclick_structure_from_live_page():
    """Регрессия: Event в JSON-LD, undefined в стейте, синонимы area/floors (живой Домклик, 2026-10)."""
    url = "https://yakutsk.domclick.ru/card/sale__flat__1933906019"
    item = await extract_listing("domclick", "1933906019", url, fixture("domclick_like.html"))
    assert (item.price, item.currency, item.area_total) == (9_800_000, "RUB", 66)
    assert (item.rooms, item.floor, item.floors_total, item.year_built) == (2, 3, 16, 2016)
    assert item.address.full == "Республика Саха (Якутия), Якутск, улица Пирогова, 9"
    assert (item.address.lat, item.address.lon) == (62.044579, 129.726243)
    assert "ипотек" not in item.title  # промо-название Event не попало в заголовок
    assert item.title.startswith("Продаётся 2-комнатная")
    assert item.completeness() == 1


async def test_yandex_structure_from_live_page():
    """Регрессия: адрес только в мета-описании, аренда, валюта RUR (живой Яндекс, 2026-10)."""
    url = "https://realty.yandex.ru/offer/6675507316243631302/"
    html = fixture("yandex_like.html")
    src = get_source("yandex")
    item = await extract_listing("yandex", "6675507316243631302", url, html,
                                 overrides=src.site_fields(LexborHTMLParser(html), url))
    assert (item.area_living, item.area_kitchen) == (18, 9.5)  # блок характеристик карточки
    assert (item.price, item.currency, item.deal_type) == (35_000, "RUB", DealType.RENT)
    assert (item.area_total, item.rooms, item.floor, item.floors_total) == (37.9, 1, 3, 5)
    assert item.address.full == "Санкт-Петербург, Челябинская улица, 37"


def test_sanitize_rejects_llm_garbage():
    """Ошибки, которые Firecrawl JSON допустил на живом Домклике (2026-10)."""
    from arte.extract.pipeline import sanitize

    out = sanitize({"lat": 0, "lon": 0, "area_total": 66, "area_living": 66, "area_kitchen": 0,
                    "floor": 3, "floors_total": 16, "year_built": 2016, "price": 9_800_000})
    assert (out["lat"], out["lon"], out["area_living"], out["area_kitchen"]) == (None, None, None, None)
    assert (out["area_total"], out["floors_total"], out["year_built"]) == (66, 16, 2016)
    assert sanitize({"floor": 9, "floors_total": 5})["floors_total"] is None
    assert sanitize({"year_built": 20160})["year_built"] is None


async def test_llm_values_do_not_override_page_data():
    """LLM только дополняет пустые поля: точные данные страницы приоритетнее."""
    url = "https://yakutsk.domclick.ru/card/sale__flat__1933906019"
    wrong = {"price": 1, "area_total": 66, "area_living": 66, "lat": 0, "lon": 0, "rooms": 5}
    item = await extract_listing("domclick", "1", url, fixture("domclick_like.html"), prefilled=wrong)
    assert (item.price, item.rooms, item.area_living) == (9_800_000, 2, None)
    assert item.address.lat == 62.044579


async def test_label_value_pairs_any_layout():
    """Сайт агентства без JSON-LD и стейта: данные только в разметке «подпись → значение»."""
    item = await extract_listing("generic", "1", "https://agency.example/object/1", fixture("labels_agency.html"))
    assert (item.area_total, item.area_living, item.area_kitchen) == (81.4, 48, 12.3)
    assert (item.floor, item.floors_total, item.year_built) == (4, 9, 1987)
    assert item.address.full == "Санкт-Петербург, Садовая ул., 40"
    assert item.rooms == 3
    assert "labels" in item.extraction_methods


def test_label_pairs_ignore_value_first_layout():
    """Вёрстка «значение → подпись» (Яндекс) не должна давать сдвинутых пар."""
    from selectolax.lexbor import LexborHTMLParser as P

    from arte.extract.structured import from_label_pairs

    html = ("<div><span>62 м²</span><span>общая</span><span>25 м²</span><span>жилая</span>"
            "<span>19 м²</span><span>кухня</span><span>21 этаж</span><span>из 29</span></div>")
    assert from_label_pairs(P(html)) == {}
