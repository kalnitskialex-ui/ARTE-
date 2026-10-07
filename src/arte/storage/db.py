from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class PropertyObject(Base):
    """Физический объект. Одна квартира на трёх площадках = один PropertyObject и три Listing."""

    __tablename__ = "objects"
    id: Mapped[int] = mapped_column(primary_key=True)
    address_key: Mapped[str | None] = mapped_column(String(300), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[int | None] = mapped_column(Integer, index=True)
    floor: Mapped[int | None] = mapped_column(Integer)
    area_total: Mapped[float | None] = mapped_column(Float, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    listings: Mapped[list["ListingRow"]] = relationship(back_populates="object")


class ListingRow(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(1000))
    object_id: Mapped[int | None] = mapped_column(ForeignKey("objects.id"), index=True)

    title: Mapped[str | None] = mapped_column(String(500))
    deal_type: Mapped[str | None] = mapped_column(String(20), index=True)
    property_type: Mapped[str | None] = mapped_column(String(20), index=True)
    price: Mapped[float | None] = mapped_column(Float, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    price_per_m2: Mapped[float | None] = mapped_column(Float)
    area_total: Mapped[float | None] = mapped_column(Float)
    area_living: Mapped[float | None] = mapped_column(Float)
    area_kitchen: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[int | None] = mapped_column(Integer, index=True)
    floor: Mapped[int | None] = mapped_column(Integer)
    floors_total: Mapped[int | None] = mapped_column(Integer)
    year_built: Mapped[int | None] = mapped_column(Integer)
    address: Mapped[str | None] = mapped_column(String(500))
    metro: Mapped[str | None] = mapped_column(String(200))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    seller_type: Mapped[str | None] = mapped_column(String(20))
    extraction_methods: Mapped[list] = mapped_column(JSON, default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    object: Mapped[PropertyObject | None] = relationship(back_populates="listings")
    prices: Mapped[list["PricePoint"]] = relationship(back_populates="listing",
                                                      order_by="PricePoint.seen_at")


class PricePoint(Base):
    __tablename__ = "price_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    price: Mapped[float] = mapped_column(Float)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    listing: Mapped[ListingRow] = relationship(back_populates="prices")


class Event(Base):
    """Изменения для уведомлений: new, price_down, price_up, removed, reactivated."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    old_price: Mapped[float | None] = mapped_column(Float)
    new_price: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    listing: Mapped[ListingRow] = relationship()


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker | None = None


def init_engine(url: str) -> async_sessionmaker:
    global _engine, _sessionmaker
    _engine = create_async_engine(url, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


async def create_all(url: str | None = None) -> None:
    if _engine is None:
        from arte.config import get_settings
        init_engine(url or get_settings().database_url)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def sessionmaker() -> async_sessionmaker:
    if _sessionmaker is None:
        from arte.config import get_settings
        init_engine(get_settings().database_url)
    return _sessionmaker
