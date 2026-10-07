"""Запись объявлений с отслеживанием изменений."""

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arte.config import Settings
from arte.dedup import assign_object
from arte.models import Listing
from arte.storage.db import Event, ListingRow, PricePoint, utcnow

_FIELDS = ("url", "title", "price", "currency", "area_total", "area_living", "area_kitchen", "rooms",
           "floor", "floors_total", "year_built", "description", "photos", "seller_type",
           "extraction_methods")


def _row_values(listing: Listing) -> dict:
    values = {f: getattr(listing, f) for f in _FIELDS}
    values["url"] = str(listing.url)
    values["deal_type"] = listing.deal_type.value if listing.deal_type else None
    values["property_type"] = listing.property_type.value if listing.property_type else None
    values["price_per_m2"] = listing.price_per_m2
    a = listing.address
    values.update(address=a.full, metro=a.metro, lat=a.lat, lon=a.lon)
    return values


async def upsert_listing(session: AsyncSession, listing: Listing, settings: Settings) -> list[Event]:
    row = await session.scalar(select(ListingRow).where(
        ListingRow.source == listing.source, ListingRow.external_id == listing.external_id))
    values = _row_values(listing)
    events: list[Event] = []
    now = utcnow()

    if row is None:
        row = ListingRow(source=listing.source, external_id=listing.external_id, **values)
        session.add(row)
        await session.flush()
        events.append(Event(listing_id=row.id, kind="new", new_price=listing.price))
    else:
        old_price = row.price
        if not row.is_active:
            events.append(Event(listing_id=row.id, kind="reactivated", new_price=listing.price))
        # Не затираем известные значения пустыми, если на этот раз страница отдала меньше данных.
        for k, v in values.items():
            if v not in (None, [], ""):
                setattr(row, k, v)
        if listing.price and old_price and abs(listing.price - old_price) >= 1:
            kind = "price_down" if listing.price < old_price else "price_up"
            events.append(Event(listing_id=row.id, kind=kind, old_price=old_price,
                                new_price=listing.price))
        row.is_active = True
        row.last_seen = now

    last_price = await session.scalar(select(PricePoint.price).where(PricePoint.listing_id == row.id)
                                      .order_by(PricePoint.seen_at.desc(), PricePoint.id.desc()).limit(1))
    if listing.price and last_price != listing.price:
        session.add(PricePoint(listing_id=row.id, price=listing.price, seen_at=now))

    if row.object_id is None:
        obj = await assign_object(session, listing, area_tolerance=settings.dedup_area_tolerance,
                                  geo_radius_m=settings.dedup_geo_radius_m)
        if obj is not None:
            row.object_id = obj.id

    session.add_all(events)
    return events


async def mark_removed(session: AsyncSession, source: str, external_id: str) -> Event | None:
    row = await session.scalar(select(ListingRow).where(
        ListingRow.source == source, ListingRow.external_id == external_id))
    if row is None or not row.is_active:
        return None
    row.is_active = False
    event = Event(listing_id=row.id, kind="removed", old_price=row.price)
    session.add(event)
    return event


async def deactivate_stale(session: AsyncSession, days: int = 14) -> int:
    """Объявления, которые давно не попадались в выдаче, считаем снятыми."""
    cutoff = utcnow() - timedelta(days=days)
    result = await session.execute(update(ListingRow).where(
        ListingRow.is_active.is_(True), ListingRow.last_seen < cutoff).values(is_active=False))
    return result.rowcount or 0


async def touch(session: AsyncSession, source: str, external_id: str) -> bool:
    """Страница не изменилась (HTTP 304) — просто отмечаем, что объявление живо."""
    result = await session.execute(update(ListingRow).where(
        ListingRow.source == source, ListingRow.external_id == external_id
    ).values(last_seen=utcnow(), is_active=True))
    return bool(result.rowcount)
