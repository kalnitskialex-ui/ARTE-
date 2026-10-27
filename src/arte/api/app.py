"""REST API и веб-дашборд."""

import statistics
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from arte import export
from arte.config import get_settings
from arte.storage.db import Event, ListingRow, PricePoint, create_all, sessionmaker
from arte.storage.queries import ListingFilter, row_to_dict

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_all()
    yield


app = FastAPI(title="ARTE", version="0.1.0", lifespan=lifespan)


class ListingOut(BaseModel):
    id: int
    source: str
    external_id: str
    url: str
    title: str | None
    deal_type: str | None
    property_type: str | None
    price: float | None
    currency: str
    price_per_m2: float | None
    area_total: float | None
    rooms: int | None
    floor: int | None
    floors_total: int | None
    address: str | None
    metro: str | None
    lat: float | None
    lon: float | None
    object_id: int | None
    is_active: bool
    first_seen: datetime
    last_seen: datetime
    photos: list[str] = []

    model_config = {"from_attributes": True}


class Page(BaseModel):
    total: int
    items: list[ListingOut]


def _filter(source, deal_type, property_type, rooms, price_min, price_max, area_min, area_max, q,
            active, sort) -> ListingFilter:
    return ListingFilter(source=source, deal_type=deal_type, property_type=property_type,
                         rooms=rooms or None, price_min=price_min, price_max=price_max,
                         area_min=area_min, area_max=area_max, q=q, active=active, sort=sort)




@app.get("/api/listings", response_model=Page)
async def list_listings(
    source: str | None = None, deal_type: str | None = None, property_type: str | None = None,
    rooms: Annotated[list[int] | None, Query()] = None,
    price_min: float | None = None, price_max: float | None = None,
    area_min: float | None = None, area_max: float | None = None,
    q: str | None = None, active: bool | None = True, sort: str = "new",
    limit: int = Query(50, le=500), offset: int = 0,
):
    f = _filter(source, deal_type, property_type, rooms, price_min, price_max, area_min, area_max,
                q, active, sort)
    async with sessionmaker()() as s:
        total = await s.scalar(f.count())
        rows = (await s.scalars(f.select().limit(limit).offset(offset))).all()
    return Page(total=total or 0, items=[ListingOut.model_validate(r) for r in rows])


@app.get("/api/listings/{listing_id}")
async def get_listing(listing_id: int):
    async with sessionmaker()() as s:
        row = await s.scalar(select(ListingRow).where(ListingRow.id == listing_id)
                             .options(selectinload(ListingRow.prices)))
        if row is None:
            raise HTTPException(404)
        duplicates = []
        if row.object_id:
            duplicates = (await s.scalars(select(ListingRow).where(
                ListingRow.object_id == row.object_id, ListingRow.id != row.id))).all()
        from arte.analytics import evaluate
        valuation = await evaluate(s, row)
    return {
        **ListingOut.model_validate(row).model_dump(),
        "valuation": valuation.as_dict() if valuation else None,
        "description": row.description,
        "extraction_methods": row.extraction_methods,
        "price_history": [{"price": p.price, "seen_at": p.seen_at} for p in row.prices],
        "same_object_on_other_sites": [ListingOut.model_validate(d).model_dump() for d in duplicates],
    }


@app.get("/api/stats")
async def stats(deal_type: str = "sale"):
    async with sessionmaker()() as s:
        by_source = dict((await s.execute(
            select(ListingRow.source, func.count()).where(ListingRow.is_active.is_(True))
            .group_by(ListingRow.source))).all())
        rows = (await s.execute(select(ListingRow.rooms, ListingRow.price_per_m2).where(
            ListingRow.is_active.is_(True), ListingRow.deal_type == deal_type,
            ListingRow.price_per_m2.is_not(None)))).all()
        objects = await s.scalar(select(func.count(func.distinct(ListingRow.object_id))))
        price_changes = await s.scalar(select(func.count()).select_from(Event).where(
            Event.kind.in_(["price_down", "price_up"])))
    groups: dict[str, list[float]] = {}
    for rooms, ppm in rows:
        key = "студия" if rooms == 0 else (f"{rooms}-комн." if rooms else "?")
        groups.setdefault(key, []).append(ppm)
    return {
        "active_by_source": by_source,
        "unique_objects": objects or 0,
        "price_changes": price_changes or 0,
        "median_price_per_m2_by_rooms": {k: round(statistics.median(v)) for k, v in sorted(groups.items())},
    }


@app.get("/api/deals")
async def deals(min_discount: float = Query(0.1, ge=0, le=0.9), deal_type: str = "sale",
                limit: int = Query(50, le=500)):
    """Объявления дешевле рыночной оценки по аналогам."""
    from arte.analytics import find_deals

    async with sessionmaker()() as s:
        found = await find_deals(s, min_discount=min_discount, deal_type=deal_type, limit=limit)
    return [{**ListingOut.model_validate(r).model_dump(), "valuation": v.as_dict()} for r, v in found]


@app.get("/api/events")
async def events(limit: int = Query(50, le=500), kind: str | None = None):
    query = select(Event, ListingRow).join(ListingRow).order_by(Event.created_at.desc()).limit(limit)
    if kind:
        query = query.where(Event.kind == kind)
    async with sessionmaker()() as s:
        rows = (await s.execute(query)).all()
    return [{"id": e.id, "kind": e.kind, "old_price": e.old_price, "new_price": e.new_price,
             "created_at": e.created_at, "listing_id": r.id, "title": r.title, "url": r.url,
             "source": r.source} for e, r in rows]


@app.get("/api/prices/{listing_id}")
async def price_history(listing_id: int):
    async with sessionmaker()() as s:
        points = (await s.scalars(select(PricePoint).where(PricePoint.listing_id == listing_id)
                                  .order_by(PricePoint.seen_at))).all()
    return [{"price": p.price, "seen_at": p.seen_at} for p in points]


@app.get("/api/export.{fmt}")
async def export_listings(
    fmt: str, source: str | None = None, deal_type: str | None = None,
    property_type: str | None = None, rooms: Annotated[list[int] | None, Query()] = None,
    price_min: float | None = None, price_max: float | None = None,
    area_min: float | None = None, area_max: float | None = None,
    q: str | None = None, active: bool | None = True, sort: str = "new",
):
    if fmt not in export.FORMATS:
        raise HTTPException(400, f"Формат: {', '.join(export.FORMATS)}")
    f = _filter(source, deal_type, property_type, rooms, price_min, price_max, area_min, area_max,
                q, active, sort)
    async with sessionmaker()() as s:
        rows = [row_to_dict(r) for r in (await s.scalars(f.select().limit(100_000))).all()]
    render, media = export.FORMATS[fmt]
    name = f"arte-{datetime.now():%Y%m%d-%H%M}.{fmt}"
    return Response(render(rows), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/jobs")
async def jobs():
    from arte.jobs import load_jobs
    return [j.model_dump() for j in load_jobs(get_settings().jobs_file)]


_running: set[str] = set()


@app.post("/api/jobs/{name}/run", status_code=202)
async def run_job_now(name: str, background: BackgroundTasks):
    from arte.crawler import run_job
    from arte.jobs import load_jobs

    job = next((j for j in load_jobs(get_settings().jobs_file) if j.name == name), None)
    if job is None:
        raise HTTPException(404, "Нет такого задания")
    if name in _running:
        raise HTTPException(409, "Задание уже выполняется")

    async def task():
        _running.add(name)
        try:
            await run_job(job)
        finally:
            _running.discard(name)

    background.add_task(task)
    return {"started": name}


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(STATIC / "index.html")
