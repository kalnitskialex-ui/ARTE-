"""Единая модель объявления, к которой приводятся данные со всех площадок."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class DealType(StrEnum):
    SALE = "sale"
    RENT = "rent"
    RENT_DAILY = "rent_daily"


class PropertyType(StrEnum):
    FLAT = "flat"
    ROOM = "room"
    HOUSE = "house"
    LAND = "land"
    COMMERCIAL = "commercial"
    GARAGE = "garage"
    OTHER = "other"


class Address(BaseModel):
    full: str | None = None
    region: str | None = None
    city: str | None = None
    district: str | None = None
    street: str | None = None
    house: str | None = None
    metro: str | None = None
    lat: float | None = None
    lon: float | None = None


class Listing(BaseModel):
    """Объявление в нормализованном виде. Все площади — в м², цены — в рублях, если не указано иное."""

    source: str
    external_id: str
    url: HttpUrl
    title: str | None = None
    deal_type: DealType | None = None
    property_type: PropertyType | None = None

    price: float | None = None
    currency: str = "RUB"

    area_total: float | None = None
    area_living: float | None = None
    area_kitchen: float | None = None
    rooms: int | None = Field(None, description="0 — студия")
    floor: int | None = None
    floors_total: int | None = None
    year_built: int | None = None

    address: Address = Field(default_factory=Address)
    description: str | None = None
    photos: list[str] = Field(default_factory=list)
    seller_type: str | None = Field(None, description="owner / agent / developer")

    published_at: datetime | None = None
    updated_at: datetime | None = None
    is_active: bool = True

    # Как были получены поля: jsonld, embedded_json, html, llm. Полезно для отладки качества.
    extraction_methods: list[str] = Field(default_factory=list)

    @property
    def price_per_m2(self) -> float | None:
        if self.price and self.area_total:
            return round(self.price / self.area_total, 2)
        return None

    def completeness(self) -> float:
        """Доля заполненных ключевых полей — решает, нужно ли звать LLM."""
        keys = [self.price, self.area_total, self.rooms, self.floor, self.address.full, self.title]
        return sum(v is not None for v in keys) / len(keys)
