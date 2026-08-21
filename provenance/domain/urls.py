"""URL acceptance, normalization, and the public-address policy.

Acceptance is deliberately narrow: absolute HTTP or HTTPS, no embedded credentials,
effective port 80 or 443, a well-formed host, and valid percent escapes. Everything
else is rejected before any DNS lookup happens.

Normalization lowercases the scheme and IDNA-encodes the host, removes a default port
and any fragment, maps an empty path to ``/``, and resolves dot segments, while
preserving path case and query bytes so exact whitelist scope comparison stays byte
sensitive.

The public-address predicate is the SSRF backstop. It rejects loopback, private,
link-local, multicast, unspecified, reserved, and non-unicast addresses, and looks
through IPv4-mapped, 6to4, and Teredo forms so an excluded IPv4 target cannot be
smuggled inside an IPv6 literal.

Requirements: 7.1, 7.2, 7.3, 7.6, 7.8, 9.2, 12.4, 12.5, 20.13
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urljoin, urlsplit

from provenance.domain.errors import FailureCode, Result, failed, ok
from provenance.domain.models import NormalizedUrl

SCHEME_HTTP: Final = "http"
SCHEME_HTTPS: Final = "https"
ALLOWED_SCHEMES: Final = frozenset({SCHEME_HTTP, SCHEME_HTTPS})
DEFAULT_PORTS: Final = {SCHEME_HTTP: 80, SCHEME_HTTPS: 443}
ALLOWED_PORTS: Final = frozenset({80, 443})

MAX_HOST_LENGTH: Final = 253
MAX_LABEL_LENGTH: Final = 63
MAX_URL_LENGTH: Final = 2048

PARSE_OPERATION: Final = "parse_url"

_ASCII_LABEL: Final = re.compile(r"\A[a-z0-9_-]{1,63}\Z", re.ASCII)
_PERCENT_ESCAPE: Final = re.compile(r"%[0-9A-Fa-f]{2}")
_BARE_HOST: Final = re.compile(r"\A[A-Za-z0-9._-]+\.?\Z", re.ASCII)

DETAIL_EMPTY_INPUT: Final = "empty_input"
DETAIL_TOO_LONG: Final = "url_too_long"
DETAIL_MISSING_SCHEME: Final = "scheme_required"
DETAIL_BAD_SCHEME: Final = "scheme_not_http"
DETAIL_CREDENTIALS: Final = "embedded_credentials"
DETAIL_EMPTY_HOST: Final = "host_missing"
DETAIL_BAD_HOST: Final = "host_malformed"
DETAIL_BAD_PORT: Final = "port_not_allowed"
DETAIL_BAD_ESCAPE: Final = "invalid_percent_escape"


@dataclass(frozen=True, slots=True)
class AbsoluteHttpUrl:
    """An accepted absolute HTTP or HTTPS URL in normalized form."""

    scheme: str
    host: str
    port: int
    path: str
    query: str = ""

    @property
    def uses_default_port(self) -> bool:
        """True when the port is the scheme's default."""
        return self.port == DEFAULT_PORTS[self.scheme]

    @property
    def origin(self) -> str:
        """Scheme, host, and non-default port, with no path."""
        authority = self.host if self.uses_default_port else f"{self.host}:{self.port}"
        return f"{self.scheme}://{authority}"

    @property
    def normalized(self) -> NormalizedUrl:
        """The canonical string form used for storage and comparison."""
        query = f"?{self.query}" if self.query != "" else ""
        return NormalizedUrl(f"{self.origin}{self.path}{query}")


def _fail(code: FailureCode, detail: str) -> Result[AbsoluteHttpUrl]:
    return failed(code, PARSE_OPERATION, safe_detail=detail)


def _escapes_are_valid(value: str) -> bool:
    if "%" not in value:
        return True
    # Every percent sign must begin a complete two-digit escape.
    return len(_PERCENT_ESCAPE.findall(value)) == value.count("%")


def _encode_host(raw_host: str) -> str | None:
    """Return the IDNA-encoded lowercase host, or None when it is malformed."""
    if raw_host == "":
        return None

    if raw_host.startswith("[") and raw_host.endswith("]"):
        try:
            address = ipaddress.IPv6Address(raw_host[1:-1])
        except ValueError:
            return None
        return f"[{address.compressed}]"

    try:
        return str(ipaddress.IPv4Address(raw_host))
    except ValueError:
        pass

    candidate = raw_host[:-1] if raw_host.endswith(".") else raw_host
    if candidate == "" or ".." in candidate:
        return None

    encoded_labels: list[str] = []
    for label in candidate.split("."):
        if label == "":
            return None
        if label.isascii():
            lowered = label.lower()
            if _ASCII_LABEL.match(lowered) is None:
                return None
            if lowered.startswith("-") or lowered.endswith("-"):
                return None
            encoded_labels.append(lowered)
            continue
        try:
            punycode = label.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            return None
        if len(punycode) > MAX_LABEL_LENGTH:
            return None
        encoded_labels.append(punycode.lower())

    host = ".".join(encoded_labels)
    return None if len(host) > MAX_HOST_LENGTH else host


def remove_dot_segments(path: str) -> str:
    """Resolve ``.`` and ``..`` segments per RFC 3986 without touching escapes."""
    segments = path.split("/")
    resolved: list[str] = []
    for index, segment in enumerate(segments):
        if segment == ".":
            if index == len(segments) - 1:
                resolved.append("")
            continue
        if segment == "..":
            if len(resolved) > 1:
                resolved.pop()
            if index == len(segments) - 1:
                resolved.append("")
            continue
        resolved.append(segment)

    joined = "/".join(resolved)
    if not joined.startswith("/"):
        joined = f"/{joined}"
    return joined


def parse_absolute_url(text: str) -> Result[AbsoluteHttpUrl]:
    """Accept an absolute HTTP or HTTPS URL, or report exactly why it was refused."""
    if text.strip() == "":
        return _fail(FailureCode.MALFORMED_HOST, DETAIL_EMPTY_INPUT)
    if len(text) > MAX_URL_LENGTH:
        return _fail(FailureCode.MALFORMED_HOST, DETAIL_TOO_LONG)

    try:
        # urlsplit raises for malformed authorities, such as an unclosed IPv6 bracket.
        split = urlsplit(text.strip())
    except ValueError:
        return _fail(FailureCode.MALFORMED_HOST, DETAIL_BAD_HOST)

    scheme = split.scheme.lower()
    if scheme == "":
        return _fail(FailureCode.UNSUPPORTED_SCHEME, DETAIL_MISSING_SCHEME)
    if scheme not in ALLOWED_SCHEMES:
        return _fail(FailureCode.UNSUPPORTED_SCHEME, DETAIL_BAD_SCHEME)
    if "@" in split.netloc:
        return _fail(FailureCode.CREDENTIALS, DETAIL_CREDENTIALS)

    try:
        raw_port = split.port
    except ValueError:
        return _fail(FailureCode.PORT, DETAIL_BAD_PORT)
    port = DEFAULT_PORTS[scheme] if raw_port is None else int(raw_port)
    if port not in ALLOWED_PORTS:
        return _fail(FailureCode.PORT, DETAIL_BAD_PORT)

    raw_host = split.hostname
    if raw_host is None or raw_host == "":
        return _fail(FailureCode.MALFORMED_HOST, DETAIL_EMPTY_HOST)
    # urlsplit lowercases the hostname but keeps IPv6 brackets out of ``hostname``.
    bracketed = f"[{raw_host}]" if ":" in raw_host else raw_host
    host = _encode_host(bracketed)
    if host is None:
        return _fail(FailureCode.MALFORMED_HOST, DETAIL_BAD_HOST)

    if not _escapes_are_valid(split.path) or not _escapes_are_valid(split.query):
        return _fail(FailureCode.MALFORMED_HOST, DETAIL_BAD_ESCAPE)

    path = remove_dot_segments(split.path) if split.path != "" else "/"
    return ok(AbsoluteHttpUrl(scheme=scheme, host=host, port=port, path=path, query=split.query))


def parse_page_input(text: str) -> Result[AbsoluteHttpUrl]:
    """Accept a page URL, treating a bare hostname as an HTTPS root URL."""
    candidate = text.strip()
    if candidate == "":
        return _fail(FailureCode.MALFORMED_HOST, DETAIL_EMPTY_INPUT)

    if "://" not in candidate:
        bare = candidate[:-1] if candidate.endswith("/") else candidate
        if _BARE_HOST.match(bare) is not None and "@" not in bare:
            return parse_absolute_url(f"{SCHEME_HTTPS}://{bare}/")
    return parse_absolute_url(candidate)


def normalize_page_input(text: str) -> Result[NormalizedUrl]:
    """Accept and normalize a page URL in one step."""
    parsed = parse_page_input(text)
    if parsed.failure is not None:
        return Result(failure=parsed.failure)
    return ok(parsed.unwrap().normalized)


def resolve_candidate(base: NormalizedUrl, candidate: str) -> Result[AbsoluteHttpUrl]:
    """Resolve a possibly relative image reference against the final page URL."""
    reference = candidate.strip()
    if reference == "":
        return _fail(FailureCode.MALFORMED_HOST, DETAIL_EMPTY_INPUT)
    return parse_absolute_url(urljoin(base, reference))


def is_public_network_address(value: str) -> bool:
    """True only for unicast addresses that are safe to contact.

    Loopback, private, link-local, multicast, unspecified, reserved, and non-global
    addresses are refused, and IPv4-mapped, 6to4, and Teredo forms are checked against
    their embedded IPv4 address.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False

    if isinstance(address, ipaddress.IPv6Address):
        for embedded in (address.ipv4_mapped, address.sixtofour):
            if embedded is not None:
                return is_public_network_address(str(embedded))
        if address.teredo is not None:
            server, client = address.teredo
            return is_public_network_address(str(server)) and is_public_network_address(str(client))

    excluded = (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )
    return not excluded and address.is_global
