"""Рыночная оценка объявлений по аналогам и поиск выгодных предложений.

Аналоги: та же сделка и комнатность, площадь ±20 %, активные, с ценой за м²;
по местоположению — в радиусе от объекта (если есть координаты у обоих) или тот же
город. Рыночная цена = медиана ₽/м² аналогов × площадь объекта. Объявления того же
физического объекта (склеенные дубли с других площадок) в аналоги не входят.
"""

import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arte.dedup import haversine_m
from arte.storage.db import Event, ListingRow

AREA_TOLERANCE = 0.2
RADIUS_M = 1500
MIN_COMPS = 5
_REGION_WORDS = ("россия", "область", "край", "республика", "округ", "обл.", "ао")


def city_of(address: str | None) -> str | None:
    """«Республика Саха (Якутия), Якутск, улица Пирогова, 9» -> «якутск»."""
    if not address:
        return None
    for part in address.split(","):
        p = part.strip().lower().replace("ё", "е")
        if p.startswith("г. "):
            p = p[3:]
        if p and not any(w in p for w in _REGION_WORDS):
            return p
    return None


@dataclass
class Valuation:
    listing_id: int
    fair_price: float
    fair_price_per_m2: float
    discount: float  # доля: 0.15 = на 15 % дешевле рынка; отрицательная — дороже
    comps: int

    def as_dict(self) -> dict:
        return {"fair_price": round(self.fair_price, -3), "fair_price_per_m2": round(self.fair_price_per_m2),
                "discount_pct": round(self.discount * 100, 1), "comps": self.comps}


def _is_comp(target: ListingRow, other: ListingRow, radius_m: float) -> bool:
    if other.id == target.id or (target.object_id and other.object_id == target.object_id):
        return False
    if None not in (target.lat, target.lon, other.lat, other.lon):
        return haversine_m(target.lat, target.lon, other.lat, other.lon) <= radius_m
    city = city_of(target.address)
    return city is not None and city == city_of(other.address)


async def evaluate(session: AsyncSession, row: ListingRow, *, radius_m: float = RADIUS_M,
                   min_comps: int = MIN_COMPS) -> Valuation | None:
    if not (row.price and row.area_total and row.price_per_m2) or row.rooms is None:
        return None
    area = row.area_total
    candidates = (await session.scalars(select(ListingRow).where(
        ListingRow.is_active.is_(True),
        ListingRow.deal_type == row.deal_type,
        ListingRow.rooms == row.rooms,
        ListingRow.price_per_m2.is_not(None),
        ListingRow.area_total.between(area * (1 - AREA_TOLERANCE), area * (1 + AREA_TOLERANCE)),
    ).limit(2000))).all()
    # Один физический объект с нескольких площадок считаем одним аналогом.
    by_object: dict[object, float] = {}
    for c in candidates:
        if _is_comp(row, c, radius_m):
            by_object.setdefault(c.object_id or f"l{c.id}", c.price_per_m2)
    if len(by_object) < min_comps:
        return None
    ppm = statistics.median(by_object.values())
    fair = ppm * area
    return Valuation(row.id, fair, ppm, 1 - row.price / fair, len(by_object))


async def find_deals(session: AsyncSession, *, min_discount: float = 0.1, deal_type: str = "sale",
                     limit: int = 50) -> list[tuple[ListingRow, Valuation]]:
    rows = (await session.scalars(select(ListingRow).where(
        ListingRow.is_active.is_(True), ListingRow.deal_type == deal_type,
        ListingRow.price_per_m2.is_not(None)))).all()
    deals = []
    for row in rows:
        v = await evaluate(session, row)
        if v and v.discount >= min_discount:
            deals.append((row, v))
    deals.sort(key=lambda x: x[1].discount, reverse=True)
    return deals[:limit]


async def deal_events(session: AsyncSession, events: list[Event], *, min_discount: float) -> list[Event]:
    """Для новых объявлений и снижений цены — событие good_deal, если цена ниже рынка."""
    out: list[Event] = []
    seen: set[int] = set()
    for e in events:
        if e.kind not in ("new", "price_down", "reactivated") or e.listing_id in seen:
            continue
        seen.add(e.listing_id)
        row = await session.get(ListingRow, e.listing_id)
        if row is None:
            continue
        v = await evaluate(session, row)
        if v and v.discount >= min_discount:
            ev = Event(listing_id=row.id, kind="good_deal", old_price=round(v.fair_price, -3), new_price=row.price)
            session.add(ev)
            out.append(ev)
    return out
