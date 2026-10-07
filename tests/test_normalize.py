import pytest

from arte.extract import normalize as n
from arte.models import DealType, PropertyType


@pytest.mark.parametrize("text,expected", [
    ("12 500 000 ₽", 12_500_000), ("4,5 млн ₽", 4_500_000), ("35 тыс. ₽/мес.", 35_000),
    ("21 900 000", 21_900_000), (9_000_000, 9_000_000), ("договорная", None),
])
def test_parse_price(text, expected):
    assert n.parse_price(text) == expected


@pytest.mark.parametrize("text,expected", [("45,6 м²", 45.6), ("78.4", 78.4), ("120 кв. м", 120)])
def test_parse_area(text, expected):
    assert n.parse_area(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("2-комн. квартира", 2), ("Студия, 25 м²", 0), ("3-к. квартира", 3), ("двухкомнатная", 2),
    ("Двух комнатная", 2), ("квартира", None),
])
def test_parse_rooms(text, expected):
    assert n.parse_rooms(text) == expected


def test_parse_floor():
    assert n.parse_floor("2-к. квартира, 54 м², 5/12 эт.") == (5, 12)
    assert n.parse_floor("7 этаж из 16") == (7, 16)
    assert n.parse_floor("3 этаж") == (3, None)
    assert n.parse_floor("4 из 9") == (4, 9)
    assert n.parse_floor("12/25") == (12, 25)


def test_infer_types():
    assert n.infer_deal_type("https://www.cian.ru/rent/flat/1/") == DealType.RENT
    assert n.infer_deal_type("Продается квартира") == DealType.SALE
    assert n.infer_property_type("https://www.avito.ru/moskva/kvartiry/x_1") == PropertyType.FLAT


def test_address_key_equivalence():
    a = n.address_key("г. Москва, ул. Ленина, д. 10")
    b = n.address_key("Москва, улица Ленина, дом 10")
    assert a == b == "москва ленина 10"
    assert n.address_key("Невский проспект, 100") == n.address_key("Невский пр-т 100")
