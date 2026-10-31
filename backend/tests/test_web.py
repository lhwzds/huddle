"""Web tools: search results, readable pages, and the refusal to reach private addresses."""
import ipaddress
import socket

import httpcore
import httpx
import pytest

from huddle import web
from huddle.web import WebError, fetch_url, research_note, web_search

PUBLIC_IP = "93.184.216.34"

SEARCH_PAGE = """<!doctype html>
<html><body>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title"><a rel="nofollow" class="result__a"
        href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ffirst&amp;rut=abc">First <b>result</b></a></h2>
    <div class="result__extras"><span class="result__url">example.com/first</span></div>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ffirst">The <b>first</b> snippet.</a>
  </div>
</div>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title"><a class="result__a" href="https://example.org/second">Second result</a></h2>
    <a class="result__snippet" href="https://example.org/second">Another snippet.</a>
  </div>
</div>
</body></html>
"""

PAGE = """<!doctype html>
<html><head><title>Example Domain</title><style>body{color:red}</style>
<script>alert('boom')</script></head>
<body><header><nav>Home Products</nav></header>
<h1>Main heading</h1><p>First paragraph.</p>
<p>Second <b>paragraph</b> here.</p>
<ul><li>Item one</li><li>Item two</li></ul>
<footer>Footer text</footer><noscript>Turn on JavaScript</noscript>
<svg><text>vector</text></svg></body></html>
"""


def _fake_infos(host, port):
    if host in ("internal.example.com", "metadata.google.internal"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]


@pytest.fixture
def resolver(monkeypatch):
    """One public address for everything, and one name that points into the private range."""
    def fake(host, port, *args, **kwargs):
        return _fake_infos(host, port)
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return fake


def mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# -- search ----------------------------------------------------------------

async def test_search_parses_results():
    async with mock(lambda request: httpx.Response(200, text=SEARCH_PAGE)) as client:
        results = await web_search("huddle agents", client=client)
    assert results == [
        {"title": "First result", "url": "https://example.com/first", "snippet": "The first snippet."},
        {"title": "Second result", "url": "https://example.org/second", "snippet": "Another snippet."},
    ]


async def test_search_respects_the_limit():
    async with mock(lambda request: httpx.Response(200, text=SEARCH_PAGE)) as client:
        results = await web_search("huddle agents", client=client, limit=1)
    assert len(results) == 1 and results[0]["url"] == "https://example.com/first"


async def test_search_without_a_query_asks_nothing():
    def handler(request):
        raise AssertionError("no request expected")
    async with mock(handler) as client:
        assert await web_search("   ", client=client) == []
        assert await web_search("", client=client) == []


async def test_search_without_results_is_empty():
    page = "<html><body><div class='no-results'>nothing here</div></body></html>"
    async with mock(lambda request: httpx.Response(200, text=page)) as client:
        assert await web_search("nothing", client=client) == []


async def test_search_posts_the_query_to_duckduckgo():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, text=SEARCH_PAGE)
    async with mock(handler) as client:
        await web_search("huddle agents", client=client)
    assert seen["method"] == "POST"
    assert seen["url"] == "https://html.duckduckgo.com/html/"
    assert b"q=huddle+agents" in seen["body"]


async def test_search_failure_is_a_clean_error():
    def failing(request):
        raise httpx.ConnectError("no network")
    async with mock(failing) as client:
        with pytest.raises(WebError, match="search is unavailable right now"):
            await web_search("huddle", client=client)
    async with mock(lambda request: httpx.Response(503, text="busy")) as client:
        with pytest.raises(WebError, match="search is unavailable right now"):
            await web_search("huddle", client=client)


# -- reading a page --------------------------------------------------------

async def test_fetch_html_becomes_readable_text(resolver):
    async with mock(lambda request: httpx.Response(200, html=PAGE)) as client:
        page = await fetch_url("https://example.com/page", client=client)
    assert page["url"] == "https://example.com/page"
    assert page["title"] == "Example Domain"
    assert "# Main heading" in page["text"]
    assert "First paragraph." in page["text"]
    assert "Second paragraph here." in page["text"]
    assert "Item one" in page["text"] and "Item two" in page["text"]
    assert page["text"].count("\n\n") >= 3
    for noise in ("alert", "color:red", "Home Products", "Footer text", "Turn on JavaScript", "vector"):
        assert noise not in page["text"]
    assert page["truncated"] is False
    assert set(page) == {"url", "title", "text", "truncated"}


async def test_fetch_plain_text_is_kept_as_is(resolver):
    async with mock(lambda request: httpx.Response(200, text="just words\nand more")) as client:
        page = await fetch_url("https://example.com/notes.txt", client=client)
    assert page["text"] == "just words\nand more"
    assert page["title"] == ""
    assert page["truncated"] is False


async def test_fetch_markdown_is_kept_as_is(resolver):
    body = b"# Heading\n\nSome *markdown*.\n"
    async with mock(lambda request: httpx.Response(200, content=body, headers={"content-type": "text/markdown"})) as client:
        page = await fetch_url("https://example.com/readme.md", client=client)
    assert page["text"] == "# Heading\n\nSome *markdown*."


async def test_fetch_refuses_other_content_types(resolver):
    async with mock(lambda request: httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})) as client:
        with pytest.raises(WebError, match="only web pages and text can be read"):
            await fetch_url("https://example.com/data.json", client=client)


async def test_fetch_truncates_to_the_character_limit(resolver):
    long_page = "<html><body><p>" + ("word " * 500) + "</p></body></html>"
    async with mock(lambda request: httpx.Response(200, html=long_page)) as client:
        page = await fetch_url("https://example.com/long", client=client, max_chars=120)
    assert len(page["text"]) == 120
    assert page["truncated"] is True


async def test_fetch_stops_at_the_byte_limit(resolver):
    class Stream(httpx.AsyncByteStream):
        def __init__(self):
            self.sent = 0
        async def __aiter__(self):
            while self.sent < 100000:
                size = min(64, 100000 - self.sent)
                self.sent += size
                yield b"a" * size
    stream = Stream()
    async with mock(lambda request: httpx.Response(200, stream=stream, headers={"content-type": "text/plain"})) as client:
        page = await fetch_url("https://example.com/big", client=client, max_bytes=100)
    assert page["text"] == "a" * 100
    assert page["truncated"] is True
    assert stream.sent <= 100 + 64


async def test_a_name_that_does_not_resolve_is_a_clean_error(monkeypatch):
    def fake(host, port, *args, **kwargs):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    async with mock(lambda request: httpx.Response(200, html="<p>never</p>")) as client:
        with pytest.raises(WebError):
            await fetch_url("https://nowhere.example.com/page", client=client)


# -- private addresses are never reached -----------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/secret",
    "http://10.0.0.5/secret",
    "http://192.168.1.1/secret",
    "http://[::1]/secret",
    "http://169.254.169.254/latest/meta-data",
    "http://internal.example.com/secret",
    "http://metadata.google.internal/computeMetadata/v1/",
])
async def test_private_addresses_are_refused(resolver, url):
    def handler(request):
        raise AssertionError("no request expected")
    async with mock(handler) as client:
        with pytest.raises(WebError, match="that address is not allowed"):
            await fetch_url(url, client=client)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/file.txt"])
async def test_only_http_is_read(url):
    with pytest.raises(WebError):
        await fetch_url(url)


@pytest.mark.parametrize("url", ["", "   ", "http://[::1", "http://example.com:not-a-port/", "not a url"])
async def test_a_broken_address_is_refused(url):
    with pytest.raises(WebError):
        await fetch_url(url)


async def test_the_resolver_can_be_replaced(monkeypatch):
    async def fake(host, port):
        return ["10.0.0.5"]
    monkeypatch.setattr(web, "_resolve", fake)
    async with mock(lambda request: httpx.Response(200, html="<p>never</p>")) as client:
        with pytest.raises(WebError, match="that address is not allowed"):
            await fetch_url("https://internal.example.com/page", client=client)


async def test_a_redirect_to_a_private_address_is_refused(resolver):
    def handler(request):
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
    async with mock(handler) as client:
        with pytest.raises(WebError, match="that address is not allowed"):
            await fetch_url("https://example.com/start", client=client)


async def test_a_redirect_to_a_public_address_is_followed(resolver):
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "https://example.com/final"})
        return httpx.Response(200, html="<title>Final</title><p>done</p>")
    async with mock(handler) as client:
        page = await fetch_url("https://example.com/start", client=client)
    assert page["url"] == "https://example.com/final"
    assert page["title"] == "Final"
    assert "done" in page["text"]


async def test_too_many_redirects_are_refused(resolver):
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://example.com/again"})
    async with mock(handler) as client:
        with pytest.raises(WebError):
            await fetch_url("https://example.com/start", client=client)


async def test_a_resolver_with_another_signature_still_works(monkeypatch):
    def fake(host, port):
        return _fake_infos(host, port)
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    async with mock(lambda request: httpx.Response(200, html="<p>fine</p>")) as client:
        page = await fetch_url("https://example.com/page", client=client)
    assert "fine" in page["text"]


# -- notes in the workspace -------------------------------------------------

def test_research_note_for_a_search():
    path, markdown = research_note("search", "Huddle agents", [
        {"title": "First result", "url": "https://example.com/first", "snippet": "A snippet."},
        {"title": "Second result", "url": "https://example.org/second", "snippet": ""},
    ])
    assert path == "research/search-huddle-agents.md"
    assert markdown.startswith("# Search: Huddle agents\n\n")
    assert "1. [First result](https://example.com/first) — A snippet." in markdown
    assert "2. [Second result](https://example.org/second)" in markdown


def test_research_note_for_a_page():
    path, markdown = research_note("page", "https://example.com/docs/page", {
        "url": "https://example.com/docs/page", "title": "Docs page", "text": "Body text."})
    assert path == "research/example.com-docs-page.md"
    assert markdown.startswith("# Docs page\n\nSource: https://example.com/docs/page\nFetched: ")
    assert markdown.endswith("\n\nBody text.")


def test_research_note_slug_edge_cases():
    assert research_note("search", "Café ☕ Bar", [])[0] == "research/search-cafe-bar.md"
    empty = research_note("search", "", [])[0]
    assert empty.startswith("research/search-") and empty.endswith(".md")
    assert len(empty) > len("research/search-.md")
    unicode_only = research_note("search", "日本語", [])[0]
    assert unicode_only.startswith("research/search-") and unicode_only.endswith(".md")
    long_slug = research_note("search", "word " * 100, [])[0][len("research/search-"):-len(".md")]
    assert 0 < len(long_slug) <= 40
    for path in (research_note("search", "Café ☕ Bar", [])[0], empty, unicode_only):
        slug = path[len("research/"):-len(".md")]
        assert slug and all(character.isalnum() or character in "-" for character in slug)


def test_research_note_survives_a_broken_address():
    path, markdown = research_note("page", "http://[::1", {"text": "body"})
    assert path.startswith("research/") and path.endswith(".md")
    assert "body" in markdown


def test_research_note_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        research_note("nonsense", "x", [])


# -- the connection itself ---------------------------------------------------

async def test_a_name_that_rebinds_to_a_private_address_is_never_contacted(monkeypatch):
    answers = iter(["93.184.216.34", "127.0.0.1"])  # public for the check, private when connecting

    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    contacted = []

    async def connect(self, host, port, **kwargs):
        contacted.append(host)
        raise httpcore.ConnectError("not in tests")
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    with pytest.raises(WebError, match="that address is not allowed"):
        await fetch_url("https://rebind.example.com/page")
    assert contacted == []


async def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a, **k: _fake_infos(host, port))
    contacted = []

    async def connect(self, host, port, **kwargs):
        contacted.append((host, port))
        raise httpcore.ConnectError("not in tests")
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    with pytest.raises(WebError, match="that page could not be read"):
        await fetch_url("https://example.com/page")
    assert contacted == [(PUBLIC_IP, 443)]


async def test_pages_ignore_proxy_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    client = web.page_client()
    try:
        assert client._mounts == {}
    finally:
        await client.aclose()
