"""robots.txt retrieval and evaluation.

Rules are fetched from the destination origin before the page itself, through the same
pinned transport, and charged to the same budget.

Status handling follows RFC 9309:

* 2xx: parse the rules and apply them
* 3xx: followed by the redirect follower, up to the redirect limit
* 4xx: no rules exist, so the page is allowed
* 5xx, timeout, or transport refusal: rules are unavailable, and the scan pauses for an
  explicit user decision rather than silently crawling

Requirements: 8.1, 8.2, 8.3, 8.5, 8.11, 8.13
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.robotparser import RobotFileParser

from provenance.domain.errors import Result, ok
from provenance.domain.scan_budget import ResponseKind, ScanBudget
from provenance.domain.urls import AbsoluteHttpUrl
from provenance.infrastructure.network.redirects import RedirectFollower
from provenance.ports.http import SafeRequest

ROBOTS_PATH: Final = "/robots.txt"
ROBOTS_OPERATION: Final = "evaluate_robots"

SUCCESS_MINIMUM: Final = 200
SUCCESS_MAXIMUM: Final = 299
CLIENT_ERROR_MINIMUM: Final = 400
CLIENT_ERROR_MAXIMUM: Final = 499


class RobotsVerdict(StrEnum):
    """What the destination's robots.txt says about the requested page."""

    ALLOWED = "allowed"
    DISALLOWED = "disallowed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    """The verdict plus the evidence behind it, shown to the user."""

    verdict: RobotsVerdict
    robots_url: str
    status: int | None = None
    detail: str | None = None
    matched_rule: str | None = None

    @property
    def may_continue(self) -> bool:
        """True only when rules explicitly permit the page."""
        return self.verdict is RobotsVerdict.ALLOWED

    @property
    def needs_user_decision(self) -> bool:
        """True when the scan must pause for an explicit user choice."""
        return self.verdict is RobotsVerdict.UNAVAILABLE


def robots_url_for(page: AbsoluteHttpUrl) -> AbsoluteHttpUrl:
    """The robots.txt URL for a page's origin."""
    return AbsoluteHttpUrl(
        scheme=page.scheme, host=page.host, port=page.port, path=ROBOTS_PATH, query=""
    )


class RobotsGate:
    """Fetches and evaluates robots.txt for one origin."""

    __slots__ = ("_follower", "_agent_token")

    def __init__(self, follower: RedirectFollower, agent_token: str) -> None:
        self._follower = follower
        self._agent_token = agent_token

    def evaluate(self, page: AbsoluteHttpUrl, budget: ScanBudget) -> Result[RobotsDecision]:
        """Decide whether the page may be fetched. Never raises for a remote condition."""
        robots = robots_url_for(page)
        attempt = self._follower.fetch(SafeRequest(url=robots, kind=ResponseKind.ROBOTS), budget)
        if attempt.failure is not None:
            # A transport-level refusal is not a licence to crawl.
            return ok(
                RobotsDecision(
                    verdict=RobotsVerdict.UNAVAILABLE,
                    robots_url=str(robots.normalized),
                    detail=attempt.unwrap_failure().code.value,
                )
            )

        outcome = attempt.unwrap()
        response = outcome.response
        status = response.head.status
        lease = budget.open_response(ResponseKind.ROBOTS)
        body = response.read_body(lease)
        response.close()

        if CLIENT_ERROR_MINIMUM <= status <= CLIENT_ERROR_MAXIMUM:
            # No rules published, so nothing forbids the page.
            return ok(
                RobotsDecision(
                    verdict=RobotsVerdict.ALLOWED,
                    robots_url=str(robots.normalized),
                    status=status,
                    detail="no_rules_published",
                )
            )
        if not (SUCCESS_MINIMUM <= status <= SUCCESS_MAXIMUM) or body.failure is not None:
            return ok(
                RobotsDecision(
                    verdict=RobotsVerdict.UNAVAILABLE,
                    robots_url=str(robots.normalized),
                    status=status,
                    detail=(
                        body.unwrap_failure().code.value
                        if body.failure is not None
                        else "server_error"
                    ),
                )
            )

        return ok(self._apply_rules(robots, page, status, body.unwrap()))

    def _apply_rules(
        self,
        robots: AbsoluteHttpUrl,
        page: AbsoluteHttpUrl,
        status: int,
        raw: bytes,
    ) -> RobotsDecision:
        parser = RobotFileParser()
        parser.parse(raw.decode("utf-8", errors="replace").splitlines())
        allowed = parser.can_fetch(self._agent_token, str(page.normalized))
        return RobotsDecision(
            verdict=RobotsVerdict.ALLOWED if allowed else RobotsVerdict.DISALLOWED,
            robots_url=str(robots.normalized),
            status=status,
            matched_rule=None if allowed else f"Disallow matched for {self._agent_token}",
        )
