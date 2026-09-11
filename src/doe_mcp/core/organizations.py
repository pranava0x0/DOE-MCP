"""Organization identity and alias resolution (decision 0008-B).

Why this module exists at all: in this ecosystem an organization's name is
not a stable key. Between 2025 and 2026 the evidence base recorded NREL
becoming the National Laboratory of the Rockies with every `*.nrel.gov`
domain going dark and no redirects, EERE dissolving into CMEI, HFTO and BETO
merging into AFFO, IEDO renaming to ITO, FECM renaming to HGEO, LPO becoming
EDF, and the Grid Deployment Office being dismantled between two other
offices. A registry keyed on names would have broken nine times in a year.

So organizations get stable internal ids that never change, and every name
anyone has ever used — current or former, plus former domains and GitHub
orgs — resolves to one. Two consequences the tools depend on:

1. A caller who says "NREL" gets an answer about NLR, is told the name is
   historical, and gets an `alias_match` warning. Silently answering as if
   the old name were current would hide a rename that changes URLs.
2. A caller who says "NREL" and gets a *dead domain* gets `domain_migrated`
   instead, which is a different fact: the organization is fine, the URL
   they have is not.

The table lives in sources/organizations.yaml (CC0) so anyone can lift it.
"""
from __future__ import annotations

import enum
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrgKind(str, enum.Enum):
    national_lab = "national_lab"
    """One of the 17 DOE national laboratories."""
    program_office = "program_office"
    headquarters = "headquarters"
    power_marketing_administration = "power_marketing_administration"
    facility = "facility"
    external = "external"
    """Not DOE. Present because DOE-network data is sometimes best served by
    a non-DOE host (IAEA for evaluated nuclear data) or funded by another
    agency (NASA for the ORNL DAAC), and the provenance triple has to be
    able to name them."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Aliases(_Strict):
    """Required for any organization that has ever renamed. The measured base
    rate for that is high enough that the field is required for all of them
    and simply empty where nothing has changed."""

    former_names: list[str] = Field(default_factory=list)
    former_domains: list[str] = Field(default_factory=list)
    github_orgs: list[str] = Field(default_factory=list)
    abbreviations: list[str] = Field(default_factory=list)


class Organization(_Strict):
    id: str
    """Stable forever. Chosen to survive renames: `nlr` would have been
    `nrel` a year ago, which is exactly the mistake this field prevents, so
    ids are assigned once and never re-derived from the current name."""
    name: str
    kind: OrgKind
    parent: str | None = None
    domains: list[str] = Field(default_factory=list)
    aliases: Aliases = Field(default_factory=Aliases)
    renamed_on: str | None = None
    """Date the current name took effect, when the organization has been
    renamed. What makes "(formerly X)" strings prunable at a phase gate
    rather than immortal."""
    rename_date_unverified: bool = False
    """Set where an organization has former names whose rename date this
    project did not establish. Three laboratories are in this state, all with
    long-settled historical renames that predate the 2025-26 churn wave.
    Recording the gap beats inventing a date, and beats leaving `renamed_on`
    blank as though the rename had not happened."""
    note: str | None = None

    @field_validator("note", mode="after")
    @classmethod
    def _tidy(cls, v):
        # Folded YAML keeps hard-wrapped lines and a trailing newline; org
        # notes reach callers through registry.resolve_org.
        return " ".join(v.split()) if v is not None else None


class OrgMatch(_Strict):
    org: Organization
    basis: str
    """`id` | `name` | `abbreviation` | `former_name` | `domain` |
    `former_domain` | `github_org`."""
    matched_text: str
    historical: bool = False
    """True when the caller used a name or domain that is no longer current.
    Drives the alias_match / domain_migrated warnings."""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class OrganizationTable:
    def __init__(self, orgs: list[Organization]) -> None:
        self.orgs = {o.id: o for o in orgs}
        if len(self.orgs) != len(orgs):
            raise ValueError("duplicate organization ids")
        self._index: dict[str, list[tuple[Organization, str, bool]]] = {}
        for o in orgs:
            self._add(o.id, o, "id", False)
            self._add(o.name, o, "name", False)
            for a in o.aliases.abbreviations:
                self._add(a, o, "abbreviation", False)
            for a in o.aliases.former_names:
                self._add(a, o, "former_name", True)
            for d in o.domains:
                self._add(d, o, "domain", False)
            for d in o.aliases.former_domains:
                self._add(d, o, "former_domain", True)
            for g in o.aliases.github_orgs:
                self._add(g, o, "github_org", False)

    def _add(self, key: str, org: Organization, basis: str,
             historical: bool) -> None:
        self._index.setdefault(_norm(key), []).append((org, basis, historical))

    @classmethod
    def load(cls, path: Path) -> "OrganizationTable":
        doc = yaml.safe_load(path.read_text())
        return cls([Organization.model_validate(o)
                    for o in doc["organizations"]])

    def get(self, org_id: str) -> Organization | None:
        return self.orgs.get(org_id)

    def ids(self) -> set[str]:
        return set(self.orgs)

    def labs(self) -> list[Organization]:
        return sorted((o for o in self.orgs.values()
                       if o.kind == OrgKind.national_lab),
                      key=lambda o: o.id)

    def resolve(self, query: str) -> list[OrgMatch]:
        """Every organization the query could mean, best basis first.

        Returns a list rather than a single answer because the ambiguity is
        real: "Ames" is both a national laboratory and a city, and "SLAC"
        names a laboratory whose formal name contains the word "Stanford"
        without being Stanford. Callers surface candidates; they do not
        pick.
        """
        hits = self._index.get(_norm(query), [])
        if not hits:
            hits = self._prefix_scan(query)
        order = {"id": 0, "name": 1, "abbreviation": 2, "domain": 3,
                 "former_name": 4, "former_domain": 5, "github_org": 6}
        matches = [OrgMatch(org=o, basis=b, matched_text=query, historical=h)
                   for o, b, h in hits]
        matches.sort(key=lambda m: (order.get(m.basis, 9), m.org.id))
        # One organization matched by two routes is not ambiguity, and
        # reporting it as such would push a caller to choose between two
        # spellings of the same laboratory. "NREL" hits `nlr` as both a former
        # name and a former GitHub org; the best basis wins and the rest are
        # dropped. Genuine ambiguity — two DIFFERENT organizations — survives.
        best_per_org: dict[str, OrgMatch] = {}
        for match in matches:
            best_per_org.setdefault(match.org.id, match)
        return sorted(best_per_org.values(),
                      key=lambda m: (order.get(m.basis, 9), m.org.id))

    def _prefix_scan(self, query: str) -> list[tuple[Organization, str, bool]]:
        """Fall back to whole-word prefix matching over every indexed key.

        Deliberately narrow. A substring scan would make "Ames" match
        "James", and a fuzzy one would invent matches the caller never
        implied; either failure mode ends with a tool confidently answering
        about the wrong laboratory.
        """
        terms = _norm(query).split()
        if not terms:
            return []
        out: list[tuple[Organization, str, bool]] = []
        seen: set[tuple[str, str]] = set()
        for key, entries in self._index.items():
            words = key.split()
            if not all(any(w.startswith(t) for w in words) for t in terms):
                continue
            for org, basis, historical in entries:
                if (org.id, basis) in seen:
                    continue
                seen.add((org.id, basis))
                out.append((org, basis, historical))
        return out
