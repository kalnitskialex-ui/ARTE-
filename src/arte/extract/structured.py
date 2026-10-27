"""Извлечение данных из машиночитаемых частей страницы.

Порядок: JSON-LD (schema.org) -> встроенный JSON-стейт фронтенда (__NEXT_DATA__,
window.__INITIAL_STATE__ и т.п.) -> OpenGraph/meta -> регулярные выражения по тексту.
Встроенный стейт разбирается без привязки к конкретной вёрстке: ищется поддерево,
в котором сосредоточено больше всего характерных полей объявления. Поэтому
изменения дизайна площадки обычно не ломают разбор.
"""

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from arte.extract import normalize as n

# Синонимы ключей, которые встречаются во фронтенд-стейтах площадок.
FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "price": ("price", "pricerur", "priceValue", "price_value", "priceTotal", "fullprice"),
    "area_total": ("totalArea", "area_total", "areaTotal", "total_area", "square", "areaValue", "area"),
    "area_living": ("livingArea", "area_living", "living_area", "livingSquare"),
    "area_kitchen": ("kitchenArea", "area_kitchen", "kitchen_area", "kitchenSquare"),
    "rooms": ("roomsCount", "rooms", "rooms_count", "roomsNumber", "roomCount", "numberOfRooms"),
    "floor": ("floorNumber", "floor", "floor_number"),
    "floors_total": ("floorsCount", "floors_total", "floorsTotal", "totalFloors", "floorsNumber", "floors"),
    "year_built": ("buildYear", "year_built", "yearBuilt", "buildingYear", "build_year"),
    "lat": ("lat", "latitude"),
    "lon": ("lng", "lon", "longitude"),
    "address": ("fullAddress", "address", "userInput", "addressString", "displayAddress"),
    "description": ("description",),
    "title": ("title", "name", "fullTitle"),
}
_KEY_TO_FIELD = {k.lower(): f for f, keys in FIELD_KEYS.items() for k in keys}
# Поля, по которым определяется «главный» объект на странице.
_CORE = {"price", "area_total", "rooms", "floor", "floors_total", "address", "lat"}


# ---------------------------------------------------------------- JSON-LD

LD_TYPES = {
    "product", "offer", "residence", "apartment", "singlefamilyresidence", "house",
    "realestatelisting", "accommodation", "place", "apartmentcomplex", "room",
    # Домклик размечает карточку объявления как Event, а объект кладёт в location.
    "event",
}


def iter_jsonld(tree: HTMLParser) -> Iterator[dict]:
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text(strip=True) or "null")
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if "@graph" in item:
                    stack.extend(item["@graph"] if isinstance(item["@graph"], list) else [item["@graph"]])
                yield item


def _ld_type(item: dict) -> set[str]:
    t = item.get("@type", [])
    return {x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}


def _ld_address(addr) -> str | None:
    if isinstance(addr, str):
        return addr
    if isinstance(addr, dict):
        parts = [addr.get(k) for k in ("addressRegion", "addressLocality", "streetAddress")]
        return ", ".join(p for p in parts if isinstance(p, str) and p) or addr.get("name")
    return None


def from_jsonld(tree: HTMLParser) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in iter_jsonld(tree):
        types = _ld_type(item)
        if not types & LD_TYPES:
            continue
        if "event" not in types:  # у Event в name/description — промо-тексты, а не объект
            out.setdefault("title", item.get("name"))
            out.setdefault("description", item.get("description"))
        offers = item.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            out.setdefault("price", n.to_float(offers.get("price")))
            if offers.get("priceCurrency"):
                out.setdefault("currency", offers["priceCurrency"])
        if "price" in item:
            out.setdefault("price", n.to_float(item["price"]))
        size = item.get("floorSize")
        if isinstance(size, dict):
            out.setdefault("area_total", n.to_float(size.get("value")))
        if item.get("numberOfRooms") is not None:
            out.setdefault("rooms", n.to_int(item["numberOfRooms"]))
        if item.get("floorLevel") is not None:
            out.setdefault("floor", n.to_int(item["floorLevel"]))
        if item.get("yearBuilt") is not None:
            out.setdefault("year_built", n.to_int(item["yearBuilt"]))
        place = item.get("location") if isinstance(item.get("location"), dict) else {}
        if addr := _ld_address(item.get("address")) or _ld_address(place.get("address")):
            out.setdefault("address", addr)
        geo = item.get("geo") or place.get("geo")
        if isinstance(geo, dict):
            out.setdefault("lat", n.to_float(geo.get("latitude")))
            out.setdefault("lon", n.to_float(geo.get("longitude")))
        images = item.get("image")
        if images:
            imgs = images if isinstance(images, list) else [images]
            out.setdefault("photos", [i if isinstance(i, str) else i.get("url") for i in imgs
                                      if isinstance(i, str | dict)])
    return {k: v for k, v in out.items() if v not in (None, "", [])}


# ---------------------------------------------------------------- встроенный JSON

# Начало JSON-литерала после присваивания или открывающей скобки вызова:
# window.X = {...};  state = [...];  (...).concat([...]);  push({...})
_JSON_START = re.compile(r"(?:=|\()\s*(?=[{\[])")
# JS-литералы, которые встречаются в «почти JSON» стейтах (window.__SSR_STATE__={...undefined...}).
_JS_LITERALS = re.compile(r"(?<=[:\[,])(\s*)(?:undefined|NaN|-?Infinity)(?=\s*[,}\]])")
# URL-кодированный JSON в строке: window.__initialData__ = "%7B%22...%7D"
_URLENCODED = re.compile(r"=\s*[\"'](%7B[^\"']{50,})[\"']", re.IGNORECASE)
_JSON_PARSE = re.compile(r'JSON\.parse\(\s*("(?:[^"\\]|\\.)*")\s*\)')


def iter_embedded_json(tree: HTMLParser, min_len: int = 200) -> Iterator[Any]:
    decoder = json.JSONDecoder()
    for node in tree.css("script"):
        stype = (node.attributes.get("type") or "").lower()
        if stype == "application/ld+json":
            continue
        text = node.text() or ""
        if len(text) < min_len:
            continue
        if stype == "application/json":
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                pass
            continue
        for m in _JSON_PARSE.finditer(text):
            try:
                yield json.loads(json.loads(m.group(1)))
            except (json.JSONDecodeError, TypeError):
                pass
        for m in _URLENCODED.finditer(text):
            try:
                yield json.loads(unquote(m.group(1)))
            except json.JSONDecodeError:
                pass
        consumed = 0
        if "undefined" in text or "NaN" in text:
            text = _JS_LITERALS.sub(r"\1null", text)
        for m in _JSON_START.finditer(text):
            if m.end() < consumed:  # внутри уже разобранного литерала
                continue
            try:
                obj, consumed = decoder.raw_decode(text, m.end())
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict | list) and len(json.dumps(obj, ensure_ascii=False)) >= min_len:
                yield obj


def _fields_below(node: Any, depth: int) -> set[str]:
    found: set[str] = set()
    if depth < 0:
        return found
    if isinstance(node, dict):
        for k, v in node.items():
            f = _KEY_TO_FIELD.get(str(k).lower())
            if f and v not in (None, "", [], {}):
                found.add(f)
            if isinstance(v, dict | list):
                found |= _fields_below(v, depth - 1)
    elif isinstance(node, list):
        for v in node[:5]:
            found |= _fields_below(v, depth - 1)
    return found


def _iter_dicts(node: Any, depth: int = 0) -> Iterator[tuple[dict, int]]:
    if isinstance(node, dict):
        yield node, depth
        for v in node.values():
            yield from _iter_dicts(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v, depth + 1)


def find_main_object(data: Any, search_depth: int = 3) -> dict | None:
    """Поддерево с максимумом ключевых полей; при равенстве — более глубокое (конкретное)."""
    best, best_score = None, (0, 0)
    for node, depth in _iter_dicts(data):
        core = len(_fields_below(node, search_depth) & _CORE)
        score = (core, depth)
        if core >= 2 and score > best_score:
            best, best_score = node, score
    return best


def _scalar(value: Any, field: str) -> Any:
    """Значение поля может быть вложенным: {"value": 45.6, "unit": "м²"} или {"price": 1}."""
    if isinstance(value, dict):
        for k in ("value", "amount", "rur", "RUB", "number", "text", "full", field):
            if k in value and not isinstance(value[k], dict | list):
                return value[k]
        return None
    if isinstance(value, list):
        return None
    return value


def extract_fields(obj: dict, depth: int = 3) -> dict[str, Any]:
    """Обход в ширину: побеждает ближайшее к корню вхождение ключа."""
    out: dict[str, Any] = {}
    queue: list[tuple[Any, int]] = [(obj, 0)]
    while queue:
        node, d = queue.pop(0)
        if isinstance(node, dict):
            for k, v in node.items():
                f = _KEY_TO_FIELD.get(str(k).lower())
                if f and f not in out:
                    sv = _scalar(v, f)
                    if sv not in (None, ""):
                        out[f] = sv
                if d < depth and isinstance(v, dict | list):
                    queue.append((v, d + 1))
        elif isinstance(node, list) and d < depth:
            queue.extend((v, d + 1) for v in node[:5])
    return out


def from_embedded_json(tree: HTMLParser) -> dict[str, Any]:
    best_obj, best_core = None, 0
    for data in iter_embedded_json(tree):
        obj = find_main_object(data)
        if obj is None:
            continue
        core = len(_fields_below(obj, 3) & _CORE)
        if core > best_core:
            best_obj, best_core = obj, core
    if best_obj is None:
        return {}
    raw = extract_fields(best_obj)
    out: dict[str, Any] = {}
    for f, v in raw.items():
        if f == "price":
            out[f] = n.parse_price(v)
        elif f in ("area_total", "area_living", "area_kitchen"):
            out[f] = n.parse_area(v)
        elif f in ("rooms", "floor", "floors_total", "year_built"):
            out[f] = n.parse_rooms(v) if f == "rooms" and isinstance(v, str) else n.to_int(v)
        elif f in ("lat", "lon"):
            out[f] = n.to_float(v)
        elif f == "title":
            continue  # title/name во фронтенд-стейте часто означают другое (улицу, ЖК) — берём из meta
        elif isinstance(v, str):
            out[f] = v.strip()
    return {k: v for k, v in out.items() if v not in (None, "")}


# ---------------------------------------------------------------- meta и текст


def from_meta(tree: HTMLParser) -> dict[str, Any]:
    def meta(*names: str) -> str | None:
        for name in names:
            node = tree.css_first(f'meta[property="{name}"]') or tree.css_first(f'meta[name="{name}"]')
            if node and node.attributes.get("content"):
                return node.attributes["content"].strip()
        return None

    out: dict[str, Any] = {}
    title = meta("og:title") or (tree.css_first("h1").text(strip=True) if tree.css_first("h1") else None)
    if not title and tree.css_first("title"):
        title = tree.css_first("title").text(strip=True)
    if title:
        out["title"] = title
    if desc := meta("og:description", "description"):
        out["meta_description"] = desc
        # «📍 Адрес: Москва, Южнопортовая улица, 52к2, ЖК …» (Яндекс Недвижимость и др.)
        if m := re.search(r"Адрес:\s*([^.✅\n]{5,200})", desc):
            out["address"] = m.group(1).strip(" ,")
    if img := meta("og:image"):
        out["photos"] = [img]
    if lat := meta("place:location:latitude", "geo.position"):
        if ";" in lat:
            la, lo = lat.split(";", 1)
            out["lat"], out["lon"] = n.to_float(la), n.to_float(lo)
        else:
            out["lat"] = n.to_float(lat)
            out["lon"] = n.to_float(meta("place:location:longitude"))
    return out


# Подписи характеристик в карточках российских сайтов недвижимости. Только однозначные
# фразы: короткие «общая», «жилая», «кухня» встречаются и в вёрстке «значение → подпись»
# (так у Яндекс Недвижимости), где они сдвинули бы все пары на одну позицию.
LABELS: dict[str, str] = {
    "общая площадь": "area_total", "жилая площадь": "area_living", "площадь кухни": "area_kitchen",
    "этаж": "floor", "этажей в доме": "floors_total", "этажность": "floors_total",
    "год постройки": "year_built", "год сдачи": "year_built",
    "количество комнат": "rooms", "адрес": "address",
}
_AREA_VALUE = re.compile(r"^\s*\d[\d\s\u00a0]*(?:[.,]\d+)?\s*(?:м²|м2|кв\.?\s*м\.?)?\s*$", re.IGNORECASE)


def from_label_pairs(tree: HTMLParser) -> dict[str, Any]:
    """Пары «подпись → значение» в видимой разметке: «Жилая площадь» → «25 м²».

    Берутся листовые элементы в порядке документа; если текст элемента — известная
    подпись, значением считается следующий непустой лист. Работает на любой вёрстке
    вида <dt>/<dd>, <span>/<span>, <div>/<div>, и не зависит от классов.
    """
    tags = {"span", "div", "dt", "dd", "td", "th", "li", "p", "b", "strong"}
    leaves: list[str] = []
    for node in tree.css(", ".join(tags)):
        # css_first в lexbor находит и сам узел, поэтому «лист» проверяем по прямым детям.
        if any(child.tag in tags for child in node.iter()):
            continue
        text = node.text(strip=True)
        if text and len(text) <= 120:
            leaves.append(text)
    out: dict[str, Any] = {}
    for label, value in zip(leaves, leaves[1:], strict=False):
        field = LABELS.get(label.lower().rstrip(":"))
        if not field or field in out or LABELS.get(value.lower().rstrip(":")):
            continue
        if field in ("area_total", "area_living", "area_kitchen"):
            v = n.parse_area(value) if _AREA_VALUE.match(value) else None
        elif field == "floor" and not value[:1].isdigit():
            v = None
        elif field == "floor":
            v, total = n.parse_floor(value)
            if v is None:
                v = n.to_int(value)
            if total and "floors_total" not in out:
                out["floors_total"] = total
        elif field == "rooms":
            v = n.parse_rooms(value) if not value.strip().isdigit() else int(value)
        elif field == "year_built":
            m = re.fullmatch(r"\s*(1[7-9]\d\d|20\d\d)\s*(?:г\.?|год)?\s*", value)
            v = int(m.group(1)) if m else None
        elif field == "floors_total":
            v = n.to_int(value)
        else:
            v = value
        if v not in (None, ""):
            out[field] = v
    return out


def from_text(text: str) -> dict[str, Any]:
    """Последний рубеж: регулярные выражения по видимому тексту/заголовку."""
    out: dict[str, Any] = {}
    if (rooms := n.parse_rooms(text)) is not None:
        out["rooms"] = rooms
    floor, total = n.parse_floor(text)
    if floor is not None:
        out["floor"] = floor
    if total is not None:
        out["floors_total"] = total
    if (m := re.search(r"(\d[\d\s,.]*)\s*(?:м²|м2|кв\.\s*м)", text)) and (a := n.to_float(m.group(1))):
        out["area_total"] = a
    if m := re.search(r"(\d[\d\s ]{3,}(?:[.,]\d+)?)\s*(?:₽|руб)", text):
        out["price"] = n.parse_price(m.group(1))
    return out


def visible_text(tree: HTMLParser, limit: int | None = None) -> str:
    body = tree.body
    if body is None:
        return ""
    clone = HTMLParser(body.html)
    for node in clone.css("script, style, noscript, svg, iframe"):
        node.decompose()
    text = re.sub(r"\s+", " ", clone.body.text(separator=" ") if clone.body else "").strip()
    return text[:limit] if limit else text
