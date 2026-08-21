"""URL acceptance, normalization, and public-address classification.

Requirements: 7.1, 7.2, 7.6, 7.8, 9.2, 12.4, 12.5
"""

from __future__ import annotations

import pytest

from provenance.domain.errors import FailureCode
from provenance.domain.models import NormalizedUrl
from provenance.domain.urls import (
    DETAIL_BAD_ESCAPE,
    DETAIL_BAD_HOST,
    DETAIL_MISSING_SCHEME,
    MAX_URL_LENGTH,
    is_public_network_address,
    normalize_page_input,
    parse_absolute_url,
    parse_page_input,
    remove_dot_segments,
    resolve_candidate,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("example.com", "https://example.com/"),
        ("example.com/", "https://example.com/"),
        ("EXAMPLE.com", "https://example.com/"),
        ("sub.example.co.uk", "https://sub.example.co.uk/"),
        ("example.com.", "https://example.com/"),
    ],
)
def test_bare_hostnames_become_https_root_urls(text: str, expected: str) -> None:
    assert normalize_page_input(text).unwrap() == expected


@pytest.mark.parametrize(
    "text",
    ["example.com/art", "example.com:8080", "user@example.com", "not a host", "example..com"],
)
def test_ambiguous_scheme_less_input_is_refused(text: str) -> None:
    result = parse_page_input(text)

    assert result.value is None
    assert result.unwrap_failure().code in {
        FailureCode.UNSUPPORTED_SCHEME,
        FailureCode.MALFORMED_HOST,
        FailureCode.PORT,
        FailureCode.CREDENTIALS,
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://example.com", "https://example.com/"),
        ("HTTPS://EXAMPLE.COM/", "https://example.com/"),
        ("https://example.com:443/", "https://example.com/"),
        ("http://example.com:80/", "http://example.com/"),
        ("http://example.com:443/", "http://example.com:443/"),
        ("https://example.com:80/", "https://example.com:80/"),
        ("https://example.com/a/b/../c", "https://example.com/a/c"),
        ("https://example.com/a/./b", "https://example.com/a/b"),
        ("https://example.com/../../etc", "https://example.com/etc"),
        ("https://example.com/Art/Piece.PNG", "https://example.com/Art/Piece.PNG"),
        ("https://example.com/p?B=2&a=1", "https://example.com/p?B=2&a=1"),
        ("https://example.com/p#fragment", "https://example.com/p"),
        ("https://example.com/p?q=1#frag", "https://example.com/p?q=1"),
        ("https://example.com/sp%20ace", "https://example.com/sp%20ace"),
        ("https://xn--caf-dma.example/", "https://xn--caf-dma.example/"),
        ("https://caf\u00e9.example/", "https://xn--caf-dma.example/"),
        ("https://[2001:db8::1]/", "https://[2001:db8::1]/"),
        ("https://[2001:DB8::0:1]/", "https://[2001:db8::1]/"),
        ("https://93.184.216.34/", "https://93.184.216.34/"),
    ],
)
def test_normalization_is_exact(text: str, expected: str) -> None:
    assert parse_absolute_url(text).unwrap().normalized == expected


def test_path_case_and_query_bytes_are_preserved() -> None:
    url = parse_absolute_url("https://example.com/Gallery/My%2DArt?Tag=Fine%20Art&b=2").unwrap()

    assert url.path == "/Gallery/My%2DArt"
    assert url.query == "Tag=Fine%20Art&b=2"


def test_differently_cased_paths_are_distinct() -> None:
    first = parse_absolute_url("https://example.com/art").unwrap().normalized
    second = parse_absolute_url("https://example.com/Art").unwrap().normalized

    assert first != second


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("ftp://example.com/", FailureCode.UNSUPPORTED_SCHEME),
        ("file:///etc/passwd", FailureCode.UNSUPPORTED_SCHEME),
        ("javascript:alert(1)", FailureCode.UNSUPPORTED_SCHEME),
        ("data:text/html,hi", FailureCode.UNSUPPORTED_SCHEME),
        ("https://user:pass@example.com/", FailureCode.CREDENTIALS),
        ("https://user@example.com/", FailureCode.CREDENTIALS),
        ("https://example.com:8080/", FailureCode.PORT),
        ("https://example.com:22/", FailureCode.PORT),
        ("https://example.com:0/", FailureCode.PORT),
        ("https://example.com:notaport/", FailureCode.PORT),
        ("https:///nohost", FailureCode.MALFORMED_HOST),
        ("https://exa mple.com/", FailureCode.MALFORMED_HOST),
        ("https://example..com/", FailureCode.MALFORMED_HOST),
        ("https://-example.com/", FailureCode.MALFORMED_HOST),
        ("https://example-.com/", FailureCode.MALFORMED_HOST),
        ("https://[not:an:ipv6/", FailureCode.MALFORMED_HOST),
        ("", FailureCode.MALFORMED_HOST),
        ("   ", FailureCode.MALFORMED_HOST),
    ],
)
def test_unacceptable_urls_are_refused(text: str, code: FailureCode) -> None:
    result = parse_absolute_url(text)

    assert result.value is None
    assert result.unwrap_failure().code is code


@pytest.mark.parametrize(
    "text",
    [
        "https://[not:an:ipv6/",
        "https://]reversed[/",
        "http://[::1",
        "http://::1]/",
        "https://[[::1]]/",
        "https://[]/",
        "https://\x00example.com/",
        "https://example.com:99999999999/",
        "https://" + "[" * 50,
    ],
)
def test_hostile_input_returns_a_failure_rather_than_raising(text: str) -> None:
    result = parse_absolute_url(text)

    assert result.value is None
    assert result.unwrap_failure().code in {
        FailureCode.MALFORMED_HOST,
        FailureCode.PORT,
        FailureCode.UNSUPPORTED_SCHEME,
    }


def test_scheme_relative_input_is_refused() -> None:
    failure = parse_absolute_url("//example.com/art").unwrap_failure()

    assert failure.code is FailureCode.UNSUPPORTED_SCHEME
    assert failure.safe_detail == DETAIL_MISSING_SCHEME


@pytest.mark.parametrize("path", ["/bad%", "/bad%zz", "/bad%2", "/%%20"])
def test_invalid_percent_escapes_are_refused(path: str) -> None:
    failure = parse_absolute_url(f"https://example.com{path}").unwrap_failure()

    assert failure.safe_detail == DETAIL_BAD_ESCAPE


def test_overlong_urls_are_refused() -> None:
    long_url = "https://example.com/" + "a" * MAX_URL_LENGTH

    assert parse_absolute_url(long_url).value is None


def test_overlong_hosts_are_refused() -> None:
    host = ".".join(["label"] * 60)

    failure = parse_absolute_url(f"https://{host}/").unwrap_failure()

    assert failure.safe_detail == DETAIL_BAD_HOST


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/a/b/c", "/a/b/c"),
        ("/a/./b", "/a/b"),
        ("/a/../b", "/b"),
        ("/../../a", "/a"),
        ("/a/b/..", "/a/"),
        ("/a/b/.", "/a/b/"),
        ("/", "/"),
        ("/a//b", "/a//b"),
    ],
)
def test_dot_segment_resolution(path: str, expected: str) -> None:
    assert remove_dot_segments(path) == expected


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("image.png", "https://example.com/gallery/image.png"),
        ("./image.png", "https://example.com/gallery/image.png"),
        ("../image.png", "https://example.com/image.png"),
        ("/root.png", "https://example.com/root.png"),
        ("https://cdn.example.net/a.png", "https://cdn.example.net/a.png"),
        ("//cdn.example.net/a.png", "https://cdn.example.net/a.png"),
    ],
)
def test_relative_candidates_resolve_against_the_page(candidate: str, expected: str) -> None:
    base = NormalizedUrl("https://example.com/gallery/index.html")

    assert resolve_candidate(base, candidate).unwrap().normalized == expected


@pytest.mark.parametrize(
    "candidate",
    ["", "   ", "javascript:alert(1)", "data:image/png;base64,AAAA", "ftp://example.com/a.png"],
)
def test_unsafe_candidates_are_refused(candidate: str) -> None:
    base = NormalizedUrl("https://example.com/gallery/index.html")

    assert resolve_candidate(base, candidate).value is None


@pytest.mark.parametrize(
    "address",
    [
        "93.184.216.34",
        "1.1.1.1",
        "8.8.8.8",
        "2606:2800:220:1:248:1893:25c8:1946",
        "2001:4860:4860::8888",
    ],
)
def test_public_addresses_are_accepted(address: str) -> None:
    assert is_public_network_address(address) is True


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        # Test data for an address the policy must reject, not a bind target.
        "0.0.0.0",  # noqa: S104
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "255.255.255.255",
        "192.0.2.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "2002:7f00:0001::",
        "not-an-address",
        "",
        "999.999.999.999",
        "example.com",
    ],
)
def test_excluded_and_invalid_addresses_are_rejected(address: str) -> None:
    assert is_public_network_address(address) is False


def test_ipv4_mapped_public_addresses_are_accepted() -> None:
    assert is_public_network_address("::ffff:93.184.216.34") is True


def test_link_local_metadata_endpoint_is_rejected() -> None:
    # The cloud metadata service is the canonical SSRF target.
    assert is_public_network_address("169.254.169.254") is False
