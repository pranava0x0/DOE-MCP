# Contributing to DOE-MCP

The most valuable contribution to this project is usually not code. It is a
**source manifest** — a description of a public DOE system, what it holds,
what its terms actually say, and what it does not cover.

## Adding a source

1. **Find out what is really there.** Probe it. Record what the response
   looks like, what the counts are, and on what date. A manifest whose facts
   came from a documentation page rather than a live call is a guess.
2. **Read the terms.** Not "it looks public" — the actual terms page. If the
   review cannot establish something, put that in `terms_gap`; it becomes a
   disclosure on every answer that cites the source, which is far better than
   a caveat only contributors read.
3. **Write the manifest** in `sources/<domain>/<id>.yaml`, where the
   directory is the manifest's `domain` and the domain is the server that
   serves or would serve it; the loader refuses a manifest anywhere else.
   Start from a neighbouring one. Every prose field is written for someone who will read
   it in a year without this context. `capabilities` stays empty until the
   source is active; list what it would serve under `planned_capabilities`,
   which is how `registry.search_sources` answers "does DOE-MCP cover X"
   with the manifest and its `blocked_reason`.
4. **Validate:** `doe-mcp sources validate`.

A manifest can land as `proposed` with `adapter: {type: none}` and never be
queried. That is a complete contribution: the registry's job is to know what
exists, and `lifecycle.blocked_reason` is where you say what stands in the
way. **"We know it exists and cannot wire it yet" is a registry state here,
not a lost note.**

## What this project will not do

None of these is negotiable, and a pull request doing any of them will be
declined regardless of how well it works.

1. **No circumvention.** Some publishers have built deliberately against
   automation — GESDB and the LANL sequence databases are the two in the
   registry today. They are described and never queried. If you think
   sanctioned access might exist, the contribution is an email and its
   answer, recorded in the repository.
2. **No claiming a 403 is a gate.** Five DOE hosts are confirmed dropping
   plain HTTP clients while serving browsers normally. That is
   `blocked_probe`, and it needs a real-browser check before anything
   user-facing describes the source as access-controlled.
3. **No write operations.** Not to E-Link, not anywhere. Read-only is
   structural: the adapters have no write verb to call.
4. **No hand-written fixtures.** Record them with
   `python tools/record_fixtures.py`. A hand-written fixture agrees with the
   code that consumes it by construction, which makes it useless as a check
   on either.

## Adding a tool

Read decision 0014 in
`design/architecture.md` first. The sizing bands — 8 to 12 tools in a default
profile, 20 anywhere — are enforced at runtime, and they are where measured
tool-selection accuracy falls off rather than a style preference. Adding a
twenty-first tool means merging two, not raising the ceiling.

A tool description is documentation for a model, and the most important
sentence in most of them is the one about what an empty result means.

## Running the tests

```bash
uv venv --python 3.12 && uv pip install -e ".[all]" --group dev
python -m pytest
ruff check .
```

Everything replays recorded responses; nothing in the suite reaches the
network. `python tools/record_fixtures.py` re-records against live endpoints
when a publisher's shape changes — and when one does, the right first move is
usually to fix the adapter rather than the fixture. Keyed sources record only
when their credential is configured; the recorder redacts the key before
writing and a test proves it with a sentinel.

`python tools/build_site.py --fixtures` regenerates the site, the README's
status table, and `docs/reference.md` from the registry and the assembled
servers. CI fails when any of them is stale, so run it after changing a
manifest or a tool.

## Prose

Manifest text, tool descriptions, and documentation are read by people
deciding whether to trust an answer. Write plainly, say what is not known,
and date anything that will age. Where a later finding corrects an earlier
one, add a dated note rather than silently rewriting it.

Run the writing checker before opening a pull request:

```bash
python3 tools/slopcheck.py --include-agent-docs .
```

A `FAIL` blocks a merge; a `WARN` is a judgment call. What it flags:
intensifiers ("genuinely", "truly", "robust", "seamless"), "not X but Y"
constructions, headings phrased as maxims, self-praise about rigor or
honesty, sentences that exist only to close a paragraph, and bold-opener
bullet cascades where a paragraph would do. Wall paragraphs get split at the
turn of thought, not trimmed to fit. Exempt only verbatim quotations and
proper-noun expansions, each with a reason in `.slopcheck.json`.
