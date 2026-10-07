"""Кэш robots.txt по хостам."""

import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

ROBOTS_TTL = 6 * 3600


class RobotsCache:
    def __init__(self, client: httpx.AsyncClient, user_agent: str):
        self._client = client
        self._ua = user_agent
        self._cache: dict[str, tuple[float, RobotFileParser]] = {}

    async def _load(self, origin: str) -> RobotFileParser:
        parser = RobotFileParser()
        try:
            resp = await self._client.get(f"{origin}/robots.txt")
        except httpx.HTTPError:
            # Сеть недоступна — не считаем это разрешением на всё, но и не блокируем навсегда.
            parser.parse([])
            return parser
        if resp.status_code in (401, 403):
            parser.disallow_all = True
        elif resp.status_code >= 400:
            parser.allow_all = True
        else:
            parser.parse(resp.text.splitlines())
        return parser

    async def get(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._cache.get(origin)
        if cached and time.monotonic() - cached[0] < ROBOTS_TTL:
            return cached[1]
        parser = await self._load(origin)
        self._cache[origin] = (time.monotonic(), parser)
        return parser

    async def allowed(self, url: str) -> bool:
        return (await self.get(url)).can_fetch(self._ua, url)

    async def crawl_delay(self, url: str) -> float | None:
        delay = (await self.get(url)).crawl_delay(self._ua)
        return float(delay) if delay is not None else None
