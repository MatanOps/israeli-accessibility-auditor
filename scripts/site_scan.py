#!/usr/bin/env python3
"""Bounded, read-only, same-origin site crawl for the accessibility auditor.

Contract (used by scripts/audit.py --site):

    scan_site(start_url, scan_page, *, timeout=30, max_pages=100, max_seconds=600)
        -> {'results': [browser_result, ...], 'run': aggregate_run, 'errors': [str, ...]}

``scan_page(url, timeout_seconds, allowed_origin)`` is supplied by the caller and
returns the existing browser result ``{ok, html, axe, run, errors}``; it may also
raise.  Only successful results are returned in ``results`` (unchanged, including
their own per-page ``run``).  Findings are NOT computed here.

Discovery is breadth-first from the start page over same-origin ``<a href>``
links found in each successful result's rendered HTML, plus the same-origin
sitemap (``/sitemap.xml`` and ``Sitemap:`` lines from ``/robots.txt``).  Every
URL is canonicalized and boundary-validated.  Conservative exclusions (external
origin, non-http schemes, embedded credentials, non-HTML suffixes, login /
account / cart style paths and action-ful queries) are recorded with a reason so
an audit-only crawl never triggers state changes.  Nothing is ever POSTed.

Limits: ``max_pages`` browser attempts, ``max_seconds`` wall clock (the timeout
handed to the callback never exceeds the remaining budget), MAX_QUEUE unique
eligible URLs, MAX_EXCLUDED_RECORDS excluded records (all exclusions are still
counted), and MAX_SITEMAP_FILES / MAX_SITEMAP_DEPTH / MAX_SITEMAP_BYTES for
sitemaps.  Reaching any limit appends a human-readable string to ``errors`` and
leaves ``run['site']['complete']`` False.  "complete" means the reachable public
discovery was exhausted within the limits, not that the whole website was seen.

Invariant of ``run['site']``: discovered == scanned + failed + pending.

Test hooks (module attributes that tests patch): ``fetch_bytes`` (the only HTTP
path for robots/sitemaps; never follows redirects itself), ``monotonic`` and
``now_iso``.  Only the stdlib plus ``requests`` (an existing dependency) is used.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

USER_AGENT = "israeli-accessibility-auditor-site-scan"
MAX_QUEUE = 10000
MAX_EXCLUDED_RECORDS = 200
MAX_PENDING_RECORDS = 1000
MAX_SITEMAP_FILES = 20
MAX_SITEMAP_DEPTH = 3
MAX_SITEMAP_BYTES = 2_000_000
MAX_SITEMAP_URLS = 50000
MAX_REDIRECT_HOPS = 3
PER_REQUEST_TIMEOUT = 10.0          # cap for one robots/sitemap request inside the discovery budget
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
DEFAULT_PORTS = {"http": 80, "https": 443}

TRACKING_PARAM_PREFIXES = ("utm_", "_hs", "mc_", "pk_", "matomo_", "piwik_")
TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "ttclid",
                   "twclid", "igshid", "srsltid", "_ga", "_gl", "_gac", "vero_id", "wickedid",
                   "s_kwcid", "sc_cid", "ref_src", "oly_anon_id", "oly_enc_id"}
NON_HTML_SUFFIXES = {
    ".pdf", ".zip", ".rar", ".7z", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".dmg", ".exe", ".msi", ".apk",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".avif", ".bmp", ".ico", ".tif", ".tiff", ".heic",
    ".mp4", ".mp3", ".wav", ".ogg", ".oga", ".ogv", ".webm", ".m4a", ".m4v", ".mov", ".avi", ".wmv", ".flv",
    ".css", ".js", ".mjs", ".map", ".json", ".xml", ".rss", ".atom", ".txt", ".csv", ".ics", ".vcf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf", ".epub",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".swf", ".wasm",
}
ACTIONFUL_PATH_WORDS = {"login", "logout", "logoff", "signin", "signout", "signup", "register",
                        "admin", "account", "checkout", "cart", "unsubscribe"}
ACTIONFUL_PATH_SEGMENTS = {"login", "logout", "signin", "signout", "signup", "myaccount", "wpadmin",
                           "wplogin", "wploginphp", "addtocart", "removefromcart"}
ACTIONFUL_QUERY_KEYS = {"action", "do", "cmd", "op", "task", "add-to-cart", "add_to_cart", "addtocart",
                        "remove", "remove_item", "delete", "destroy", "logout", "unsubscribe",
                        "confirm", "submit", "token", "_method", "wc-ajax"}
ACTIONFUL_QUERY_FRAGMENTS = ("add-to-cart", "add_to_cart", "addtocart", "logout", "unsubscribe",
                             "delete", "remove")

_WS_RE = re.compile(r"[\t\r\n]")
_XML_DECL_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

monotonic = time.monotonic


def now_iso():
    """UTC timestamp for started_at / finished_at (patchable in tests)."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- URL helpers

def _split(url):
    if not isinstance(url, str):
        return None
    cleaned = _WS_RE.sub("", url.strip())
    if not cleaned:
        return None
    try:
        return urlsplit(cleaned)
    except ValueError:
        return None


def _is_tracking_param(key):
    lowered = key.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PARAM_PREFIXES)


def canonicalize(url):
    """Canonical http(s) URL or None (unparseable, unsupported scheme, credentials).

    Strips the fragment, lowercases scheme/host, drops default ports, sorts the
    query and removes tracking parameters (utm_*, fbclid, gclid, ...).
    """
    parts = _split(url)
    if parts is None:
        return None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        return None
    try:
        if parts.username is not None or parts.password is not None:
            return None
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not host:
        return None
    if ":" in host:
        host = "[" + host + "]"
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else "{}:{}".format(host, port)
    query = ""
    if parts.query:
        pairs = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not _is_tracking_param(k))
        query = urlencode(pairs)
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


def _origin_of(url):
    canonical = canonicalize(url)
    if canonical is None:
        return None
    parts = urlsplit(canonical)
    return "{}://{}".format(parts.scheme, parts.netloc)


def same_origin(url, origin):
    """True when url and origin share scheme, host and (default-normalized) port."""
    left, right = _origin_of(url), _origin_of(origin)
    return left is not None and right is not None and left == right


def _actionful_path(path):
    for segment in path.split("/"):
        if not segment:
            continue
        if _NON_ALNUM_RE.sub("", segment) in ACTIONFUL_PATH_SEGMENTS:
            return True
        if any(word in ACTIONFUL_PATH_WORDS for word in _NON_ALNUM_RE.split(segment)):
            return True
    return False


def _actionful_query(query):
    if not query:
        return False
    lowered = query.lower()
    if any(fragment in lowered for fragment in ACTIONFUL_QUERY_FRAGMENTS):
        return True
    return any(key.lower() in ACTIONFUL_QUERY_KEYS for key, _ in parse_qsl(lowered, keep_blank_values=True))


def classify_url(url, origin):
    """(eligible, reason): reason is '' when eligible, otherwise a short slug."""
    parts = _split(url)
    if parts is None or not parts.scheme:
        return False, "invalid-url"
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        return False, "non-http-scheme"
    try:
        if parts.username is not None or parts.password is not None:
            return False, "credentials"
    except ValueError:
        return False, "invalid-url"
    canonical = canonicalize(url)
    if canonical is None:
        return False, "invalid-url"
    if not same_origin(canonical, origin):
        return False, "external-origin"
    cparts = urlsplit(canonical)
    path = cparts.path.lower()
    decoded = unquote(path).lower()          # /%6cogout, /wp-%61dmin: decode once, then match
    for candidate in (path, decoded):
        last = candidate.rsplit("/", 1)[-1]
        if "." in last and "." + last.rsplit(".", 1)[-1] in NON_HTML_SUFFIXES:
            return False, "non-html-suffix"
    if _actionful_path(path) or _actionful_path(decoded):
        return False, "actionful-path"
    if _actionful_query(cparts.query):
        return False, "actionful-query"
    return True, ""


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []
        self.base = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value is not None:
                    self.hrefs.append(value)
                    break
        elif tag == "base" and self.base is None:
            for name, value in attrs:
                if name == "href" and value:
                    self.base = value
                    break


def extract_links(html, page_url):
    """Absolute ``<a href>`` targets in document order (deduplicated).

    A ``<base href>`` is honoured with normal browser semantics; an external base
    simply makes the links external, which the eligibility filter then excludes.
    """
    if not isinstance(html, str) or not html:
        return []
    parser = _LinkParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is tolerant; stay defensive
        pass
    base = page_url
    if parser.base:
        try:
            candidate = urljoin(page_url, _WS_RE.sub("", parser.base.strip()))
        except ValueError:
            candidate = None
        if candidate and canonicalize(candidate):
            base = candidate
    links, seen = [], set()
    for href in parser.hrefs:
        href = _WS_RE.sub("", href.strip())
        if not href or href.startswith("#"):
            continue
        try:
            absolute = urljoin(base, href)
        except ValueError:
            continue
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


# ------------------------------------------------------------------ sitemaps

def _read_some(raw, amount=8192):
    """One unbuffered read from urllib3's HTTPResponse (b'' at EOF).

    ``read1`` returns as soon as any bytes arrive, so a trickling server cannot
    hold the loop past the deadline the way ``iter_content(65536)`` would.
    """
    read1 = getattr(raw, "read1", None)
    if read1 is not None:
        try:
            return read1(amount, decode_content=True)
        except TypeError:  # pragma: no cover - older urllib3 signature
            return read1(amount)
    inner = getattr(raw, "_fp", None)
    if inner is not None and hasattr(inner, "read1"):  # pragma: no cover - urllib3 < 2
        return inner.read1(amount)
    return raw.read(1, decode_content=True)  # pragma: no cover - last resort, still bounded


def fetch_bytes(url, timeout, max_bytes=MAX_SITEMAP_BYTES):
    """Read-only GET -> (status_code, lower-cased headers, body). Never follows redirects.

    The socket timeout is the per-request ``timeout``; the body is read in small
    unbuffered pieces with a wall-clock deadline (``timeout`` seconds in total)
    checked after every read.  Raises on network errors, when the deadline
    passes or when the body exceeds ``max_bytes``; the response is closed on abort.
    """
    import requests
    timeout = float(timeout)
    deadline = time.monotonic() + timeout
    response = requests.get(url, timeout=(min(timeout, 10.0), timeout), stream=True,
                            allow_redirects=False,
                            headers={"User-Agent": USER_AGENT,
                                     "Accept": "application/xml,text/xml,text/plain;q=0.9,*/*;q=0.5"})
    try:
        headers = {str(key).lower(): value for key, value in response.headers.items()}
        declared = str(headers.get("content-length", "")).strip()
        if declared.isdigit() and int(declared) > max_bytes:
            raise ValueError("response declares {} bytes (limit {})".format(declared, max_bytes))
        chunks, size = [], 0
        while True:
            chunk = _read_some(response.raw)
            if not chunk:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("download exceeded {:g} seconds".format(timeout))
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("response exceeds {} bytes".format(max_bytes))
            chunks.append(chunk)
        return response.status_code, headers, b"".join(chunks)
    finally:
        response.close()


def _short(exc):
    return "{}: {}".format(type(exc).__name__, exc)[:200]


def _metadata_url_allowed(url, origin):
    """Gate for robots/sitemap GETs and their redirect hops.

    Same-origin http(s), no credentials, no action-ful path or query.  The
    non-HTML suffix rule is deliberately not applied: sitemaps and robots.txt
    legitimately end in .xml/.txt/.gz.
    """
    canonical = canonicalize(url)  # None for non-http(s) schemes or credentials
    if canonical is None or not same_origin(canonical, origin):
        return False
    parts = urlsplit(canonical)
    path = parts.path.lower()
    if _actionful_path(path) or _actionful_path(unquote(path).lower()):
        return False
    return not _actionful_query(parts.query)


class _BudgetExhausted(Exception):
    """The sitemap discovery budget ran out before a request could start."""


def _fetch_same_origin(url, origin, deadline):
    """fetch_bytes with manual redirect validation (same origin, http(s), <= 3 hops).

    The discovery deadline (a ``monotonic()`` instant) is re-checked before every
    request, including each redirect hop; each request gets the smaller of
    PER_REQUEST_TIMEOUT and the remaining budget.
    """
    current = url
    for _hop in range(MAX_REDIRECT_HOPS + 1):
        if not _metadata_url_allowed(current, origin):
            raise ValueError("{} is not an allowed audit-only metadata URL; not requested".format(current))
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise _BudgetExhausted()
        status, headers, body = fetch_bytes(current, min(PER_REQUEST_TIMEOUT, remaining))
        if status not in REDIRECT_STATUSES:
            return status, headers, body
        location = headers.get("location")
        if not location:
            return status, headers, body
        target = canonicalize(urljoin(current, str(location).strip()))
        if target is None or not _metadata_url_allowed(target, origin):
            raise ValueError("redirect to {} (external, non-http(s) or action-ful) was not followed".format(
                str(location).strip()[:200]))
        current = target
    raise ValueError("more than {} redirects".format(MAX_REDIRECT_HOPS))


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def parse_sitemap(data):
    """Parse a urlset or sitemapindex (namespaced or not) -> {'urls', 'sitemaps', 'error'}.

    Documents carrying a DOCTYPE or ENTITY declaration are rejected before parsing.
    """
    result = {"urls": [], "sitemaps": [], "error": None}
    if not isinstance(data, (bytes, bytearray)):
        result["error"] = "not-bytes"
        return result
    if len(data) > MAX_SITEMAP_BYTES:
        result["error"] = "oversized"
        return result
    if b"\x00" in data or data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        # UTF-16 / NUL-padded documents would bypass the byte-level pre-scan.
        result["error"] = "malformed-xml: NUL bytes or UTF-16 encoding are not accepted"
        return result
    if _XML_DECL_RE.search(data):
        result["error"] = "doctype-or-entity-declaration"
        return result
    try:
        root = ET.fromstring(bytes(data))
    except (ET.ParseError, ValueError) as exc:
        result["error"] = "malformed-xml: {}".format(str(exc)[:120])
        return result
    kind = _local(root.tag)
    if kind == "urlset":
        child_tag, target = "url", result["urls"]
    elif kind == "sitemapindex":
        child_tag, target = "sitemap", result["sitemaps"]
    else:
        result["error"] = "unexpected-root: " + (kind or "?")
        return result
    seen = set()
    for entry in root:
        if _local(entry.tag) != child_tag:
            continue
        for node in entry:
            if _local(node.tag) == "loc":
                loc = (node.text or "").strip()
                if loc and loc not in seen:
                    seen.add(loc)
                    target.append(loc)
                break
    return result


def discover_sitemaps(origin, timeout):
    """Same-origin sitemap discovery -> {'urls', 'warnings', 'truncated', 'files'}.

    ``timeout`` is the TOTAL discovery budget in seconds (measured with the
    patchable ``monotonic``); every request, including redirect hops, receives
    min(PER_REQUEST_TIMEOUT, remaining budget) and discovery stops with
    ``truncated`` True and a warning once the budget is exhausted.

    A missing (404/410) root sitemap is normal.  Anything that prevents reading a
    declared sitemap (403, malformed, oversized, external redirect, limits) sets
    ``truncated`` so the crawl is reported as partial rather than complete.
    """
    out = {"urls": [], "warnings": [], "truncated": False, "files": 0}
    base = _origin_of(origin)
    if base is None:
        out["warnings"].append("invalid origin {!r}".format(origin))
        out["truncated"] = True
        return out

    def warn(message):
        out["warnings"].append(message)
        out["truncated"] = True

    deadline = monotonic() + float(timeout)

    def exhausted():
        warn("sitemap discovery budget ({:g}s) exhausted; remaining sitemaps were not read".format(float(timeout)))

    candidates = deque([(base + "/sitemap.xml", 0, "root")])
    robots_url = base + "/robots.txt"
    try:
        status, _headers, body = _fetch_same_origin(robots_url, base, deadline)
        if status == 200:
            for line in body.decode("utf-8", "replace").splitlines():
                key, sep, value = line.partition(":")
                if sep and key.strip().lower() == "sitemap":
                    declared = value.strip()
                    try:
                        resolved = canonicalize(urljoin(robots_url, declared))
                    except ValueError:
                        resolved = None
                    if resolved is None or not _metadata_url_allowed(resolved, base):
                        warn("robots.txt declares a sitemap outside {} (or an action-ful URL) which was ignored: {}".format(
                            base, declared[:200]))
                    else:
                        candidates.append((resolved, 0, "robots.txt"))
        elif status not in (404, 410):
            warn("robots.txt returned HTTP {}; declared sitemaps could not be read".format(status))
    except _BudgetExhausted:
        exhausted()
        return out
    except Exception as exc:
        warn("robots.txt could not be read ({}); declared sitemaps are unknown".format(_short(exc)))

    visited, seen_urls, depth_warned = set(), set(), False
    while candidates:
        url, depth, source = candidates.popleft()
        canonical = canonicalize(url)
        if canonical is None or not _metadata_url_allowed(canonical, base):
            warn("sitemap outside {} (or an action-ful URL) was ignored: {}".format(base, str(url)[:200]))
            continue
        if canonical in visited:
            continue
        if out["files"] >= MAX_SITEMAP_FILES:
            warn("sitemap file limit ({}) reached; remaining sitemaps were not read".format(MAX_SITEMAP_FILES))
            break
        if deadline - monotonic() <= 0:
            exhausted()
            break
        visited.add(canonical)
        out["files"] += 1
        try:
            status, _headers, body = _fetch_same_origin(canonical, base, deadline)
        except _BudgetExhausted:
            exhausted()
            break
        except Exception as exc:
            warn("sitemap {} could not be read ({})".format(canonical, _short(exc)))
            continue
        if status in (404, 410):
            if source != "root":
                warn("declared sitemap {} returned HTTP {}".format(canonical, status))
            continue
        if status != 200:
            warn("sitemap {} returned HTTP {}; discovery is partial".format(canonical, status))
            continue
        parsed = parse_sitemap(body)
        if parsed["error"]:
            warn("sitemap {} was rejected ({})".format(canonical, parsed["error"]))
            continue
        for loc in parsed["urls"]:
            if loc in seen_urls:
                continue
            if len(out["urls"]) >= MAX_SITEMAP_URLS:
                warn("sitemap URL limit ({}) reached".format(MAX_SITEMAP_URLS))
                break
            seen_urls.add(loc)
            out["urls"].append(loc)
        for child in parsed["sitemaps"]:
            if depth + 1 > MAX_SITEMAP_DEPTH:
                if not depth_warned:
                    warn("sitemap nesting deeper than {} was not followed".format(MAX_SITEMAP_DEPTH))
                    depth_warned = True
                continue
            candidates.append((child, depth + 1, "index"))
    return out


# --------------------------------------------------------------------- crawl

def scan_site(start_url, scan_page, *, timeout=30, max_pages=100, max_seconds=600):
    """Bounded BFS crawl; see the module docstring for the full contract."""
    started_at = now_iso()
    started = monotonic()
    max_pages_n = max(1, int(max_pages))
    max_seconds_n = float(max_seconds)
    errors, results, page_results, excluded_records = [], [], [], []
    excluded_seen, seen, aliases, redirect_targets = set(), set(), set(), set()
    scanned_keys, failed_keys, done = set(), set(), set()
    queue = deque()
    flags = {"truncated": False}
    engines = {}
    attempts = 0
    start_final = None
    stop_reason = None

    def record_excluded(raw, reason):
        key = canonicalize(raw) or (raw.strip() if isinstance(raw, str) else str(raw))
        if key in excluded_seen:
            return
        excluded_seen.add(key)
        if len(excluded_records) < MAX_EXCLUDED_RECORDS:
            excluded_records.append({"url": key[:2000], "reason": reason})

    def consider(raw):
        eligible, reason = classify_url(raw, origin)
        if not eligible:
            record_excluded(raw, reason)
            return
        canonical = canonicalize(raw)
        if canonical in seen or canonical in redirect_targets:
            return
        if len(seen) >= MAX_QUEUE:
            if not flags["truncated"]:
                flags["truncated"] = True
                errors.append("Discovery limit reached: more than {} unique same-origin URLs were found; "
                              "further links were not queued, so coverage is partial.".format(MAX_QUEUE))
            return
        seen.add(canonical)
        queue.append(canonical)

    start_canonical = canonicalize(start_url)
    if start_canonical is None:
        errors.append("Start URL is not a valid public http(s) URL: {!r}".format(start_url))
        origin = None
    else:
        origin = _origin_of(start_canonical)
        seen.add(start_canonical)
        queue.append(start_canonical)
        remaining_total = max_seconds_n - (monotonic() - started)
        discovery_budget = max(0.0, min(30.0, max_seconds_n / 4.0, remaining_total))
        try:
            sitemap = discover_sitemaps(origin, discovery_budget)
        except Exception as exc:  # never let optional discovery kill the crawl
            sitemap = {"urls": [], "warnings": ["sitemap discovery failed ({})".format(_short(exc))],
                       "truncated": True, "files": 0}
        errors.extend("Sitemap: " + warning for warning in sitemap["warnings"])
        if sitemap["truncated"]:
            flags["truncated"] = True
        for loc in sitemap["urls"]:
            consider(loc)

    while queue:
        url = queue[0]
        if url in done:
            queue.popleft()
            continue
        if attempts >= max_pages_n:
            stop_reason = "page-limit"
            break
        remaining = max_seconds_n - (monotonic() - started)
        if remaining < 1.0:
            stop_reason = "time-limit"
            break
        queue.popleft()
        attempts += 1
        budget = min(float(timeout), remaining)
        failure = None
        try:
            result = scan_page(url, budget, origin)
        except Exception as exc:
            result, failure = None, _short(exc)
        ok = isinstance(result, dict) and bool(result.get("ok")) and isinstance(result.get("html"), str)
        page_run = result.get("run") if ok and isinstance(result.get("run"), dict) else {}
        if not ok:
            if failure is None:
                page_errors = result.get("errors") if isinstance(result, dict) else None
                if isinstance(page_errors, list) and page_errors:
                    failure = "; ".join(str(item) for item in page_errors)
                elif isinstance(result, dict) and result.get("ok"):
                    failure = "browser returned no HTML"
                else:
                    failure = "browser scan failed"
            failed_keys.add(url)
            failed_run = result.get("run") if isinstance(result, dict) and isinstance(result.get("run"), dict) else {}
            final_url = failed_run.get("final_url") if isinstance(failed_run.get("final_url"), str) else None
            page_results.append({"url": url, "final_url": final_url, "status": "failed", "reason": failure[:500]})
            errors.append("Page failed: {} ({})".format(url, failure[:300]))
            continue

        final = page_run.get("final_url") if isinstance(page_run.get("final_url"), str) else None
        final_canonical = canonicalize(final) if final else None
        identity = url
        if final_canonical and final_canonical != url and same_origin(final_canonical, origin):
            if final_canonical in scanned_keys or final_canonical in redirect_targets:
                aliases.add(url)                      # duplicate content of an audited page
                record_excluded(url, "redirect-alias")
                continue
            if final_canonical in seen and final_canonical not in failed_keys:
                aliases.add(url)                      # the pending target owns this result
                record_excluded(url, "redirect-alias")
                identity = final_canonical
                done.add(final_canonical)
            elif final_canonical not in seen:
                redirect_targets.add(final_canonical)
        elif final_canonical and url == start_canonical and not same_origin(final_canonical, origin):
            errors.append("Start URL redirected to a different origin ({}); the crawl stayed on {}. "
                          "Re-run with the final URL to audit that site.".format(_origin_of(final_canonical), origin))
        scanned_keys.add(identity)
        results.append(result)
        page_results.append({"url": identity, "final_url": final, "status": "completed", "reason": ""})
        if url == start_canonical:
            start_final = final or url
        if isinstance(page_run.get("engines"), dict):
            for key, value in page_run["engines"].items():
                engines.setdefault(key, value)
        for link in extract_links(result["html"], final or url):
            consider(link)

    pending_urls = [item for item in queue if item not in done and item not in scanned_keys]
    scanned, failed, pending = len(scanned_keys), len(failed_keys), len(pending_urls)
    discovered = len(seen) - len(aliases)
    if stop_reason == "page-limit":
        errors.append("Page limit reached (max_pages={}): {} discovered page(s) were not scanned; "
                      "coverage is partial.".format(max_pages_n, pending))
    elif stop_reason == "time-limit":
        errors.append("Time limit reached (max_seconds={}): {} discovered page(s) were not scanned; "
                      "coverage is partial.".format(max_seconds, pending))
    if scanned == 0:
        stop_reason = "no-pages"                   # nothing succeeded: not performed
    elif stop_reason is None:
        if flags["truncated"]:
            stop_reason = "discovery-limit"
        elif failed:
            stop_reason = "page-failures"
        else:
            stop_reason = "completed"
    complete = stop_reason == "completed" and pending == 0 and failed == 0 and not flags["truncated"]
    status = "completed" if complete else ("partial" if scanned else "not-performed")
    untested = ["Pages not reachable from public links or sitemaps, authenticated states, "
                "keyboard and screen-reader interactions"]
    if not complete:
        untested.append("Discovered pages not scanned within the limits ({} pending, {} failed)".format(pending, failed))
    run = {
        "status": status,
        "started_at": started_at,
        "finished_at": now_iso(),
        "original_url": start_url,
        "final_url": start_final,
        "rendered": scanned > 0,
        "engines": engines,
        "pages": [item["url"] for item in page_results if item["status"] == "completed"],
        "states": ["rendered"] if scanned else [],
        "untested": untested,
        "observed_selectors": {},
        "site": {
            "discovered": discovered,
            "scanned": scanned,
            "failed": failed,
            "pending": pending,
            "excluded": len(excluded_seen),
            "complete": complete,
            "stop_reason": stop_reason,
            "limits": {"max_pages": max_pages_n, "max_seconds": max_seconds},
            "page_results": page_results,
            "excluded_urls": excluded_records,
            "pending_urls": pending_urls[:MAX_PENDING_RECORDS],
        },
    }
    return {"results": results, "run": run, "errors": errors}
