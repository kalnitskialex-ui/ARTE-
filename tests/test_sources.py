from arte.fetch.http import FetchResult
from arte.sources import get_source, source_for_url
from tests.conftest import fixture


def test_cian_search_links_are_deduplicated_and_canonical():
    src = get_source("cian")
    page = FetchResult(url="https://www.cian.ru/cat.php?deal_type=sale", status=200,
                       text=fixture("search_cian.html"))
    assert src.listing_urls(page) == [
        "https://www.cian.ru/sale/flat/312345678/",
        "https://www.cian.ru/sale/flat/398765432/",
        "https://www.cian.ru/rent/flat/301111111/",
    ]
    assert src.external_id("https://www.cian.ru/sale/flat/312345678/?x=1") == "312345678"


def test_links_from_backend_take_priority():
    src = get_source("cian")
    page = FetchResult(url="https://www.cian.ru/", status=200, text="",
                       links=["https://spb.cian.ru/sale/flat/123456789/"])
    assert src.listing_urls(page) == ["https://spb.cian.ru/sale/flat/123456789/"]


def test_ids_and_paging():
    assert get_source("avito").external_id(
        "https://www.avito.ru/moskva/kvartiry/studiya_25m_39et._3011223344?context=x") == "3011223344"
    assert get_source("domclick").external_id("https://domclick.ru/card/sale__flat__1987654321") == "1987654321"
    assert get_source("yandex").external_id("https://realty.yandex.ru/offer/4471234567890123456/") \
        == "4471234567890123456"
    assert get_source("avito").page_url("https://www.avito.ru/moskva/kvartiry?s=104", 3) \
        == "https://www.avito.ru/moskva/kvartiry?s=104&p=3"
    assert "offset=40" in get_source("domclick").page_url("https://domclick.ru/search?a=1", 3)


def test_source_for_url_and_generic_pattern():
    assert source_for_url("https://spb.cian.ru/sale/flat/1/").name == "cian"
    generic = get_source("generic", r"agency\.example/object/(?P<id>\d+)")
    assert generic.is_listing_url("https://agency.example/object/55")
    assert generic.external_id("https://agency.example/object/55") == "55"
    assert len(get_source("generic").external_id("https://x.example/a")) == 16
