import httpx
from sqlalchemy import select

from arte.analytics import city_of, deal_events, evaluate, find_deals
from arte.models import Address, DealType, Listing
from arte.storage import repo
from arte.storage.db import ListingRow

BASE_LAT, BASE_LON = 55.75, 37.62


def flat(ext, price, area=50.0, rooms=2, lat=BASE_LAT, lon=BASE_LON, address=None, floor=None, source="cian"):
    return Listing(source=source, external_id=ext, url=f"https://{source}.example/{ext}", price=price,
                   area_total=area, rooms=rooms, floor=floor if floor is not None else int(ext) % 20 + 1,
                   deal_type=DealType.SALE,
                   address=Address(full=address, lat=lat, lon=lon))


async def seed_market(s, settings):
    # 6 аналогов по 300 000 ₽/м² рядом (координаты в пределах ~500 м).
    for i in range(6):
        await repo.upsert_listing(s, flat(str(i), 300_000 * 50, lat=BASE_LAT + i * 0.0007), settings)
    await s.commit()


def test_city_of():
    assert city_of("Республика Саха (Якутия), Якутск, улица Пирогова, 9") == "якутск"
    assert city_of("г. Москва, ул. Ленина, 1") == "москва"
    assert city_of("Московская область, Химки, ул. Мира") == "химки"
    assert city_of(None) is None


async def test_fair_price_and_discount(database, settings):
    async with database() as s:
        await seed_market(s, settings)
        await repo.upsert_listing(s, flat("100", 12_000_000, floor=19), settings)  # 240 000 ₽/м² — на 20 % дешевле
        # Не аналоги: далеко, другая комнатность, сильно другая площадь.
        await repo.upsert_listing(s, flat("101", 1, lat=59.93, lon=30.36), settings)
        await repo.upsert_listing(s, flat("102", 1, rooms=3), settings)
        await repo.upsert_listing(s, flat("103", 1, area=120), settings)
        await s.commit()
        row = await s.scalar(select(ListingRow).where(ListingRow.external_id == "100"))
        v = await evaluate(s, row)
    assert v.comps == 6
    assert v.fair_price_per_m2 == 300_000
    assert v.fair_price == 15_000_000
    assert round(v.discount, 2) == 0.2


async def test_not_enough_comps(database, settings):
    async with database() as s:
        for i in range(3):
            await repo.upsert_listing(s, flat(str(i), 15_000_000), settings)
        await s.commit()
        row = await s.scalar(select(ListingRow).limit(1))
        assert await evaluate(s, row) is None


async def test_duplicates_of_same_object_are_one_comp(database, settings):
    async with database() as s:
        # Один объект на четырёх площадках + два других объекта: всего 3 аналога, не 6.
        for src in ("cian", "avito", "domclick", "yandex"):
            await repo.upsert_listing(s, flat("1", 15_000_000, source=src, floor=5), settings)
        await repo.upsert_listing(s, flat("2", 15_000_000, lat=BASE_LAT + 0.002, floor=6), settings)
        await repo.upsert_listing(s, flat("3", 15_000_000, lat=BASE_LAT + 0.003, floor=7), settings)
        await repo.upsert_listing(s, flat("9", 12_000_000, lat=BASE_LAT + 0.001, floor=9), settings)
        await s.commit()
        row = await s.scalar(select(ListingRow).where(ListingRow.external_id == "9"))
        v = await evaluate(s, row, min_comps=1)
    assert v.comps == 3


async def test_city_fallback_without_coordinates(database, settings):
    async with database() as s:
        for i in range(5):
            await repo.upsert_listing(s, flat(str(i), 15_000_000, lat=None, lon=None,
                                              address=f"Москва, ул. Тестовая, {i}"), settings)
        await repo.upsert_listing(s, flat("50", 15_000_000, lat=None, lon=None, address="Казань, ул. Баумана, 1"),
                                  settings)
        await s.commit()
        msk = await s.scalar(select(ListingRow).where(ListingRow.external_id == "0"))
        kzn = await s.scalar(select(ListingRow).where(ListingRow.external_id == "50"))
        assert (await evaluate(s, msk, min_comps=4)).comps == 4
        assert await evaluate(s, kzn, min_comps=1) is None


async def test_find_deals_and_events(database, settings):
    async with database() as s:
        await seed_market(s, settings)
        events = await repo.upsert_listing(s, flat("100", 12_000_000, floor=19), settings)
        events += await repo.upsert_listing(s, flat("101", 14_900_000, floor=18), settings)  # всего −0.7 %
        await s.commit()
        deals = await find_deals(s, min_discount=0.1)
        assert [r.external_id for r, _ in deals] == ["100"]
        good = await deal_events(s, events, min_discount=0.1)
        await s.commit()
    assert len(good) == 1
    assert (good[0].kind, good[0].old_price, good[0].new_price) == ("good_deal", 15_000_000, 12_000_000)


async def test_deals_api(database, settings):
    async with database() as s:
        await seed_market(s, settings)
        await repo.upsert_listing(s, flat("100", 12_000_000, floor=19), settings)
        await s.commit()
        target = await s.scalar(select(ListingRow.id).where(ListingRow.external_id == "100"))
    from arte.api.app import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        deals = (await client.get("/api/deals")).json()
        assert [d["id"] for d in deals] == [target]
        assert deals[0]["valuation"]["discount_pct"] == 20.0
        detail = (await client.get(f"/api/listings/{target}")).json()
        assert detail["valuation"]["fair_price"] == 15_000_000
