"""Разбор русскоязычных форматов: цены, площади, комнаты, этажи, адреса."""

import re

from arte.models import DealType, PropertyType

_SPACES = re.compile(r"[\s   ]+")
_NUM = r"(\d[\d\s   ]*(?:[.,]\d+)?)"


def to_float(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    s = _SPACES.sub("", str(value)).replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def to_int(value) -> int | None:
    f = to_float(value)
    return int(f) if f is not None else None


def parse_price(text) -> float | None:
    """'12 500 000 ₽' -> 12500000; '4,5 млн' -> 4500000; '35 тыс. ₽/мес.' -> 35000."""
    if text is None:
        return None
    if isinstance(text, int | float):
        return float(text)
    s = str(text).lower()
    m = re.search(_NUM + r"\s*(млрд|млн|тыс|т\.р|k)?", s)
    if not m:
        return None
    value = to_float(m.group(1))
    if value is None:
        return None
    mult = {"млрд": 1e9, "млн": 1e6, "тыс": 1e3, "т.р": 1e3, "k": 1e3}.get(m.group(2) or "", 1)
    return value * mult


def parse_area(text) -> float | None:
    """'45,6 м²' -> 45.6"""
    if text is None:
        return None
    if isinstance(text, int | float):
        return float(text)
    m = re.search(_NUM + r"\s*(?:м²|м2|кв\.?\s*м|m²|sq)", str(text).lower())
    return to_float(m.group(1)) if m else to_float(text)


def parse_rooms(text) -> int | None:
    """'2-комн. квартира' -> 2, 'Студия' -> 0, '3-к. квартира' -> 3."""
    if text is None:
        return None
    s = str(text).lower()
    if "студи" in s or "свободн" in s and "планиров" in s:
        return 0
    m = re.search(r"(\d+)\s*-?\s*(?:комн|к\.|к\s|-к\b|room)", s)
    if m:
        return int(m.group(1))
    words = {"одно": 1, "двух": 2, "трех": 3, "трёх": 3, "четырех": 4, "четырёх": 4, "пяти": 5}
    for word, n in words.items():
        if re.search(word + r"\s*комнат", s):
            return n
    return None


def parse_floor(text) -> tuple[int | None, int | None]:
    """'5/17 эт.' -> (5, 17); '5 этаж из 17' -> (5, 17)."""
    if text is None:
        return None, None
    s = str(text).lower()
    m = (re.search(r"(\d+)\s*/\s*(\d+)\s*эт", s) or re.search(r"(\d+)\s*этаж\w*\s*из\s*(\d+)", s)
         or re.fullmatch(r"\s*(\d+)\s*(?:/|из)\s*(\d+)\s*", s))
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)\s*этаж", s)
    return (int(m.group(1)), None) if m else (None, None)


def infer_deal_type(*texts: str | None) -> DealType | None:
    s = " ".join(t for t in texts if t).lower()
    if "посуточ" in s or "/daily" in s:
        return DealType.RENT_DAILY
    if any(k in s for k in ("аренд", "сдаю", "сдается", "сдаётся", "снять", "/rent", "rent__", "/snyat")):
        return DealType.RENT
    if any(k in s for k in ("продаж", "продаю", "продается", "продаётся", "купить", "/sale", "sale__", "/prodam")):
        return DealType.SALE
    return None


def infer_property_type(*texts: str | None) -> PropertyType | None:
    s = " ".join(t for t in texts if t).lower()
    rules = [
        (PropertyType.ROOM, ("комнату", "комната ", "/room", "/komnaty")),
        (PropertyType.FLAT, ("квартир", "студи", "апартамент", "/flat", "/kvartiry", "flat__")),
        (PropertyType.HOUSE, ("дом", "коттедж", "таунхаус", "дача", "/house", "/doma_dachi")),
        (PropertyType.LAND, ("участ", "/land", "/zemelnye")),
        (PropertyType.COMMERCIAL, ("коммерч", "офис", "склад", "торгов", "/commercial", "/kommercheskaya")),
        (PropertyType.GARAGE, ("гараж", "машиномест", "/garazhi")),
    ]
    for ptype, keys in rules:
        if any(k in s for k in keys):
            return ptype
    return None


_ADDR_DROP = re.compile(
    r"\b(россия|г|город|ул|улица|д|дом|корп|к|стр|строение|кв|р-н|район|мкр|микрорайон|обл|область)\b\.?"
)
_ADDR_ABBR = {
    "проспект": "пр-т", "просп": "пр-т", "пр-кт": "пр-т",
    "переулок": "пер", "шоссе": "ш", "бульвар": "б-р", "бульв": "б-р",
    "набережная": "наб", "площадь": "пл", "проезд": "пр-д",
}


def address_key(address: str | None) -> str | None:
    """Ключ адреса для сравнения: без служебных слов, регистра и пунктуации."""
    if not address:
        return None
    s = address.lower().replace("ё", "е")
    for full, short in _ADDR_ABBR.items():
        s = re.sub(rf"\b{full}\b\.?", short, s)
    s = _ADDR_DROP.sub(" ", s)
    s = re.sub(r"[^\w\s/-]", " ", s)
    return _SPACES.sub(" ", s).strip() or None
