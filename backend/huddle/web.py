"""Web access for agents: search and page reading, kept safe inside the backend.

Agents run in a sandbox with no network, so anything that needs the internet
goes through this module. Two rules shape it: the model never sees a raw
exception, and no request ever reaches a private, loopback or metadata
address. Only http and https are allowed, redirects are followed by hand (at
most three), every hop is checked before it is contacted, and the connection
itself goes only to an address that passed the check (see _PublicOnlyBackend).
"""
from __future__ import annotations

import asyncio
import inspect
import ipaddress
import re
import socket
import ssl
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

import certifi
import httpcore
import httpx

# The search endpoint is configuration (config/huddle.yaml web.search_url); this is only the default
# for callers that pass none, such as tests.
DEFAULT_SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
MAX_REDIRECTS = 3
MAX_BYTES = 2_000_000
MAX_CHARS = 20_000
SEARCH_UNAVAILABLE = "search is unavailable right now"
ADDRESS_NOT_ALLOWED = "that address is not allowed"
NOT_A_PAGE = "only web pages and text can be read"

# Names that point at cloud metadata services even when they resolve elsewhere.
BLOCKED_HOSTS = {"metadata.google.internal", "metadata.google.com", "instance-data.ec2.internal"}

# Address space that must never be reached from a tool call. The documentation
# ranges (192.0.2.0/24 and friends) are left alone: they are not private.
BLOCKED_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
    "::/128", "::1/128", "64:ff9b::/96", "fc00::/7", "fe80::/10", "ff00::/8",
))

HTML_TYPES = {"text/html", "application/xhtml+xml", "application/xml", "text/xml"}
TEXT_TYPES = {"text/plain", "text/markdown", "text/x-markdown"}


class WebError(Exception):
    """A web problem whose message is safe to show the model."""


# -- helpers ---------------------------------------------------------------

def _collapse(text: str) -> str:
    """One line, no repeated spaces."""
    return re.sub(r"\s+", " ", text or "").strip()


def _split(url: str):
    """The parts of a URL, or a plain refusal when it makes no sense."""
    try:
        return urlsplit(url or "")
    except ValueError as exc:
        raise WebError(ADDRESS_NOT_ALLOWED) from exc


def _address_is_blocked(address: str) -> bool:
    """True when an address must never be contacted."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if ip.version == 6:
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is not None:
            return _address_is_blocked(str(embedded))
    if ip.is_unspecified or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        return True
    return any(ip in network for network in BLOCKED_NETWORKS if network.version == ip.version)


async def _maybe_await(value):
    """Await a resolver that may or may not be a coroutine function."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _resolve(host: str, port: int) -> list[str]:
    """Every address the host resolves to.

    The resolver is looked up at call time, so the usual ways of replacing it
    (a patched socket.getaddrinfo, a patched event loop) all work.
    """
    loop = asyncio.get_running_loop()
    attempts = []
    module_resolver = getattr(asyncio, "getaddrinfo", None)
    if module_resolver is not None:
        attempts.append(lambda: module_resolver(host, port))
    loop_resolver = getattr(loop, "getaddrinfo", None)
    if loop_resolver is not None:
        attempts.append(lambda: loop_resolver(host, port))
    attempts.append(lambda: loop.run_in_executor(None, socket.getaddrinfo, host, port))
    infos = None
    for attempt in attempts:
        try:
            infos = await _maybe_await(attempt())
        except TypeError:  # a resolver with another signature; try the next one
            continue
        except WebError:
            raise
        except Exception as exc:  # a name that does not resolve, or no resolver
            raise WebError("that address could not be found") from exc
        break
    if infos is None:
        raise WebError("that address could not be found")
    addresses = []
    for info in infos or ():
        address = ""
        if isinstance(info, str):
            address = info
        elif isinstance(info, (tuple, list)) and info:
            last = info[-1]
            if isinstance(last, (tuple, list)) and last:
                address = str(last[0])
            elif isinstance(last, str):
                address = last
        if address and address not in addresses:
            addresses.append(address)
    return addresses


async def _public_addresses(host: str, port: int) -> list[str]:
    """The addresses of a host, or a refusal when any of them is not public."""
    name = (host or "").strip().lower().rstrip(".").strip("[]")
    if not name or name in BLOCKED_HOSTS:
        raise WebError(ADDRESS_NOT_ALLOWED)
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        if _address_is_blocked(name):
            raise WebError(ADDRESS_NOT_ALLOWED)
        return [name]
    addresses = []
    for entry in await _resolve(name, port):
        address = str(entry)
        if isinstance(entry, (tuple, list)) and entry:  # a raw resolver answer
            last = entry[-1]
            address = str(last[0]) if isinstance(last, (tuple, list)) and last else str(last)
        if _address_is_blocked(address):
            raise WebError(ADDRESS_NOT_ALLOWED)
        addresses.append(address)
    if not addresses:
        raise WebError("that address could not be found")
    return addresses


async def _ensure_public(host: str, port: int) -> None:
    """Refuse anything that is not a public address, before it is contacted."""
    await _public_addresses(host, port)


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Connects only to addresses that were checked a moment before, at connection time.

    The check before each request gives a clear refusal, but the HTTP client would resolve the name
    again to connect, and a name can answer differently the second time (DNS rebinding). Here the
    name is resolved once, every address is checked, and the connection goes to one of exactly
    those addresses. TLS still verifies the certificate against the name.
    """

    def __init__(self):
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        error = None
        for address in await _public_addresses(host, port):
            try:
                return await self._inner.connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                                     socket_options=socket_options)
            except httpcore.ConnectError as exc:
                error = exc
        raise error or WebError("that page could not be read")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise WebError(ADDRESS_NOT_ALLOWED)

    async def sleep(self, seconds):
        await self._inner.sleep(seconds)


def page_client() -> httpx.AsyncClient:
    """The HTTP client for reading pages: public addresses only, no proxy from the environment."""
    transport = httpx.AsyncHTTPTransport()
    # httpx has no public way to choose the network backend; the pool it builds is replaced by
    # one that uses ours, with the same certificate checks.
    transport._pool = httpcore.AsyncConnectionPool(ssl_context=ssl.create_default_context(cafile=certifi.where()),
                                                   network_backend=_PublicOnlyBackend())
    return httpx.AsyncClient(transport=transport, timeout=TIMEOUT, follow_redirects=False, trust_env=False)


# -- search ----------------------------------------------------------------

def _resolve_link(href: str) -> str:
    """The real target of a search link, unwrapping the redirect when there is one."""
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    query = urlsplit(href).query
    for part in query.split("&"):
        if part.startswith("uddg="):
            target = unquote(part[5:])
            return target or href
    return href


class _SearchResults(HTMLParser):
    """Pick the title, link and snippet out of a DuckDuckGo results page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self.links: list[dict] = []
        self._mode = ""
        self._buffer: list[str] = []
        self._href = ""
        self._in_heading = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = " ".join((attributes.get("class") or "").split())
        href = attributes.get("href") or ""
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = True
        if tag != "a":
            if "snippet" in classes and not self._mode:
                self._mode = "snippet"
                self._buffer = []
            return
        if "result__a" in classes or (self._in_heading and not self._mode):
            self._mode = "title"
            self._buffer = []
            self._href = href
        elif "snippet" in classes:
            self._mode = "snippet"
            self._buffer = []
        elif "uddg=" in href:
            self._mode = "link"
            self._buffer = []
            self._href = href

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = False
        if self._mode in ("title", "link") and tag == "a":
            title = _collapse("".join(self._buffer))
            url = _resolve_link(self._href)
            if title and url:
                if self._mode == "title":
                    self.results.append({"title": title, "url": url, "snippet": ""})
                else:
                    self.links.append({"title": title, "url": url, "snippet": ""})
            self._mode = ""
            self._buffer = []
        elif self._mode == "snippet" and tag in ("a", "div"):
            snippet = _collapse("".join(self._buffer))
            if self.results:
                self.results[-1]["snippet"] = snippet
            self._mode = ""
            self._buffer = []

    def handle_data(self, data):
        if self._mode:
            self._buffer.append(data)


async def web_search(query: str, *, client: httpx.AsyncClient | None = None, limit: int = 5,
                     search_url: str = DEFAULT_SEARCH_URL) -> list[dict]:
    """Search the web; returns [{"title", "url", "snippet"}], newest first."""
    question = (query or "").strip()
    if not question or limit <= 0:
        return []
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
    try:
        try:
            response = await client.post(search_url, data={"q": question},
                                         headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            if response.status_code >= 400:
                raise WebError(SEARCH_UNAVAILABLE)
            page = response.text
        except WebError:
            raise
        except Exception as exc:
            raise WebError(SEARCH_UNAVAILABLE) from exc
    finally:
        if own:
            await client.aclose()
    parser = _SearchResults()
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        pass
    results = [item for item in (parser.results or parser.links) if item.get("url") and item.get("title")]
    return results[:limit]


# -- reading a page --------------------------------------------------------

SKIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "svg", "template", "iframe"}
BLOCK_TAGS = {"p", "div", "section", "article", "main", "aside", "ul", "ol", "li", "dl", "dt", "dd",
              "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "td", "th",
              "blockquote", "pre", "figure", "figcaption", "hr", "address", "details", "summary",
              "fieldset", "form", "label", "option", "time", "code"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _Readable(HTMLParser):
    """Turn a page into readable text: no scripts, no navigation, no clutter."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._title_buffer: list[str] = []
        self._buffer: list[str] = []
        self._prefix = ""

    def _flush(self):
        text = _collapse("".join(self._buffer))
        self._buffer = []
        if text:
            self.blocks.append(self._prefix + text)
        self._prefix = ""

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = True
            self._title_buffer = []
            return
        if tag in HEADING_TAGS:
            self._flush()
            self._prefix = "# "
            return
        if tag in BLOCK_TAGS:
            self._flush()
            return
        if tag == "br":
            self._buffer.append(" ")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "title":
            self.title = _collapse("".join(self._title_buffer))
            self._in_title = False
            return
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self._title_buffer.append(data)
            return
        self._buffer.append(data)

    def text(self) -> str:
        self._flush()
        return "\n\n".join(self.blocks).strip()


def _html_to_text(page: str) -> tuple[str, str]:
    """(title, readable text) of an HTML page."""
    parser = _Readable()
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        pass
    return parser.title, parser.text()


def _charset(content_type: str) -> str:
    for part in (content_type or "").split(";")[1:]:
        name, _, value = part.strip().partition("=")
        if name.strip().lower() == "charset" and value.strip():
            return value.strip().strip('"').strip("'")
    return "utf-8"


async def _read_body(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """The body of a response, stopped at max_bytes."""
    chunks: list[bytes] = []
    total = 0
    cut = False
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        room = max_bytes - total
        if room <= 0:
            cut = True
            break
        if len(chunk) > room:
            chunks.append(chunk[:room])
            total = max_bytes
            cut = True
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), cut


async def fetch_url(url: str, *, client: httpx.AsyncClient | None = None, max_bytes: int = MAX_BYTES,
                    max_chars: int = MAX_CHARS) -> dict:
    """Read a web page as text: {"url", "title", "text", "truncated"}."""
    target = (url or "").strip()
    if not target:
        raise WebError(ADDRESS_NOT_ALLOWED)
    if _split(target).scheme.lower() not in ("http", "https"):
        raise WebError("only http and https pages can be read")
    own = client is None
    if own:
        client = page_client()
    try:
        return await _fetch(client, target, max_bytes=max_bytes, max_chars=max_chars)
    finally:
        if own:
            await client.aclose()


async def _fetch(client: httpx.AsyncClient, target: str, *, max_bytes: int, max_chars: int) -> dict:
    current = target
    for _ in range(MAX_REDIRECTS + 1):
        parts = _split(current)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            raise WebError("only http and https pages can be read")
        host = parts.hostname or ""
        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise WebError(ADDRESS_NOT_ALLOWED) from exc
        await _ensure_public(host, port)
        try:
            async with client.stream("GET", current, follow_redirects=False, timeout=TIMEOUT,
                                     headers={"User-Agent": USER_AGENT,
                                              "Accept": "text/html,text/plain,text/markdown;q=0.9,*/*;q=0.5"}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise WebError("that page could not be read")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise WebError("that page could not be read")
                body, cut = await _read_body(response, max_bytes)
                content_type = response.headers.get("content-type", "")
        except WebError:
            raise
        except httpx.HTTPError as exc:
            raise WebError("that page could not be read") from exc
        except Exception as exc:
            raise WebError("that page could not be read") from exc
        media = content_type.split(";")[0].strip().lower()
        page = body.decode(_charset(content_type), errors="replace")
        if media in HTML_TYPES:
            title, text = _html_to_text(page)
        elif media in TEXT_TYPES:
            title, text = "", page.strip()
        elif not media:
            title, text = _html_to_text(page) if page.lstrip().startswith("<") else ("", page.strip())
        else:
            raise WebError(NOT_A_PAGE)
        truncated = cut
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True
        return {"url": current, "title": title, "text": text, "truncated": truncated}
    raise WebError("that page redirects too much")


# -- notes for the workspace ------------------------------------------------

def _slug(text: str, fallback: str = "untitled") -> str:
    """Lowercase ascii letters, digits and hyphens; never empty."""
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    cleaned = cleaned[:40].strip("-")
    return cleaned or fallback


def _host_of(url: str) -> str:
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        host = ""
    return host or "page"


def _path_of(url: str) -> str:
    try:
        return urlsplit(url or "").path or ""
    except ValueError:
        return ""


def research_note(kind: str, query_or_url: str, payload) -> tuple[str, str]:
    """The file to write in the conversation workspace and what to write in it."""
    kind = (kind or "").strip().lower()
    if kind == "search":
        query = str(query_or_url or "").strip()
        results = payload or []
        if isinstance(results, dict):
            results = [results]
        lines = []
        for index, item in enumerate(results, 1):
            item = item if isinstance(item, dict) else {}
            title = _collapse(str(item.get("title") or ""))
            url = str(item.get("url") or "").strip()
            snippet = _collapse(str(item.get("snippet") or ""))
            entry = f"{index}. [{title}]({url})"
            if snippet:
                entry += f" — {snippet}"
            lines.append(entry)
        markdown = f"# Search: {query}\n\n" + "\n".join(lines)
        return f"research/search-{_slug(query)}.md", markdown
    if kind == "page":
        page = payload if isinstance(payload, dict) else {}
        url = str(page.get("url") or query_or_url or "").strip()
        raw_title = _collapse(str(page.get("title") or ""))
        text = str(page.get("text") or "")
        fetched = str(page.get("fetched") or "").strip() or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        heading = raw_title or url
        slug = _slug(raw_title or (_path_of(url) or url))
        markdown = f"# {heading}\n\nSource: {url}\nFetched: {fetched}\n\n{text}"
        return f"research/{_host_of(url)}-{slug}.md", markdown
    raise ValueError(f"unknown research kind: {kind}")
