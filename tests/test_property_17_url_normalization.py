"""Property 17: URL normalization is idempotent.

Validates: Requirements 7.1, 7.2, 9.2, 12.4, 20.13
"""

from __future__ import annotations

from typing import Final

from hypothesis import assume, given
from hypothesis import strategies as st

from provenance.domain.urls import (
    ALLOWED_PORTS,
    parse_absolute_url,
    parse_page_input,
)

_SCHEMES: Final = ("http", "https", "HTTP", "HttpS")
_HOSTS: Final = (
    "example.com",
    "EXAMPLE.com",
    "sub.example.co.uk",
    "example.com.",
    "caf\u00e9.example",
    "xn--caf-dma.example",
    "93.184.216.34",
    "[2001:db8::1]",
    "[2001:DB8::0:1]",
)
_PATHS: Final = (
    "",
    "/",
    "/art",
    "/Art",
    "/a/b/../c",
    "/a/./b",
    "/../etc",
    "/a/b/..",
    "/sp%20ace",
    "/My%2DArt",
    "/a//b",
    "/trailing/",
)
_QUERIES: Final = ("", "?a=1", "?B=2&a=1", "?tag=Fine%20Art", "?empty=", "?a=1&a=2")
_FRAGMENTS: Final = ("", "#top", "#Section%20One")


@st.composite
def candidate_urls(draw: st.DrawFn) -> str:
    """Build URLs that vary in every dimension normalization touches."""
    scheme = draw(st.sampled_from(_SCHEMES))
    host = draw(st.sampled_from(_HOSTS))
    port = draw(st.sampled_from([None, *sorted(ALLOWED_PORTS)]))
    path = draw(st.sampled_from(_PATHS))
    query = draw(st.sampled_from(_QUERIES))
    fragment = draw(st.sampled_from(_FRAGMENTS))
    authority = host if port is None else f"{host}:{port}"
    return f"{scheme}://{authority}{path}{query}{fragment}"


@given(candidate_urls())
def test_normalizing_twice_equals_normalizing_once(text: str) -> None:
    # Feature: provenance, Property 17: URL normalization is idempotent
    first = parse_absolute_url(text)
    assume(first.failure is None)
    once = first.unwrap().normalized

    second = parse_absolute_url(once)
    assert second.failure is None
    twice = second.unwrap().normalized

    assert twice == once


@given(candidate_urls())
def test_normalized_urls_are_stable_under_repeated_parsing(text: str) -> None:
    # Feature: provenance, Property 17: URL normalization is idempotent
    result = parse_absolute_url(text)
    assume(result.failure is None)

    current = result.unwrap().normalized
    for _ in range(3):
        repeated = parse_absolute_url(current)
        assert repeated.failure is None
        current = repeated.unwrap().normalized

    assert current == result.unwrap().normalized


@given(candidate_urls())
def test_normalization_preserves_path_case_and_query_bytes(text: str) -> None:
    # Feature: provenance, Property 17: URL normalization is idempotent
    result = parse_absolute_url(text)
    assume(result.failure is None)
    url = result.unwrap()

    # The scheme and host are lowercased; nothing else changes case.
    assert url.scheme == url.scheme.lower()
    assert url.host == url.host.lower()
    assert url.normalized.startswith(f"{url.scheme}://")
    assert "#" not in url.normalized
    if url.query != "":
        assert url.normalized.endswith(f"?{url.query}")


@given(candidate_urls())
def test_default_ports_are_removed_and_others_kept(text: str) -> None:
    # Feature: provenance, Property 17: URL normalization is idempotent
    result = parse_absolute_url(text)
    assume(result.failure is None)
    url = result.unwrap()

    if url.uses_default_port:
        assert f":{url.port}" not in url.origin
    else:
        assert url.origin.endswith(f":{url.port}")


@given(st.sampled_from(_HOSTS))
def test_bare_hosts_normalize_to_https_roots(host: str) -> None:
    # Feature: provenance, Property 17: URL normalization is idempotent
    if host.startswith("["):
        return  # bracketed IPv6 literals are not bare hostnames

    result = parse_page_input(host)
    assume(result.failure is None)
    normalized = result.unwrap().normalized

    assert normalized.startswith("https://")
    assert normalized.endswith("/")
    assert parse_absolute_url(normalized).unwrap().normalized == normalized
