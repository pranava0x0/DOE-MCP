"""EnvelopeBuilder: one uniform way for tools to assemble envelopes.

Keeps the disclosure rules in one place, so that a rule recorded in a
manifest becomes a disclosure at the point of use rather than a caveat only
a contributor ever reads:

- a publisher with no machine-readable update date auto-carries
  `freshness_unavailable`;
- a manifest with a `terms_gap` auto-carries `terms_note` quoting it;
- a source whose publisher requires citation auto-carries
  `citation_required` with the string to use;
- evidence must reference a registered source entry;
- coverage dimensions are explicit at build time, never defaulted into
  optimism.
"""
from __future__ import annotations

import uuid

from .envelope import (AccessPath, AccessRecipe, AuthorityLevel, Citation,
                       Coverage, Envelope, Evidence, ExecutionProvenance,
                       NextAction, PaginationCoverage, RawRecovery,
                       RecipeKind, RegistryCoverage, ResourceRef,
                       ResultCoverage, SourceEntry, SourceFailure,
                       SourceGap, WarningCode, WarningNote)


class EnvelopeBuilder:
    def __init__(self, *, server: str, server_version: str, tool: str,
                 contract_version: str, registry_revision: str,
                 adapters: dict[str, str]) -> None:
        self._execution = ExecutionProvenance(
            server=server, server_version=server_version, tool=tool,
            tool_contract_version=contract_version,
            adapters=adapters, registry_revision=registry_revision,
            request_id=uuid.uuid4().hex)
        self._sources: list[SourceEntry] = []
        self._evidence: list[Evidence] = []
        self._warnings: list[WarningNote] = []
        self._recipes: list[AccessRecipe] = []
        self._resources: list[ResourceRef] = []
        self._next: list[NextAction] = []
        self._versions_seen: dict[str, str] = {}

    def add_source(self, *, source_id: str, steward: str, system: str,
                   dataset: str, dataset_version: str,
                   authority_level: AuthorityLevel, access_path: AccessPath,
                   source_updated_at: str | None, retrieved_at: str,
                   cache_age_seconds: int, funder: str | None = None,
                   host: str | None = None,
                   citation: Citation | None = None,
                   warn_on_missing_freshness: bool = True,
                   terms_gap: str | None = None) -> str:
        ref = f"source_{len(self._sources) + 1:02d}"
        self._sources.append(SourceEntry(
            id=ref, source_id=source_id, steward=steward, funder=funder,
            host=host, system=system, dataset=dataset,
            dataset_version=dataset_version, authority_level=authority_level,
            access_path=access_path, source_updated_at=source_updated_at,
            retrieved_at=retrieved_at, cache_age_seconds=cache_age_seconds,
            citation=citation))
        if source_updated_at is None and warn_on_missing_freshness:
            self.warn(WarningCode.freshness_unavailable,
                      "The publisher exposes no machine-readable update date "
                      "for this source; retrieval time is known, data vintage "
                      "is not.", source_id)
        if terms_gap and not any(
                w.code == WarningCode.terms_note and w.source_id == source_id
                for w in self._warnings):
            self.warn(WarningCode.terms_note, terms_gap, source_id)
        if citation and citation.required and citation.recommended:
            self.warn(WarningCode.citation_required,
                      f"This publisher requires citation as a condition of "
                      f"use. Cite: {citation.recommended}", source_id)
        self._note_version(source_id, dataset, dataset_version)
        return ref

    def _note_version(self, source_id: str, dataset: str, version: str) -> None:
        """Vintage discipline (architecture Part 1 § 3.3). Two entries for the same dataset at
        different versions is the mixed-vintage join the ecosystem produces
        most often — ATB 2024 numbers next to ATB 2026 ones — and it is
        caught here rather than in every tool."""
        prior = self._versions_seen.get(dataset)
        if prior is not None and prior != version:
            self.warn(
                WarningCode.mixed_vintages,
                f"This answer combines {dataset} at two versions "
                f"({prior} and {version}). Values from different releases of "
                "the same dataset are not comparable; say which is which "
                "before using them together.", source_id)
        else:
            self._versions_seen[dataset] = version

    def add_evidence(self, *, source_ref: str, record_id: str,
                     retrieved_at: str, transformations: list[str],
                     payload_hash: str | None = None,
                     locator: str | None = None,
                     effective_at: str | None = None,
                     raw_recovery: RawRecovery = RawRecovery.available) -> str:
        if source_ref not in {s.id for s in self._sources}:
            raise ValueError(f"evidence references unknown source entry "
                             f"{source_ref!r}")
        ref = f"evidence_{len(self._evidence) + 1:02d}"
        self._evidence.append(Evidence(
            id=ref, source_ref=source_ref, record_id=record_id,
            locator=locator, retrieved_at=retrieved_at,
            effective_at=effective_at, transformations=transformations,
            payload_hash=payload_hash, raw_recovery=raw_recovery))
        return ref

    def add_recipe(self, kind: RecipeKind, uri: str, *,
                   label: str | None = None,
                   instructions: str | None = None,
                   requires_account: bool = False,
                   approximate_size: str | None = None) -> None:
        """A typed pointer to data this answer does not inline (DECISIONS
        0010). Deduped on (kind, uri) because several records in one result
        commonly share a landing page."""
        if any(r.kind == kind and r.uri == uri for r in self._recipes):
            return
        self._recipes.append(AccessRecipe(
            kind=kind, uri=uri, label=label, instructions=instructions,
            requires_account=requires_account,
            approximate_size=approximate_size))

    def add_resource(self, uri: str, media_type: str | None = None,
                     description: str | None = None) -> None:
        self._resources.append(ResourceRef(uri=uri, media_type=media_type,
                                           description=description))

    def warn(self, code: WarningCode, message: str,
             source_id: str | None = None) -> None:
        if any(w.code == code and w.message == message
               and w.source_id == source_id for w in self._warnings):
            return
        self._warnings.append(WarningNote(code=code, message=message,
                                          source_id=source_id))

    def next_action(self, finding: str, capability: str, reason: str) -> None:
        if len(self._next) >= 3:  # at most 3, inherited
            return
        self._next.append(NextAction(finding=finding,
                                     suggested_capability=capability,
                                     reason=reason))

    def build(self, data: dict, coverage: Coverage, *,
              requires_user_choice: bool = False) -> Envelope:
        return Envelope(data=data, provenance=self._sources,
                        evidence=self._evidence, coverage=coverage,
                        warnings=self._warnings,
                        access_recipes=self._recipes,
                        next_actions=self._next, resources=self._resources,
                        requires_user_choice=requires_user_choice,
                        execution=self._execution)


def gap(source_id: str, reason: str) -> SourceGap:
    return SourceGap(source_id=source_id, reason=reason)


def failure(source_id: str, error: str, detail: str) -> SourceFailure:
    return SourceFailure(source_id=source_id, error=error, detail=detail)


def result_dim(record_count: int) -> ResultCoverage:
    return ResultCoverage.hit if record_count > 0 else ResultCoverage.empty


def pagination_coverage(total: int | None, seen: int, *, offset: int = 0,
                        more: bool | None = None) -> PaginationCoverage:
    """The pagination dimension, from what a publisher said about its total.

    One computation for every tool that pages, so the third state cannot be
    lost again: `unknown` when the publisher sent no usable total and no
    flag, never `complete`. Before this helper the computation existed nine
    times, and five of them read a missing total as zero.

    `more` is a publisher's own has-more signal where one exists: OPTIMADE's
    `more_data_available`, a continuation cursor, a count the publisher says
    it capped. True settles the question as truncated; False settles it as
    complete only when no total contradicts it; None means the publisher
    offered no such signal.
    """
    if more is True:
        return PaginationCoverage.truncated
    if total is not None:
        return (PaginationCoverage.truncated if offset + seen < total
                else PaginationCoverage.complete)
    if more is False:
        return PaginationCoverage.complete
    return PaginationCoverage.unknown


def selection_coverage(sources, capability: str,
                       selected: list) -> tuple[RegistryCoverage,
                                                list[SourceGap]]:
    """The registry-coverage dimension and any source gaps for a capability.

    The distinction this function protects is the one the whole envelope is
    built around: `none` when the registry holds nothing for the capability,
    `partial` when it holds something that is not currently selectable. A
    caller reading `none` knows the data may exist and we cannot see it; a
    caller reading `partial` knows we can see it and could not reach it.
    """
    gaps = [gap(sid, reason)
            for sid, reason in sources.unavailable_for(capability)]
    if selected:
        return RegistryCoverage.covered, gaps
    if not gaps:
        return RegistryCoverage.none, []
    return RegistryCoverage.partial, gaps


def new_request_id() -> str:
    return uuid.uuid4().hex
