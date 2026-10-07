"""Командная строка: arte --help"""

import asyncio
import json
import logging
import time
from pathlib import Path

import typer

from arte.config import get_settings

app = typer.Typer(help="ARTE — сбор и мониторинг объявлений о недвижимости", no_args_is_help=True)


@app.callback()
def main(verbose: bool = typer.Option(False, "-v", "--verbose")):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.command("init-db")
def init_db():
    """Создать таблицы в базе данных."""
    from arte.storage.db import create_all
    asyncio.run(create_all())
    typer.echo(f"Готово: {get_settings().database_url}")


@app.command()
def check(url: str, source: str = typer.Option(None, help="Имя адаптера; по умолчанию — по домену"),
          backend: str = typer.Option(None, help="httpx | firecrawl | scrapling | browser"),
          llm: bool = typer.Option(False, help="Разрешить LLM-извлечение")):
    """Скачать одно объявление и показать, что удалось извлечь (без записи в БД)."""
    from arte.crawler import extract_url, make_fetcher, make_llm

    settings = get_settings().model_copy(update={k: v for k, v in
                                                 {"fetch_backend": backend, "llm_enabled": llm or None}.items()
                                                 if v is not None})

    async def go():
        async with make_fetcher(settings) as fetcher:
            _, res, listing = await extract_url(url, fetcher, make_llm(settings), source, settings=settings)
        if listing is None:
            typer.echo(f"HTTP {res.status}: объявление не разобрано")
            raise typer.Exit(1)
        typer.echo(json.dumps(listing.model_dump(mode="json"), ensure_ascii=False, indent=2))
        typer.echo(f"\nЗаполненность ключевых полей: {listing.completeness():.0%}", err=True)

    asyncio.run(go())


def _jobs(name: str | None):
    from arte.jobs import load_jobs
    jobs = load_jobs(get_settings().jobs_file)
    if name:
        jobs = [j for j in jobs if j.name == name]
    if not jobs:
        typer.echo(f"Нет заданий{' с именем ' + name if name else ''} в {get_settings().jobs_file}")
        raise typer.Exit(1)
    return jobs


@app.command()
def crawl(job: str = typer.Option(None, help="Имя задания; по умолчанию — все")):
    """Один проход по заданиям из jobs.yaml."""
    from arte.crawler import run_job
    from arte.storage.db import create_all

    async def go():
        await create_all()
        for j in _jobs(job):
            typer.echo((await run_job(j)).summary())

    asyncio.run(go())


@app.command()
def run():
    """Работать постоянно: запускать задания по их interval_minutes."""
    from arte.crawler import run_job
    from arte.storage import repo
    from arte.storage.db import create_all, sessionmaker

    jobs = _jobs(None)

    async def go():
        await create_all()
        next_run = {j.name: 0.0 for j in jobs}
        while True:
            for j in jobs:
                if time.monotonic() >= next_run[j.name]:
                    try:
                        typer.echo((await run_job(j)).summary())
                    except Exception:
                        logging.exception("задание %s упало", j.name)
                    next_run[j.name] = time.monotonic() + j.interval_minutes * 60
            async with sessionmaker()() as s:
                await repo.deactivate_stale(s)
                await s.commit()
            await asyncio.sleep(30)

    asyncio.run(go())


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    """Запустить API и дашборд."""
    import uvicorn
    uvicorn.run("arte.api.app:app", host=host, port=port)


@app.command("export")
def export_cmd(out: Path, source: str = None, deal_type: str = None,
               price_max: float = None, rooms: list[int] = typer.Option(None)):
    """Выгрузить объявления: формат по расширению файла (.csv, .xlsx, .json)."""
    from arte import export
    from arte.storage.db import create_all, sessionmaker
    from arte.storage.queries import ListingFilter, row_to_dict

    fmt = out.suffix.lstrip(".")
    if fmt not in export.FORMATS:
        raise typer.BadParameter("расширение: .csv, .xlsx или .json")

    async def go():
        await create_all()
        f = ListingFilter(source=source, deal_type=deal_type, price_max=price_max, rooms=rooms or None)
        async with sessionmaker()() as s:
            rows = [row_to_dict(r) for r in (await s.scalars(f.select())).all()]
        out.write_bytes(export.FORMATS[fmt][0](rows))
        typer.echo(f"{len(rows)} объявлений -> {out}")

    asyncio.run(go())


if __name__ == "__main__":
    app()
