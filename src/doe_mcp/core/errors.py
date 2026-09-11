"""Typed error taxonomy.

Emptiness and partial coverage are NOT errors — they are coverage dimensions
(envelope.Coverage). These classes exist only for conditions that prevent a
valid answer. Every message is written for the model: what failed, what it
means in DOE-data terms, what to try next. No stack traces, hostnames, or
credentials in messages.
"""
from __future__ import annotations


class DoeMcpError(Exception):
    """Base for all typed errors. `code` is the wire-stable class name."""

    code = "DoeMcpError"

    def model_message(self) -> str:
        return f"{self.code}: {self}"


class SourceUnavailable(DoeMcpError):
    code = "SourceUnavailable"

    def __init__(self, msg: str, status: int | None = None) -> None:
        super().__init__(msg)
        self.status = status
        """The HTTP status behind this, when there was one.

        Carried because one publisher in this registry reports an empty
        result set as HTTP 404 with a body saying so, and an adapter has to
        be able to tell that apart from an outage without matching on the
        message text. Everything else leaves it None.
        """


class RateLimited(DoeMcpError):
    code = "RateLimited"

    def __init__(self, msg: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(msg)
        self.retry_after_seconds = retry_after_seconds


class SourceSchemaChanged(DoeMcpError):
    """The drift alarm. A source whose response shape stopped matching its
    recorded fixture has changed under us; answering from a half-understood
    payload is worse than saying so."""

    code = "SourceSchemaChanged"


class InvalidQuery(DoeMcpError):
    code = "InvalidQuery"


class AmbiguousEntity(DoeMcpError):
    """Raised only when ambiguity cannot be expressed as candidates-in-data.

    The normal path returns candidates in the envelope with
    requires_user_choice=true; this error is for malformed or contradictory
    input.
    """

    code = "AmbiguousEntity"


class SourceNotActivated(DoeMcpError):
    """The registry knows this source and it is not wired up.

    Distinct from "we have never heard of it": a `proposed` manifest is a
    recorded intention with a reason attached, and telling a caller that is
    a better answer than an empty result.
    """

    code = "SourceNotActivated"


class TermsRestricted(DoeMcpError):
    """The publisher's terms forbid what was asked.

    Covers the `outreach_pending` sources (GESDB, the LANL sequence
    databases) whose correct next step is an email, not a workaround.
    """

    code = "TermsRestricted"


class CredentialMissing(DoeMcpError):
    """A keyed source was reached without its key (decision 0012).

    The message names the credential and the command that sets it, never the
    key itself and never a suggestion to paste one into a client config.
    """

    code = "CredentialMissing"


class EgressRefused(DoeMcpError):
    """A request violated the egress policy.

    Not in the wire error list: egress refusals are internal policy failures
    that surface to callers as SourceUnavailable with a policy note, never as
    an invitation to relax the policy from the tool surface.
    """

    code = "EgressRefused"
