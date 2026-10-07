import httpx
import pytest

from arte.fetch.http import BlockedError, DisallowedError, Fetcher


def transport(handler):
    return httpx.MockTransport(handler)


async def test_retries_then_succeeds(settings, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, text="<html>ok</html>", headers={"ETag": '"v1"'})

    async with Fetcher(settings, transport=transport(handler)) as f:
        res = await f.get("https://site.example/a")
    assert res.status == 200 and len(calls) == 2
    assert calls[1].headers["User-Agent"].startswith("ARTE-bot")


async def test_conditional_get_not_modified(settings):
    def handler(request):
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, text="<html>" + "x" * 30000 + "</html>", headers={"ETag": '"v1"'})

    async with Fetcher(settings, transport=transport(handler)) as f:
        assert not (await f.get("https://site.example/a")).not_modified
        assert (await f.get("https://site.example/a")).not_modified


async def test_stops_on_bot_protection(settings):
    def handler(request):
        return httpx.Response(200, text="<html>Пожалуйста, введите капчу</html>")

    async with Fetcher(settings, transport=transport(handler)) as f:
        with pytest.raises(BlockedError):
            await f.get("https://site.example/a")
        with pytest.raises(BlockedError):  # хост помечен — повторно не стучимся
            await f.get("https://site.example/b")


async def test_robots_txt_respected(settings):
    s = settings.model_copy(update={"respect_robots_txt": True})

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text="<html>" + "x" * 30000 + "</html>")

    async with Fetcher(s, transport=transport(handler)) as f:
        await f.get("https://site.example/public/1")
        with pytest.raises(DisallowedError):
            await f.get("https://site.example/private/1")
