from sqlalchemy import func, select

from arte.models import Address, Listing
from arte.storage import repo
from arte.storage.db import Event, ListingRow, PricePoint


def make(source, ext, price, **kw):
    addr = kw.pop("address", Address(full="Москва, ул. Ленина, д. 10"))
    return Listing(source=source, external_id=ext, url=f"https://{source}.example/{ext}", price=price,
                   area_total=kw.pop("area", 54.0), rooms=2, floor=5, address=addr, **kw)


async def test_change_tracking(database, settings):
    async with database() as s:
        ev = await repo.upsert_listing(s, make("cian", "1", 10_000_000), settings)
        await s.commit()
    assert [e.kind for e in ev] == ["new"]

    async with database() as s:
        ev = await repo.upsert_listing(s, make("cian", "1", 9_500_000), settings)
        await s.commit()
    assert [(e.kind, e.old_price, e.new_price) for e in ev] == [("price_down", 10_000_000, 9_500_000)]

    async with database() as s:
        ev = await repo.upsert_listing(s, make("cian", "1", 9_500_000), settings)
        await s.commit()
        assert ev == []
        prices = (await s.scalars(select(PricePoint.price).order_by(PricePoint.id))).all()
        assert prices == [10_000_000, 9_500_000]

    async with database() as s:
        removed = await repo.mark_removed(s, "cian", "1")
        await s.commit()
        assert removed.kind == "removed"
        ev = await repo.upsert_listing(s, make("cian", "1", 9_000_000), settings)
        await s.commit()
        assert {e.kind for e in ev} == {"reactivated", "price_down"}
        assert await s.scalar(select(func.count()).select_from(Event)) == 5


async def test_missing_fields_do_not_overwrite_known(database, settings):
    async with database() as s:
        await repo.upsert_listing(s, make("avito", "7", 5_000_000, year_built=2001), settings)
        await s.commit()
        await repo.upsert_listing(s, make("avito", "7", None), settings)
        await s.commit()
        row = await s.scalar(select(ListingRow))
        assert (row.price, row.year_built) == (5_000_000, 2001)


async def test_cross_site_dedup(database, settings):
    async with database() as s:
        await repo.upsert_listing(s, make("cian", "1", 10_000_000), settings)
        # Та же квартира: адрес записан иначе, площадь чуть отличается.
        await repo.upsert_listing(s, make("avito", "2", 9_900_000, area=54.5,
                                          address=Address(full="г Москва, улица Ленина, дом 10")), settings)
        # Другая квартира в том же доме — другая площадь.
        await repo.upsert_listing(s, make("domclick", "3", 12_000_000, area=70), settings)
        # По координатам: адреса нет, но точка в 20 м.
        await repo.upsert_listing(s, make("yandex", "4", 1, address=Address(lat=55.7510, lon=37.6180)),
                                  settings)
        await repo.upsert_listing(s, make("cian", "5", 1, address=Address(lat=55.7512, lon=37.6181)),
                                  settings)
        await s.commit()
        rows = {r.external_id: r.object_id for r in (await s.scalars(select(ListingRow))).all()}
    assert rows["1"] == rows["2"]
    assert rows["3"] != rows["1"]
    assert rows["4"] == rows["5"] != rows["1"]
