"""Схема, по которой LLM (Firecrawl JSON или Claude) извлекает объявление со страницы."""

from pydantic import BaseModel, Field

EXTRACTION_PROMPT = (
    "Извлеки данные одного объявления о недвижимости с этой страницы. "
    "Бери только сведения о главном объекте страницы, а не о похожих объявлениях "
    "и рекламных блоках. Если поля нет на странице, оставь null — не додумывай. "
    "Цена — полная стоимость в рублях (для аренды — в месяц) числом. "
    "Площади — в квадратных метрах числом. Студия — rooms = 0. "
    "Не извлекай имена и телефоны продавцов."
)


class ListingExtraction(BaseModel):
    title: str | None = None
    deal_type: str | None = Field(None, description="sale | rent | rent_daily")
    property_type: str | None = Field(
        None, description="flat | room | house | land | commercial | garage | other"
    )
    price: float | None = None
    currency: str | None = Field(None, description="ISO-код, например RUB")
    area_total: float | None = None
    area_living: float | None = None
    area_kitchen: float | None = None
    rooms: int | None = None
    floor: int | None = None
    floors_total: int | None = None
    year_built: int | None = None
    address: str | None = Field(None, description="Полный адрес одной строкой")
    metro: str | None = None
    lat: float | None = None
    lon: float | None = None
    description: str | None = None
    seller_type: str | None = Field(None, description="owner | agent | developer")
