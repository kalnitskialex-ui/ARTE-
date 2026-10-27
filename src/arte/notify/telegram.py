"""Уведомления в Telegram о новых объявлениях и изменениях цены."""

import html
import logging

import httpx
from sqlalchemy import select, update

from arte.config import Settings
from arte.jobs import Job
from arte.storage.db import Event, ListingRow, sessionmaker

log = logging.getLogger(__name__)

TITLES = {"new": "🆕 Новое", "price_down": "📉 Цена снижена", "price_up": "📈 Цена выросла",
          "removed": "❌ Снято", "reactivated": "♻️ Снова в продаже", "good_deal": "💎 Ниже рынка"}


def _rub(v: float | None) -> str:
    return f"{v:,.0f} ₽".replace(",", " ") if v else "—"


def format_event(event: Event, row: ListingRow) -> str:
    lines = [f"<b>{TITLES.get(event.kind, event.kind)}</b> · {row.source}"]
    if row.title:
        lines.append(html.escape(row.title))
    if event.kind in ("price_down", "price_up"):
        lines.append(f"{_rub(event.old_price)} → <b>{_rub(event.new_price)}</b>")
    elif event.kind == "good_deal" and event.old_price and event.new_price:
        pct = (1 - event.new_price / event.old_price) * 100
        lines.append(f"<b>{_rub(event.new_price)}</b> при рыночной оценке {_rub(event.old_price)} (−{pct:.0f} %)")
    else:
        lines.append(f"<b>{_rub(row.price)}</b>" + (f" · {row.price_per_m2:,.0f} ₽/м²".replace(",", " ")
                                                     if row.price_per_m2 else ""))
    if row.address:
        lines.append(html.escape(row.address))
    lines.append(row.url)
    return "\n".join(lines)


async def send(text: str, settings: Settings, client: httpx.AsyncClient | None = None) -> bool:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    async with client or httpx.AsyncClient(timeout=15) as c:
        resp = await c.post(url, json=payload)
    if resp.status_code != 200:
        log.warning("Telegram ответил %s: %s", resp.status_code, resp.text[:200])
    return resp.status_code == 200


async def notify_events(events: list[Event], job: Job, settings: Settings,
                        client: httpx.AsyncClient | None = None) -> int:
    sent = 0
    async with sessionmaker()() as session:
        ids = [e.id for e in events if e.id]
        rows = {r.id: r for r in (await session.scalars(
            select(ListingRow).where(ListingRow.id.in_([e.listing_id for e in events])))).all()}
        for event in events:
            row = rows.get(event.listing_id)
            if row is None or not job.notify.accepts(event.kind, event.new_price or row.price,
                                                     row.rooms, row.area_total):
                continue
            if await send(format_event(event, row), settings, client):
                sent += 1
        if ids:
            await session.execute(update(Event).where(Event.id.in_(ids)).values(notified=True))
            await session.commit()
    return sent
