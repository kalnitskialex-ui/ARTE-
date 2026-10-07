"""Общие выборки для API и экспорта."""

from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select

from arte.storage.db import ListingRow

SORTS = {
    "new": ListingRow.first_seen.desc(),
    "price": ListingRow.price.asc(),
    "-price": ListingRow.price.desc(),
    "ppm": ListingRow.price_per_m2.asc(),
    "area": ListingRow.area_total.desc(),
}


@dataclass
class ListingFilter:
    source: str | None = None
    deal_type: str | None = None
    property_type: str | None = None
    rooms: list[int] | None = None
    price_min: float | None = None
    price_max: float | None = None
    area_min: float | None = None
    area_max: float | None = None
    q: str | None = None
    active: bool | None = True
    sort: str = "new"

    def apply(self, query: Select) -> Select:
        c = ListingRow
        conds = []
        if self.source:
            conds.append(c.source == self.source)
        if self.deal_type:
            conds.append(c.deal_type == self.deal_type)
        if self.property_type:
            conds.append(c.property_type == self.property_type)
        if self.rooms:
            conds.append(c.rooms.in_(self.rooms))
        if self.price_min is not None:
            conds.append(c.price >= self.price_min)
        if self.price_max is not None:
            conds.append(c.price <= self.price_max)
        if self.area_min is not None:
            conds.append(c.area_total >= self.area_min)
        if self.area_max is not None:
            conds.append(c.area_total <= self.area_max)
        if self.active is not None:
            conds.append(c.is_active.is_(self.active))
        if self.q:
            like = f"%{self.q}%"
            conds.append(or_(c.title.ilike(like), c.address.ilike(like), c.metro.ilike(like)))
        return query.where(*conds)

    def select(self) -> Select:
        return self.apply(select(ListingRow)).order_by(SORTS.get(self.sort, SORTS["new"]), ListingRow.id)

    def count(self) -> Select:
        return self.apply(select(func.count(ListingRow.id)))


EXPORT_COLUMNS = ["id", "source", "external_id", "url", "title", "deal_type", "property_type", "price",
                  "currency", "price_per_m2", "area_total", "area_living", "area_kitchen", "rooms",
                  "floor", "floors_total", "year_built", "address", "metro", "lat", "lon",
                  "seller_type", "object_id", "is_active", "first_seen", "last_seen"]


def row_to_dict(row: ListingRow, columns: list[str] = EXPORT_COLUMNS) -> dict:
    return {c: getattr(row, c) for c in columns}
