"""Склейка одного физического объекта, размещённого на нескольких площадках.

Кандидаты отбираются по комнатам, этажу и площади (с допуском), затем подтверждаются
либо близостью координат, либо похожестью нормализованного адреса.
"""

import math

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arte.extract.normalize import address_key
from arte.models import Listing
from arte.storage.db import PropertyObject

ADDRESS_MIN_SCORE = 90


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def same_object(listing: Listing, obj: PropertyObject, geo_radius_m: float) -> bool:
    a = listing.address
    if None not in (a.lat, a.lon, obj.lat, obj.lon):
        return haversine_m(a.lat, a.lon, obj.lat, obj.lon) <= geo_radius_m
    key = address_key(a.full)
    if key and obj.address_key:
        return fuzz.token_set_ratio(key, obj.address_key) >= ADDRESS_MIN_SCORE
    return False


async def assign_object(session: AsyncSession, listing: Listing, *, area_tolerance: float,
                        geo_radius_m: float) -> PropertyObject | None:
    """Найти объект для объявления или создать новый. Без площади склейка не делается."""
    if listing.area_total is None or (listing.address.full is None and listing.address.lat is None):
        return None
    area = listing.area_total
    query = select(PropertyObject).where(
        PropertyObject.area_total.between(area * (1 - area_tolerance), area * (1 + area_tolerance))
    )
    if listing.rooms is not None:
        query = query.where(PropertyObject.rooms == listing.rooms)
    if listing.floor is not None:
        query = query.where(PropertyObject.floor == listing.floor)
    for obj in (await session.scalars(query.limit(200))).all():
        if same_object(listing, obj, geo_radius_m):
            return obj
    obj = PropertyObject(
        address_key=address_key(listing.address.full), lat=listing.address.lat,
        lon=listing.address.lon, rooms=listing.rooms, floor=listing.floor, area_total=area,
    )
    session.add(obj)
    await session.flush()
    return obj
