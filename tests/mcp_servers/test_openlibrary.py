import httpx

from lumen.mcp_servers.openlibrary import _get, _search


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openlibrary.org")


async def test_search_returns_grounded_lines():
    def handler(request):
        assert "/search.json" in request.url.path
        return httpx.Response(200, json={"docs": [
            {"title": "Dune", "author_name": ["Frank Herbert"], "first_publish_year": 1965,
             "key": "/works/OL893415W", "isbn": ["9780441172719"]},
        ]})

    out = await _search(_client(handler), "dune", 5)
    assert "Dune" in out and "Frank Herbert" in out and "1965" in out


async def test_search_handles_no_results():
    def handler(request):
        return httpx.Response(200, json={"docs": []})

    assert "no results" in (await _search(_client(handler), "zzzz", 5)).lower()


async def test_search_degrades_on_http_error():
    def handler(request):
        raise httpx.ConnectError("down")

    assert "look up" in (await _search(_client(handler), "dune", 5)).lower()


async def test_get_book_by_key():
    def handler(request):
        return httpx.Response(200, json={"title": "Dune",
            "description": "Desert planet.", "first_publish_date": "1965"})

    out = await _get(_client(handler), "/works/OL893415W")
    assert "Dune" in out
