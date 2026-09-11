#!/usr/bin/env python3
"""slopcheck: find AI-slop writing in product copy, docs, code comments and commit text.

One checker consolidating the prose gates that grew independently across six
unrelated projects' private checkers, plus the structural patterns none of
them caught. The donors, anonymized, by what each contributed:

  Project A's check_writing.py             two-severity rule table, prose
                                           extraction, slogan heuristic,
                                           allow-with-reason
  Project B's check_writing.py             doc-tree scan, wall paragraphs,
                                           mega sentences, docstring scan
  Project C's check_language.py            plain-language MOVES: UI narration,
                                           meta-commentary, hedge stacks,
                                           em-dash piles
  Project C's check_register.py            headline SHAPES: comma-tail,
                                           colon-setup
  Project D's audit-prose.mjs              rendered-HTML sweep, block
                                           splitting, sentence stats
  Project E's register_report.py           comma-staple twins, epigrams,
                                           so-tails, motif words
  DESIGN.md 11.1 + 11.1.2                  the register list and the rhythm
                                           tells behind all of it

Three layers, because slop hides at three resolutions:

  1. PHRASES   a word list catches "delve" and "at your fingertips".
  2. SHAPES    a regex over short display strings catches the constructions a
               generated headline defaults to: "Published cost bands, sourced",
               "The gap: no vendor has signed", "The alternative,
               industrialized".
  3. RHYTHM    nothing greppable. Uniform sentence length (low burstiness),
               three sentences built on the same template, a run of staccato
               declaratives, a kicker closing every paragraph, bullets all the
               same length, one vivid word repeated across a corpus until it
               reads as a tic. These are computed, not matched.

Three severities, and the split is the design:

  FAIL   no legitimate use in authored prose. Gates.
  WARN   usually slop, sometimes load-bearing. A human decides. Gates only
         under --fail-on WARN.
  INFO   corpus statistics and rhythm measurements. Never gates, by design:
         over-mechanizing prose produces its own slop, and a long sentence is
         sometimes the right sentence.

Rules this checker follows, learned the hard way and enforced on itself:

  * It prints how much it actually examined, every run. A gate reporting
    "0 findings" over 12% of a corpus looks identical to one reporting it over
    all of it, and only the count tells them apart.
  * Scanning nothing is an error (exit 2), never a pass. Silent success is a
    bug class.
  * Every rule ships a known-bad fixture that must fire and a known-good one
    that must not. `--selftest` proves the checker can still fail; a checker
    tuned into silence produces the most reassuring possible output.
  * Every exemption carries a written reason, checked for substance. Widening
    an allowlist until the output is empty is not calibration.
  * Quoted spans are exempt because verbatim quotes are not ours to rewrite,
    and the count of exempted spans is printed, so quotes cannot silently
    become a smuggling route.

Usage:
  python3 tools/slopcheck.py docs/ README.md        # scan paths
  python3 tools/slopcheck.py --stats .              # add rhythm + corpus stats
  python3 tools/slopcheck.py --calibrate .          # first-run triage table
  python3 tools/slopcheck.py --selftest             # prove the rules still fire
  python3 tools/slopcheck.py --list                 # the rule table
  python3 tools/slopcheck.py --explain tricolon     # one rule, in full
  python3 tools/slopcheck.py --only tricolon,em-dash-pileup docs/
  python3 tools/slopcheck.py --changed origin/main..HEAD
  python3 tools/slopcheck.py --commits origin/main..HEAD
  python3 tools/slopcheck.py --pr 24
  git log -1 --format=%B | python3 tools/slopcheck.py --stdin
  python3 tools/slopcheck.py --fail-on WARN --json findings.json .

Config: an optional .slopcheck.json beside the scanned root.

  {
    "exclude": ["research/raw", "base-files"],
    "disable": {"register-drift": "the client's house voice is British English"},
    "promote": {"emoji-in-prose": "FAIL"},
    "demote":  {"citation-meta": "WARN"},
    "quote_keys": {"claim": "verbatim corporate PR quotes the tracker records, not our prose"},
    "allow": [{"phrase": "waste-heat utilisation",
               "reason": "a state regulator's term of art in its own code § 216.6(a); rewriting misquotes the rule"}],
    "thresholds": {"wall_paragraph_words": {"value": 200, "reason": "..."}}
  }

  A "disable" value IS the reason it is off, and an allowlist entry needs one
  too: both are rejected at load if the reason would not survive review.
  "promote"/"demote" take a level (FAIL/WARN/INFO), not a reason.

Exit codes: 0 clean, 1 findings at or above --fail-on, 2 nothing scanned or a
usage error.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import statistics
import subprocess
import sys
import tokenize
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.0"

LEVELS = ("FAIL", "WARN", "INFO")
LEVEL_RANK = {"FAIL": 3, "WARN": 2, "INFO": 1, "NEVER": 99}

# Scopes are registers, not file types. A PR body saying "extended the
# citation-liveness check" is engineering vocabulary; product copy saying
# "every claim is cited" is the tell. Rules declare where they apply.
SCOPE_COPY = "copy"    # user-visible product strings: HTML text, JS literals, JSON values
SCOPE_DOC = "doc"      # Markdown: specs, research, decision records, READMEs
SCOPE_CODE = "code"    # comments and docstrings
SCOPE_TEXT = "text"    # commit messages, PR bodies, stdin
ALL_SCOPES = (SCOPE_COPY, SCOPE_DOC, SCOPE_CODE, SCOPE_TEXT)

SUPPORTED_SUFFIXES = {".md", ".markdown", ".html", ".htm", ".js", ".mjs", ".cjs",
                      ".jsx", ".ts", ".tsx", ".json", ".py", ".txt", ".rst"}

DEFAULT_EXCLUDE_PARTS = {
    ".git", "node_modules", "dist", "build", ".next", "__pycache__", ".venv",
    "venv", "site-packages", "coverage", ".mypy_cache", ".pytest_cache",
    ".claude", "vendor", "third_party", ".cache", "out", ".turbo",
}

MAX_FILE_BYTES = 2_000_000       # past this it is a data blob, not prose

# Contributor notes, not shipped prose. These files quote banned phrases in
# order to record why they are banned, so scanning them reports the rule table
# as a violation of itself. --include-agent-docs opts back in.
AGENT_DOCS = {"claude.md", "agents.md", "design.md", "testing.md", "data.md",
              "security.md", "git.md", "ledger.md", "issues.md", "backlog.md",
              "refresh.md", "uat.md", "prompting.md", "skillslog.md",
              "contributing.md", "changelog.md", "todo.md", "notes.md"}
AGENT_DOC_DIRS = {"base-files", ".claude", "docs/agents"}

# Thresholds. Every one is overridable in .slopcheck.json, and every one is
# stated here rather than buried at its use site, because a number that gets
# hand-tuned in place is a solver waiting to be written.
DEFAULTS: Dict[str, float] = {
    "mega_sentence_words_doc": 70,      # calibrated on a 1,900-sentence doc corpus, p95 = 43
    "mega_sentence_words_copy": 32,     # rendered display copy: over 32 is usually two sentences
    "mega_sentence_words_data": 60,     # JSON/JS data prose. Measured 2026-08-29 over
                                        # 6,199 sentences: median 23, p95 51, p99 66.
                                        # 60 flags 1.8%; 45 flagged 9.6%, a distribution.
    "wall_paragraph_words": 120,        # doc corpus median 20 words, p95 79
    "shape_max_chars": 80,              # both headline shapes anchor at the end of a short string
    "min_display_chars": 45,            # below this a string is a label, and no move applies
    "hedge_distinct_min": 3,            # one hedge is honest, three is a shrug
    "burstiness_min_cv": 0.45,          # human sentence lengths cluster unevenly
    "burstiness_min_sentences": 15,     # below this the CV is noise
    "template_run_min": 3,              # three sentences on one template reads as generated
    "parataxis_run_min": 3,             # a single short sentence is a beat; a run is the tell
    "parataxis_max_words": 9,
    "kicker_max_words": 7,
    "kicker_min_paragraphs": 3,
    "uniform_bullets_min_items": 4,
    "uniform_bullets_max_cv": 0.15,
    "bold_lead_in_ratio": 0.6,          # over 60% of bullets is the model's default outline
    "heading_colon_ratio": 0.4,
    "motif_min_count": 6,
    "motif_min_files": 3,
    "motif_min_rate": 0.8,              # per 1,000 words
    "phrase_tic_min_count": 4,
    "phrase_tic_min_files": 2,
    "intensifier_per_1k": 6.0,
    "passive_per_1k": 25.0,
    "nominalization_per_1k": 12.0,
    "noun_cluster_min": 4,
    "corpus_min_words": 200,          # below this the corpus rates are noise
    "slogan_max_sentence": 110,
    "slogan_tail_chars": 45,
}


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Rule:
    id: str
    level: str
    family: str
    why: str
    fix: str
    pattern: Optional[str] = None
    scopes: Tuple[str, ...] = ALL_SCOPES
    slogan_only: bool = False
    origin: str = ""
    bad: Tuple[str, ...] = ()
    good: Tuple[str, ...] = ()
    # Computed rules have no pattern; the engine dispatches on id.
    computed: bool = False
    # Which extractor --selftest should feed this rule's fixtures through.
    # Empty derives it from the scopes: a shape rule needs a display string,
    # a structural rule needs real paragraphs. Running a fixture through the
    # wrong extractor is how a rule "passes" its own test while dead.
    fixture_as: str = ""

    def compiled(self) -> Optional["re.Pattern[str]"]:
        return re.compile(self.pattern, re.I) if self.pattern else None


# --- Layer 1: phrases ------------------------------------------------------
PHRASE_RULES: List[Rule] = [
    Rule(
        "llm-register", "FAIL", "register",
        "LLM register word with no legitimate use in authored prose",
        "name the specific thing: what it does, for whom, with what number",
        r"\b(delv(?:e|es|ed|ing)|tapestr(?:y|ies)|nestled|vibrant|"
        r"seamless(?:ly)?|ever[-\s]evolving|game[-\s]chang(?:er|ing)|"
        r"cutting[-\s]edge|(?:a|the) testament to|stands? as a testament|"
        r"unlock(?:ing|s)? the (?:power|potential)|navigat(?:e|es|ing) the complex\w*|"
        r"treasure trove|veritable|paradigm shift|synerg(?:y|ies|istic)|"
        r"holistic approach|supercharg(?:e|es|ed|ing)|revolutioniz(?:e|es|ed|ing)|"
        r"best[-\s]in[-\s]class|world[-\s]class|embark(?:ing)? on a journey|"
        r"in the (?:realm|world) of|bustling|a rich (?:tapestry|history) of)\b",
        origin="DESIGN.md 11.1; Project D's audit-prose.mjs; Project C's test_data.py",
        bad=("This dashboard offers a seamless way to explore the filings.",
             "The report stands as a testament to the agency's rigor.",
             "A rich tapestry of interconnected obligations."),
        good=("The dashboard lists 49 filings, filterable by docket and date.",
              "The seam between the two panels is a 1px rule.",
              "Example Township Solar Conversion, funded by a federal grant",
              "See https://example.gov/news/fact-sheet-agency-launches-supercharge-initiative "
              "for the announcement."),
    ),
    Rule(
        "llm-register-soft", "WARN", "register",
        "register word that is usually padding but occasionally load-bearing",
        "delete it and reread the sentence; if nothing was lost it was padding",
        r"\b(leverag(?:e|es|ed|ing)|robust(?:ly|ness)?|comprehensive(?:ly)?|"
        r"crucial(?:ly)?|pivotal|underscor(?:e|es|ed|ing)|elevat(?:e|es|ed|ing)|"
        r"empower(?:s|ed|ing)?|harness(?:es|ed|ing)? the|myriad|plethora|"
        r"multifaceted|intricate|meticulous(?:ly)?|profound(?:ly)?|"
        r"foster(?:s|ing)? (?:a|an|the)|transformative|realm|"
        r"plays? an? (?:crucial|key|vital|pivotal|important) role)\b",
        origin="DESIGN.md 11.1; 'realm' and 'not only' were demoted from FAIL in "
               "Project D because both have ordinary uses",
        bad=("The tool leverages a robust ingest pipeline.",
             "Onboarding plays a crucial role in the queue."),
        good=("The pipeline retries twice, then writes the row to the dead-letter file.",),
    ),
    Rule(
        "ai-opener", "FAIL", "filler",
        "chatbot connective tissue; the sentence starts by clearing its throat",
        "open on the point: cut the clause and start at the noun",
        r"\b(in today'?s (?:world|landscape|digital age|fast[-\s]paced|era|market)|"
        r"let'?s dive in|it'?s (?:important|worth) (?:to note|noting)|"
        r"it is (?:important|worth) (?:to note|noting)|when it comes to|"
        r"at the end of the day|in conclusion|needless to say|"
        r"without further ado|first and foremost|last but not least)\b",
        origin="Project A's ai-opener; Project B's twin",
        bad=("In today's fast-paced energy market, timing matters.",
             "It's worth noting that the deadline moved to March."),
        good=("The deadline moved to March 4 after the show-cause order.",),
    ),
    Rule(
        "marketing-vapor", "FAIL", "vapor",
        "marketing vapor; a claim with nothing behind it",
        "state what it does and the number that proves it",
        r"\b(at your fingertips|to the next level|the ultimate (?:solution|guide|tool)|"
        r"designed to help you|powerful insights|unleash|one[-\s]stop shop|"
        r"effortless(?:ly)?|get started in seconds|game plan for success|"
        r"take the guesswork out|enterprise[-\s]grade|battle[-\s]tested|"
        r"bulletproof|turnkey|frictionless|best[-\s]of[-\s]breed)\b",
        origin="DESIGN.md 11.1 marketing vapor; a CRM's test_design.py",
        bad=("Powerful insights at your fingertips.",
             "An enterprise-grade, turnkey compliance workflow."),
        good=("Exports every filing as CSV in under two seconds.",),
    ),
    Rule(
        "assistant-boilerplate", "FAIL", "mechanics",
        "assistant conversation furniture left inside shipped text",
        "delete it; it was addressed to the person who ran the prompt",
        r"(\bas an AI(?: language)? model\b|\bas an AI\b(?=\s*[,.:;!?]|$)|"
        r"\bI hope this helps\b|"
        r"\blet me know if you\b|\bfeel free to (?:reach out|ask|let me know)\b|"
        r"\b(?:great|excellent) question\b|\bhappy to help\b|"
        r"\bI'?d be happy to\b|\bhere'?s a (?:breakdown|rundown|quick overview)\b|"
        r"^\s*(?:certainly|absolutely|sure)[!,]|\bdive deeper into\b|"
        r"\bin this (?:article|post|guide),? (?:we|I)'?ll\b|"
        r"\bby the end of this (?:article|post|guide)\b)",
        origin="new here; this is the tell that survives a copy-paste out of a chat window",
        bad=("Great question! Here's a breakdown of the three tiers.",
             "As an AI language model, I cannot verify the filing date."),
        good=("The three tiers are set by capacity, not by revenue.",
              "The deal deepens West Texas as an AI-infrastructure hub.",
              "How to work in these repos as an AI agent"),
    ),
    Rule(
        "placeholder-leftover", "FAIL", "mechanics",
        "a template placeholder shipped as if it were content",
        "fill it in or cut the line",
        r"(\[your [a-z ]{2,20}\]|\[insert[^\]]{0,40}\]|\[company name\]|"
        r"\blorem ipsum\b|<placeholder>|\bTBD\b|\bXXX\b|\[[a-z]+ here\])",
        origin="new here; caught by reading, never by a test, until now",
        bad=("Contact [Your Name] for the raw data.",
             "Filing deadline: TBD"),
        good=("Contact the docket clerk for the raw data.",),
    ),
    Rule(
        "negation-slogan", "FAIL", "negation",
        "negative-parallelism slogan; the shape reads as generated whether or "
        "not the contrast is real",
        "define the thing by what it IS, in one declarative",
        r"\b(it'?s not just \w+|isn'?t just about|not just [\w\s]{1,20}(?:,? but|; it'?s)|"
        r"more than just (?:a|an|the)|not (?:a|an|the) \w+\.\s+Rather,|"
        r"it'?s not about [\w\s]{1,25}, it'?s about)\b",
        origin="DESIGN.md 11.1 contrast & negation family; Wikipedia 'Signs of AI writing'",
        bad=("It's not just a tracker, it's a decision tool.",
             "This isn't just about cost."),
        good=("The tracker ranks 49 dockets by filing date.",),
    ),
    Rule(
        "self-praise", "FAIL", "selfpraise",
        "the text announcing its own virtue instead of demonstrating it",
        "state the accurate thing; the reader decides whether it was honest",
        r"\b(hand[-\s]curated|rigorous(?:ly)? (?:designed|engineered|tested)|"
        r"we take \w+ seriously|the honest (?:version|answer|read|gap|truth)|"
        r"here'?s the thing|the real question is|"
        r"(?:named|naming|written|phrased|worded|titled) honestly|"
        r"honest(?:ly)? (?:named|written|titled)|"
        r"\bin all honesty\b|if (?:we|I)'?(?:re| am) being honest|"
        r"honest(?:ly)? about what it (?:actually |really )?(?:does|is))\b|"
        r"(?:^|[,;(]\s*)to be (?:fully |completely )?honest\s*[,.)]",
        origin="Project B's self-attributed-honesty (2026-08-28); Project A's self-praise",
        bad=("Here's the thing: the numbers are hand-curated.",
             "The schema is named honestly for what it does."),
        good=("The schema field is called filing_date because it holds the filing date.",),
    ),
    Rule(
        "achievement-report", "FAIL", "selfpraise",
        "status-report register aimed at no reader",
        "say what works, what does not, and what is left",
        r"\b(exit criteri(?:on|a)\b[^.]{0,80}?\b(?:is|are|was|were) met|"
        r"milestone (?:is |was |has been )?achieved|"
        r"successfully (?:completed|implemented|delivered|integrated|migrated)|"
        r"as (?:per|of) the adopted plan|we are pleased to announce|"
        r"delivering value|drives? (?:real )?(?:value|impact))\b",
        origin="Project B's achievement-report (2026-08-28)",
        bad=("The migration was successfully completed.",
             "The exit criterion for this stage is met."),
        good=("The migration moved 412 rows; 3 failed on a null docket id.",),
    ),
    Rule(
        "caption-register", "FAIL", "meta",
        "caption voice narrating a source instead of quoting or citing it",
        'use a short verbatim quote, or a plain attribution ("X found", "X said")',
        r"\b(underscor(?:es|ed|ing) (?:that|the (?:importance|need|urgency))|"
        r"highlight(?:s|ed|ing) the (?:importance|need|fact)|"
        r"makes? (?:it )?clear that|serves? as a reminder|speaks? volumes|"
        r"sheds? light on|paints? a picture of)\b",
        origin="DESIGN.md 11.1 caption-register phrasing",
        bad=("The order underscores the importance of timely onboarding.",),
        good=('The order says onboarding studies "shall be completed in 90 days".',),
    ),
    Rule(
        "invisible-unicode", "FAIL", "mechanics",
        "zero-width or exotic whitespace, the residue of a copy-paste",
        "normalize the text; these break greps, diffs and quote-locks",
        "[​‌‍‎‏⁠﻿­   ]",
        origin="a project's norm() in validate-summaries.mjs",
        bad=("The docket​ number is EL26-67.",),
        good=("The docket number is EL26-67.",),
    ),
    Rule(
        "emoji-in-prose", "WARN", "mechanics",
        "emoji in body copy; outline pills do the badge work",
        "cut it, or document the exception in the project's design.md",
        "[\U0001F300-\U0001FAFF\U0001F000-\U0001F2FF\U00002600-\U000026FF"
        "\u2728\u274C\u2705\u2757\u2764\u2049\u203C]",
        scopes=(SCOPE_COPY, SCOPE_DOC, SCOPE_TEXT),
        origin="DESIGN.md 11: no emojis by default. Promote to FAIL in .slopcheck.json "
               "for projects holding the house style.",
        bad=("Ready to ship \U0001F680", "The order \U0001F4E6 is ready"),
        good=("Ready to ship.",
              "Run data-refresh \u2192 REFRESH.md, then check the \u2713 column."),
    ),
    Rule(
        "hedge-reflex", "WARN", "hedge",
        "a hedge pair reached for by reflex",
        "state the claim, or state the specific uncertainty and its size",
        r"\b(might potentially|could possibly|may perhaps|somewhat unclear|"
        r"generally speaking|it could be argued|to some extent|"
        r"in most cases|more often than not|relatively speaking)\b",
        origin="Project A's and Project B's hedge-stack rules",
        bad=("The rule might potentially apply to concurrent load.",),
        good=("The rule applies to concurrent load above 20 units.",),
    ),
    Rule(
        "vague-attribution", "WARN", "hedge",
        "a claim attributed to nobody in particular",
        "name the source and link it, or drop the claim",
        r"\b((?:experts|observers|analysts|critics|many|some) "
        r"(?:say|says|agree|argue|argues|believe|cite|note)|"
        r"studies show|research suggests|it is (?:widely )?believed|"
        r"industry reports|reports indicate|is (?:widely )?considered)\b",
        origin="Project A's vague-attribution; Wikipedia 'Signs of AI writing'",
        bad=("Experts say the queue will clear by 2027.",),
        good=("The operator's 2025 queue report projects a clear queue by 2027.",),
    ),
    Rule(
        "throat-clearing", "WARN", "filler",
        "an opener that delays the point",
        "delete the first clause and start on the noun",
        r"(?:^|(?<=[.!?]\s))\s*(It'?s worth noting|It is worth noting|"
        r"Importantly|Crucially|Notably|Interestingly|Ultimately|"
        r"That said|With that in mind|Simply put|In essence|At its core|"
        r"Fundamentally|Broadly speaking)\b[,\s]",
        origin="Project C's check_language.py throat-clearing; DESIGN.md 11.1",
        bad=("Notably, the commission moved the date twice.",),
        good=("The commission moved the date twice.",),
    ),
    Rule(
        "stage-direction", "WARN", "meta",
        "a stage direction to the reader standing in for the point",
        "cut the device, keep the sentence after it",
        r"\b(consider this:|picture this:|imagine (?:this|for a moment):|"
        r"think of it (?:as|like) an?\b|let'?s break (?:it|this) down|"
        r"here'?s what nobody (?:talks about|tells you)|"
        r"but here'?s the (?:catch|kicker|twist))",
        origin="DESIGN.md 11.1 rhetorical crutches; Project B's stage-direction",
        bad=("Consider this: the deadline already passed.",),
        good=("The deadline passed on March 4.",),
    ),
    Rule(
        "rhetorical-question", "WARN", "meta",
        "a rhetorical question doing a transition's job",
        "state the answer as a declarative and delete the question",
        r"(\bBut what does (?:this|that) mean\b|\bSo what changed\?|"
        r"\bWhy does (?:this|it) matter\?|\bWhat'?s the catch\?|"
        r"\bThe (?:result|upshot|answer)\?|\bSound familiar\?|"
        r"\bWhat if I told you\b)",
        origin="DESIGN.md 11.1.2 setup/payoff constructions",
        bad=("So what changed? Everything.",),
        good=("Two things changed: the deadline and the filing fee.",),
    ),
    Rule(
        "hollow-summary", "WARN", "filler",
        "a recap sentence restating a point already made",
        "cut it; the reader just read the point",
        r"(?:^|(?<=[.!?]\s))\s*(In conclusion|Overall|To summarize|In summary|"
        r"All in all|To sum up|The bottom line is|In short|Put simply)\b[,:\s]",
        origin="DESIGN.md 11.1 hollow summaries",
        bad=("In summary, the filing was late.",),
        good=("The filing was late.",),
    ),
    Rule(
        "meta-commentary", "WARN", "meta",
        "a sentence about the previous sentence",
        "trust the sentence you already wrote",
        r"\b(is the useful part|is what matters|which is the point|"
        r"that'?s the difference|which is exactly why|and that'?s the key|"
        r"the useful thing here|that is worth knowing)\b",
        origin="Project C's check_language.py meta-commentary",
        bad=("The two agencies disagree, and that disagreement is the useful part.",),
        good=("The agency dates the order to March 4; the state regulator dates it to March 11.",),
    ),
    Rule(
        "paragraph-pinning", "WARN", "meta",
        "a paragraph naming its own function before making its point",
        "let the content carry the point; delete the announcement",
        r"\b(what this shows is|the key thing to understand|"
        r"it'?s important to understand|the takeaway here is|"
        r"what'?s interesting is|the point (?:here )?is that)\b",
        origin="DESIGN.md 11.1.2 paragraph pinning",
        bad=("What this shows is that the queue is the constraint.",),
        good=("The queue is the constraint: 411 projects, 14 engineers.",),
    ),
    Rule(
        "ui-narration", "WARN", "meta",
        "prose describing the interface instead of using it",
        "delete it; the chip, the badge and the link already say this on every row",
        r"\b(carr(?:y|ies|ying) (?:a |its )?cit\w*|links? to its source|"
        r"carry no citation|each (?:card|row|entry) carries|every figure links|"
        r"every claim links its source|proven by a contract)\b",
        scopes=(SCOPE_COPY, SCOPE_DOC),
        origin="Project C's check_language.py ui-narration; Project A's citation-boast",
        bad=("Each card carries a cited roadmap.",),
        good=("Roadmap dates come from the vendor's own filing.",),
    ),
    Rule(
        "citation-meta", "INFO", "meta",
        "product copy talking about citations rather than linking them",
        "add the link and delete the sentence",
        r"\b(cited|citations?|sourced throughout|fully referenced)\b",
        scopes=(SCOPE_COPY,),
        origin="Project A's citation-meta, where it is WARN over user-visible copy. "
               "INFO here because research data legitimately discusses citation; "
               'promote it in .slopcheck.json for a product-copy corpus.',
        bad=("Every figure on this page is cited.",),
        good=("Records portal doc REC-2025-00123, filed 2025-03-04.",),
    ),
    Rule(
        "empty-intensifier", "WARN", "register",
        "an adverb that adds no information",
        "cut the adverb and keep the fact",
        r"\b(quietly (?:withdrew|lapsed|dropped|shelved|killed)|genuinely|"
        r"truly|incredibly|remarkably|undoubtedly|effortlessly|"
        r"significantly (?:better|improved|enhanced)|"
        r"worth more than any press release)\b",
        origin="DESIGN.md 11.1 empty intensifiers (2026-07-17). That list also names "
               "really / actually / precisely, which are counted by "
               "intensifier-density instead of flagged per instance: measured "
               "2026-08-29, 97 uses across these docs and nearly every one marks an "
               "appearance-versus-reality contrast (\"confirm the code is actually "
               "safe\"), which is padding in display copy and the point in a rule.",
        bad=("The vendor quietly withdrew the application.",),
        good=("The vendor withdrew the application on 2025-06-11 without a press release.",),
    ),
    Rule(
        "comparative-superlative", "WARN", "vapor",
        "a claim leaning on the field instead of on the fact",
        "say what is true of the subject on its own",
        r"\b(the (?:industry'?s|world'?s|country'?s) (?:first|best|only|most|largest)|"
        r"the rare \w+ that|the first \w+ where|unlike any other|"
        r"second to none|unparalleled|unrivall?ed|most[-\s]copied)\b",
        origin="DESIGN.md 11.1 comparative superlatives (2026-07-17)",
        bad=("The industry's first joint-supply filing.",),
        good=("The first filing under Rule 4021 to pair two supply sources.",),
    ),
    Rule(
        "contrast-pair", "WARN", "negation",
        "a contrasting-pair couplet; the cadence is the tell, not the claim",
        "make the real claim in one clause",
        r"\b(simple yet powerful|powerful yet simple|fast but reliable|"
        r"small but mighty|(?:simple|fast|small|cheap|light|easy) "
        r"(?:yet|but) (?:powerful|capable|complete|thorough|rigorous))\b",
        origin="DESIGN.md 11.1 contrasting-pair coupling",
        bad=("A simple yet powerful screening tool.",),
        good=("Screens 3,000 parcels in nine seconds on a laptop.",),
    ),
    Rule(
        "corrective-negation", "WARN", "negation",
        "defining a thing by what it is not, in slogan position",
        "define by what it IS; keep only load-bearing legal caveats",
        r"(,\s+not (?:a|an|the|just)\b|\bnever an?\s+\w+|\bnot merely\b|"
        r"\brather than an?\s+\w+)",
        slogan_only=True,
        scopes=(SCOPE_COPY, SCOPE_TEXT),
        fixture_as="json",
        origin="Project A's negative-parallelism, slogan-position heuristic. Copy "
               "and commit text only: DESIGN.md 11.1 scopes the negation family to "
               "shipped words, and in an instruction the shape carries the content "
               '("Read the slice, not the file").',
        bad=("Screening evidence, not an agency determination.",),
        good=("This is screening evidence. Agencies make the determinations, "
              "and this tool never substitutes for one of them in any filing.",),
    ),
    Rule(
        "register-drift", "WARN", "register",
        "formal or British forms in copy read on a phone",
        "use the plain form: realize, use, while, to, before",
        r"\b(realis(?:e|ed|es|ing|ation)|utilis(?:e|ed|es|ing|ation)|utiliz(?:e|ed|es|ing|ation)|"
        r"whilst|amongst|endeavour(?:s|ed|ing)?|in order to|prior to the|"
        r"subsequent to|with regard to|in the event that)\b",
        origin="Project C's check_language.py register-drift. Disable it with a "
               "reason when the project's voice is genuinely British.",
        bad=("Whilst the docket is open, utilise the comment form.",),
        good=("While the docket is open, use the comment form.",),
    ),
    Rule(
        "nominalization", "WARN", "syntax",
        "a verb turned into a noun, forcing a weaker verb to carry the sentence",
        "use the verb: decide, investigate, explain, analyze",
        r"\b(?:mak(?:e|es|ing)|made|conduct(?:s|ing|ed)?|provid(?:e|es|ing)|"
        r"perform(?:s|ing|ed)?|undertak(?:e|es|ing)|carr(?:y|ies|ied) out|"
        r"giv(?:e|es|ing))\s+(?:a|an|the)\s+"
        # The -tion word has to be the HEAD of the object, so it must be
        # followed by punctuation, a preposition or the end of the clause.
        # "made a validation job pass" is an adjective doing modifier work.
        r"(?!(?:sentence|difference|experience|audience|science|evidence|"
        r"reference|preference|conference|sequence|instance|distance|"
        r"balance|entrance|maintenance)\b)"
        r"\w*(?:tion|ment|ance|ence|sis|ity)\b"
        r"(?=\s*(?:[.,;:)]|$|of\b|into\b|on\b|for\b|about\b|to\b))",
        origin="DESIGN.md 11.1 nominalization (2026-07-30)",
        bad=("The team will conduct an investigation of the outage.",),
        good=("The team will investigate the outage.",
              "That is what made the sentence feel verified.",
              "It would have made a validation job pass while validating nothing."),
    ),
    Rule(
        "section-shape-intro", "WARN", "shape",
        "a section intro describing its own layout instead of its point",
        "state the takeaway; the reader can count the cards",
        r"(?:^|(?<=[.!?]\s))\s*(Three ways to|Two moves\.|Here are (?:three|four|five)|"
        r"(?:Three|Four|Five|Six) (?:reasons|things|steps|ways)\b|"
        r"Let'?s explore the|In this section,? we)",
        origin="DESIGN.md 11.1.1 section intros carry the point",
        bad=("Three ways to read the company. One conclusion.",),
        good=("The moat is energy origination, and its durable form is a public ledger.",),
    ),
    Rule(
        "listicle-furniture", "WARN", "filler",
        "outline furniture the model adds by default",
        "delete the heading; the section already has one",
        r"\b(key takeaways|TL;?DR|in this article|table of contents:|"
        r"pros and cons:|final thoughts|wrapping up|closing thoughts)\b",
        origin="new here; the default shape of generated long-form",
        bad=("## Key Takeaways",),
        good=("## What the order changed",),
    ),
]

# --- Layer 2: shapes (short display strings and headings) -----------------
SHAPE_RULES: List[Rule] = [
    Rule(
        "comma-tail", "WARN", "shape",
        "a phrase with an adverb or participle stapled on after a comma",
        "make it one clause with a verb, or cut the tail",
        r",\s+(?:\w+ly\s+)?"
        r"(?:stated|sourced|said|put|noted|explained|argued|framed|quantified|"
        r"industrialized|detailed|verified|measured|precisely|honestly|"
        r"carefully|plainly|briefly|in detail|in brief)"
        r"(?:\s+\w+ly)?\s*[.!]?$",
        scopes=(SCOPE_COPY, SCOPE_DOC), fixture_as="json",
        origin="Project C's check_register.py comma-tail; trailing-adverb form added "
               "2026-08-29 after 'The problem, stated precisely' passed a clean run - the "
               "adverb slot was leading-only, so the commoner order went unmatched",
        bad=("Published cost bands, sourced",
             "The problem, stated precisely",
             "The tradeoffs, measured carefully"),
        good=("Published cost bands with a source on every row",),
    ),
    Rule(
        "hollow-actually", "WARN", "shape",
        "a heading using \"actually\" or \"really\" to manufacture a revelation",
        "cut the adverb and say the finding: the heading should state what is true, not "
        "imply everyone else got it wrong",
        r"^\W*(?:what|where|who|why|how|when)\b[^.?!]{0,70}?\b(?:actually|really)\b",
        scopes=(SCOPE_COPY, SCOPE_DOC), fixture_as="json",
        origin="user report 2026-08-29: 'where the XYZ actually is' shipped in a pitch doc "
               "and survived a 0-FAIL 0-WARN run; the adverb does no work in a heading and "
               "the shape is a model default",
        bad=("Where the demand actually is",
             "What is actually settled",
             "What I would actually build",
             "Where the money really goes"),
        good=("Who buys, and what they are escaping",
              "What Janus settled",
              "Megawatts that have actually reached construction"),
    ),
    Rule(
        "colon-setup", "WARN", "shape",
        "a label, a colon, then the sentence that was the actual point",
        "promote the sentence; delete the label",
        r"^[^:(\[]{4,60}:\s+\S",
        scopes=(SCOPE_COPY,), fixture_as="json",
        origin="Project C's check_register.py colon-setup: ten of thirty-two "
               "policy names ran this template",
        bad=("The gap: no vendor has signed a supply pairing",),
        good=("No vendor has signed a supply pairing",),
    ),
    Rule(
        "comma-staple", "WARN", "shape",
        "two ideas stapled with a comma where one sentence belongs",
        "cut the weaker fragment, or join them into one real clause",
        computed=True,
        scopes=(SCOPE_COPY,), fixture_as="json",
        origin="Project E's register_report.py is_twin (2026-07-19)",
        bad=("raw input to finished output, made on site",
             "the alternative, industrialized",
             "Familiar ground, familiar speed"),
        good=("a process that pays for itself",
              "J. Alvarez, for the client",
              "the benefits pass through, if the numbers are public",
              "Central Valley Research Station, Ohio",
              "Meridian Logistics Group, Inc.",
              "Example Village Council (local utility, program participant P59)"),
    ),
    Rule(
        "epigram", "INFO", "shape",
        'a short aphoristic "X is Y" line with no number in it',
        "keep at most one per surface; replace the rest with the fact",
        computed=True,
        scopes=(SCOPE_COPY,), fixture_as="json",
        origin="Project E's register_report.py is_epigram",
        bad=("The moat is the onboarding queue.",),
        good=("The queue holds 411 projects as of March 2025.",),
    ),
    Rule(
        "so-tail", "INFO", "shape",
        'a bullet ending ", so <outcome>"',
        "vary it; at most one per section",
        r",\s+so\s+[^.]{4,}\.?$",
        scopes=(SCOPE_COPY, SCOPE_DOC), fixture_as="json",
        origin="Project E's register_report.py SO_TAIL",
        bad=("The site already holds a substation, so onboarding is cheap.",),
        good=("The site holds a 230kV substation built in 1998.",),
    ),
]

# --- Layer 3: computed structure and rhythm --------------------------------
COMPUTED_RULES: List[Rule] = [
    Rule(
        "hedge-stack", "WARN", "hedge",
        "three or more distinct hedges in one sentence",
        "keep the one real uncertainty; delete the other two",
        computed=True,
        origin="Project C's check_language.py. May/might/could are excluded: in a "
               "rule they mean permitted, not hedged, and counting them turned a "
               "regulatory sentence into a false positive.",
        bad=("The estimate is arguably somewhat generally correct.",),
        good=("An emergency engine may run at most 100 hours, and those hours "
              "may not be used for peak shaving.",),
    ),
    Rule(
        "em-dash-pileup", "WARN", "mechanics",
        "more than two em dashes in one paragraph; an aside about an aside",
        "use commas, colons, periods or parentheses as the sentence needs",
        computed=True,
        origin="Project B's proximity regex; Project C's EM_DASH_MAX=2 over "
               "short display strings. Proximity, not a per-paragraph count: the "
               "count version scored a long enumerated bullet worse than a short "
               "aside-riddled one.",
        bad=("The order — filed late — moved the date — again.",),
        good=("The order, filed late, moved the date again.",),
    ),
    Rule(
        "mega-sentence", "WARN", "rhythm",
        "one sentence carrying more than one idea",
        "split it at the conjunction",
        computed=True,
        origin="Project B (70 words, docs) and Project D (32, display "
               "copy). Measured on the longest run between semicolons and (N) "
               "enumerators, not on the whole sentence: an author who supplied "
               "breakpoints already split the ideas.",
        bad=(("The commission issued the order on March 4 and then the utility "
               "responded on March 11 with a filing that asked for an extension "
               "of ninety days which the commission granted in part on April 2 "
               "while denying the request to consolidate the two dockets that "
               "had been pending since the previous autumn and remained open "
               "for comment until the end of the following quarter anyway, "
               "which left the intervenors with a schedule nobody had agreed "
               "to and a hearing date that moved twice more before the record "
               "closed at the end of the summer."),),
        good=("The commission issued the order on March 4. The utility responded a week later.",
              # An inline enumeration: long, but broken for the reader.
              ("The suite missed six of them: health that described the slice "
               "rather than the source; a sync that stamped success after failed "
               "writes; a route returning a status the UI never read; a rule that "
               "re-closed the issue; a badge that read green over a dead tick; and "
               "a timestamp written before the write landed.")),
    ),
    Rule(
        "wall-paragraph", "WARN", "rhythm",
        "a paragraph long enough that nobody reads it",
        "break it at the turn, or cut it",
        computed=True,
        origin="a project's repo: a 250-word README status paragraph passed every "
               "regex while being the worst prose in the repo",
        bad=(("word " * 130).strip(),),
        good=("The queue holds 411 projects.",),
    ),
    Rule(
        "low-burstiness", "INFO", "rhythm",
        "sentence lengths too even to be human",
        "let one sentence run long, then stop the next one short",
        computed=True,
        origin="DESIGN.md 11.1.2; low burstiness is the detection term",
        bad=(" ".join(["The team reviewed the filing today."] * 20),),
        good=("The team reviewed the filing. It ran to 340 pages, most of it "
              "exhibits, and the one paragraph that mattered sat on page 291 "
              "under a heading about service territory boundaries. Nobody had "
              "read it. That was the finding.",),
    ),
    Rule(
        "uniform-template", "INFO", "rhythm",
        "consecutive sentences built on one syntactic template",
        "change which clause leads, or let one sentence run longer",
        computed=True,
        origin="DESIGN.md 11.1.2 uniform sentence structure",
        bad=("The system tracks filings. The system ranks dockets. The system "
             "exports rows.",),
        good=("Filings arrive weekly. After the clerk stamps one, it lands in "
              "the docket index, where the ranker picks it up.",),
    ),
    Rule(
        "parataxis-run", "INFO", "rhythm",
        "a run of short declaratives with no connective tissue",
        "coordinate them when they are actually related",
        computed=True,
        origin="DESIGN.md 11.1.2 parataxis (the staccato stack)",
        bad=("This works. This scales. This lasts.",),
        good=("It works because it scales, and it has lasted through three rewrites.",),
    ),
    Rule(
        "kicker-cadence", "INFO", "rhythm",
        "a short aphoristic line closing paragraph after paragraph",
        "save the kicker for the one paragraph that earns it",
        computed=True,
        origin="DESIGN.md 11.1.2 landing sentences used as a reflex",
        bad=("The queue is long. The engineers are few. That is the difference.\n\n"
             "The utility filed late this spring. It did the same last autumn. "
             "It always does.\n\n"
             "The order moved the date. Then it moved again. Nobody noticed.",),
        good=("The queue holds 411 projects and the operator staffs 14 engineers against it.",),
    ),
    Rule(
        "tricolon", "WARN", "negation",
        "rule of three; three short items in a row for cadence",
        "keep the one that carries information",
        computed=True,
        origin="DESIGN.md 11.1 rule of three / tricolon",
        bad=("It is fast, simple, and powerful.",),
        good=("It runs in nine seconds on a laptop.",
              "Loading, error, and empty states on every view.",
              "Every dependency is a future bug, migration, and advisory.",
              "Separate facts, estimates, and judgments into labeled lanes."),
    ),
    Rule(
        "noun-cluster", "INFO", "syntax",
        "four or more nouns stacked as unbroken modifiers",
        "unpack with a preposition or a verb",
        computed=True,
        origin="DESIGN.md 11.1 stacked noun phrases; ASD-STE100 rule 7 (a personal site's ste.py)",
        bad=("a user engagement optimization framework",),
        good=("a framework for optimizing engagement",),
    ),
    Rule(
        "uniform-bullets", "INFO", "rhythm",
        "every bullet in a list the same length",
        "let the list be as uneven as the facts are",
        computed=True,
        scopes=(SCOPE_DOC,),
        origin="new here; generated lists are suspiciously even",
        bad=("- The first item runs to about eight words here\n"
             "- The second item runs to about eight words too\n"
             "- The third item runs to about eight words also\n"
             "- The fourth item runs to about eight words now",),
        good=("- Filed March 4\n"
              "- The utility asked for ninety days, got forty-five, and appealed "
              "the difference two weeks later\n"
              "- Denied\n"
              "- Reopened in June",),
    ),
    Rule(
        "bold-lead-in", "INFO", "rhythm",
        "most bullets opening with a bolded label",
        "use the shape where it earns its keep, not on every item",
        computed=True,
        scopes=(SCOPE_DOC,),
        origin="new here; the model's default outline format",
        bad=("- **Cost:** high\n- **Speed:** low\n- **Risk:** medium\n- **Scope:** wide",),
        good=("- Costs $4.2M\n- Takes 14 months\n- Two vendors have bid",),
    ),
    Rule(
        "heading-colon", "INFO", "shape",
        "most headings running the label-colon-point template",
        "promote the point into the heading",
        computed=True,
        scopes=(SCOPE_DOC,),
        origin="Project C's colon-setup, applied at document scale",
        bad=("## The gap: nobody signed\n\ntext\n\n## The fix: sign it\n\ntext\n\n"
             "## The risk: they will not\n\ntext",),
        good=("## Nobody signed\n\ntext\n\n## What signing would cost\n\ntext",
              "## 2026-08-12 (opener rebuild: usage undercount)\n\ntext\n\n"
              "## 2026-08-18 (daily sweep: a repair tool that deleted a row)\n\ntext\n\n"
              "## 2026-08-19 (dataset review: conflated positions)\n\ntext"),
    ),
    Rule(
        "paragraph-opening-repeat", "INFO", "rhythm",
        "consecutive paragraphs opening on the same word",
        "vary the entry point, or merge the paragraphs",
        computed=True,
        origin="new here; sibling of uniform-template at paragraph scale",
        bad=("This is the first paragraph of the section.\n\n"
             "This is the second paragraph of the section.\n\n"
             "This is the third paragraph of the section.",),
        good=("The order landed on March 4.\n\nBy June the utility had appealed.\n\n"
              "Nothing has moved since.",),
    ),
    Rule(
        "motif-word", "INFO", "corpus",
        "one vivid word repeated across the corpus until it reads as a tic",
        "vary it or cut it; keep only the literal uses",
        computed=True,
        origin='DESIGN.md 11.1 motif-word overuse: "receipts" ran 80+ times across ten decks',
        bad=(),   # corpus-level: exercised by the corpus fixture in --selftest
        good=(),
    ),
    Rule(
        "phrase-tic", "INFO", "corpus",
        "a three-word phrase repeated across files",
        "say it once, then find another way to say it",
        computed=True,
        origin="new here; the bigram/trigram sibling of motif-word",
        bad=(),
        good=(),
    ),
    Rule(
        "intensifier-density", "INFO", "corpus",
        "padding adverbs per thousand words",
        "cut them; the fact underneath does the work",
        computed=True,
        origin="DESIGN.md 11.1 empty intensifiers, measured rather than matched",
        bad=(),
        good=(),
    ),
    Rule(
        "passive-density", "INFO", "corpus",
        "passive constructions per thousand words",
        "name the actor: who did it",
        computed=True,
        origin="ASD-STE100 rule 4 via a personal site's tools/ste.py",
        bad=(),
        good=(),
    ),
]

RULES: List[Rule] = PHRASE_RULES + SHAPE_RULES + COMPUTED_RULES
RULES_BY_ID: Dict[str, Rule] = {r.id: r for r in RULES}
assert len(RULES_BY_ID) == len(RULES), "duplicate rule id"

# Hedges counted for hedge-stack. May/might/could are deliberately absent: in a
# permission-granting document "may" means allowed, not hedged.
HEDGES = ("perhaps", "arguably", "roughly", "somewhat", "relatively", "generally",
          "typically", "often", "possibly", "potentially", "seemingly", "broadly",
          "largely", "fairly", "quite", "presumably", "approximately", "apparently")

INTENSIFIERS = ("very", "really", "actually", "simply", "just", "truly", "precisely", "quite",
                "extremely", "incredibly", "highly", "genuinely", "literally",
                "absolutely", "completely", "totally", "utterly", "remarkably")

SUBORDINATORS = ("because", "although", "though", "while", "whilst", "since",
                 "unless", "until", "whereas", "if", "when", "after", "before",
                 "which", "who", "that", "so that", "even as")

FUNCTION_WORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "by", "at", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "it", "its", "this", "that", "these", "those", "they", "them", "their",
    "we", "our", "you", "your", "he", "she", "his", "her", "not", "no", "than",
    "then", "so", "if", "when", "while", "into", "over", "under", "after",
    "before", "between", "through", "during", "about", "against", "per", "via",
    "has", "have", "had", "will", "would", "can", "could", "may", "might",
    "must", "should", "do", "does", "did", "there", "here", "what", "which",
    "who", "whom", "whose", "how", "why", "all", "any", "both", "each", "more",
    "most", "other", "some", "such", "only", "own", "same", "too", "very",
    "one", "two", "three", "also", "up", "out", "off", "down", "now", "new",
}

STOPWORDS = FUNCTION_WORDS | {
    "said", "says", "make", "made", "get", "got", "use", "used", "uses", "using",
    "need", "needs", "want", "like", "see", "seen", "take", "taken", "give",
    "given", "know", "known", "think", "come", "came", "go", "goes", "went",
    "first", "second", "third", "last", "next", "still", "even", "many", "much",
    "well", "back", "way", "ways", "thing", "things", "time", "times", "year",
    "years", "day", "days", "work", "works", "worked", "part", "parts",
    "every", "never", "always", "often", "cannot", "must", "should", "would",
    "could", "might", "shall", "without", "against", "unless", "until",
    "already", "instead", "rather", "enough", "least", "every-", "don't",
    "doesn't", "isn't", "won't", "can't", "didn't", "wasn't", "aren't",
}

DET = {"the", "a", "an", "this", "that", "these", "those", "its", "their", "his", "her", "our", "your"}
PRO = {"it", "they", "we", "you", "he", "she", "i", "there", "one"}
CONJ = {"but", "and", "so", "yet", "or", "nor", "however", "still", "then"}
PREP = {"in", "on", "at", "for", "with", "by", "from", "of", "after", "before",
        "under", "over", "between", "during", "through", "against", "without",
        "within", "across", "beyond", "per", "via", "since", "until"}

ABBREV = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg",
          "ie", "al", "inc", "ltd", "co", "corp", "fig", "no", "vol", "approx",
          "dept", "est", "u.s", "e.g", "i.e", "p.m", "a.m", "cf", "ca", "ch",
          "sec", "art", "para", "pp", "ed", "eds", "rev", "min", "max", "avg"}


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")


def words_of(text: str) -> List[str]:
    return WORD_RE.findall(text)


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


CLAUSE_SPLIT = re.compile(r";\s+|\s+\(\d+\)\s+|\s+\u2014\s+")

# Three em dashes inside one window. The window is the rule: spread across a
# long passage they are punctuation, bunched together they are an aside about
# an aside.
EM_DASH_WINDOW = 110
EM_DASH_PILEUP = re.compile(
    "\u2014[^\u2014\n]{{0,{w}}}\u2014[^\u2014\n]{{0,{w}}}\u2014".format(w=EM_DASH_WINDOW))


def longest_clause(sentence: str) -> int:
    """Words in the longest run between author-supplied breakpoints."""
    return max((word_count(part) for part in CLAUSE_SPLIT.split(sentence)),
               default=0)


def split_sentences(text: str) -> List[str]:
    """Sentence split that survives abbreviations, decimals and docket numbers.

    A naive split on [.!?] turns "EL26-67-000 v. Acme Inc. filed Mar. 4" into
    four sentences and every length statistic computed from it is fiction.
    """
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    out: List[str] = []
    start = 0
    for m in re.finditer(r"[.!?]+[\"')\]”’]?(\s|$)", text):
        end = m.end()
        head = text[start:end].strip()
        if not head:
            continue
        # Decimal or version number: 3.5, v1.2.3
        dot = m.start()
        if text[dot] == "." and dot + 1 < len(text) and text[dot + 1].isdigit() \
                and dot > 0 and text[dot - 1].isdigit():
            continue
        tail = re.search(r"([A-Za-z][A-Za-z.]*)\.$", head)
        if tail:
            tok = tail.group(1).lower().rstrip(".")
            if tok in ABBREV or len(tok) == 1:
                continue
        out.append(head)
        start = end
    rest = text[start:].strip()
    if rest:
        out.append(rest)
    return out


def paragraphs_of(text: str) -> List[Tuple[int, str]]:
    """(starting line number, paragraph) for blank-line-separated blocks."""
    out: List[Tuple[int, str]] = []
    buf: List[str] = []
    start = 1
    for i, raw in enumerate(text.splitlines(), 1):
        if raw.strip():
            if not buf:
                start = i
            buf.append(raw)
        elif buf:
            out.append((start, "\n".join(buf)))
            buf = []
    if buf:
        out.append((start, "\n".join(buf)))
    return out


def blank_out(text: str, pattern: str) -> str:
    """Remove matches but keep newlines, so line numbers stay correct."""
    return re.sub(pattern, lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def cv(values: Sequence[float]) -> float:
    """Coefficient of variation. The burstiness measure."""
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    if mean == 0:
        return 0.0
    return statistics.pstdev(values) / mean


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    """One run of authored text, with where it came from.

    kind: line | string | heading | bullet | para
    Phrase rules run on every chunk; shape rules only on short string/heading
    chunks; structure rules only on para chunks. Mixing them is how a table of
    short cells gets measured as one 900-word sentence.
    """
    loc: int
    text: str
    kind: str
    scope: str
    origin: str = "text"      # md | html | json | js | py | text


@dataclass
class Extracted:
    chunks: List[Chunk] = field(default_factory=list)
    paragraphs: List[Chunk] = field(default_factory=list)
    quoted_exempt: int = 0
    raw_words: int = 0


URL_RE = re.compile(r"""(?:https?://|www\.)[^\s"'<>)\]]+|\b[\w.-]+\.(?:com|org|gov|net|io|edu)\b""", re.I)


def strip_urls(line: str) -> str:
    """Blank URLs before matching.

    A slug is not prose: docs/llms.txt reported "supercharge" as LLM register
    when the word sat inside a press-release URL. Markdown already drops
    link targets; every other format needs this.
    """
    return URL_RE.sub(" ", line)


QUOTE_CHARS = '"“”'
MENTION_MAX = 40


def _exempt_quotes(line: str, counter: List[int]) -> str:
    """Blank quoted spans: verbatim quotations, and mentioned terms.

    Quotes are the source's wording, not ours to rewrite. The count is
    reported so this cannot quietly become a way to smuggle authored prose
    past the checker.

    Two things are exempt, for two different reasons:

      a quotation   12+ characters. Somebody else's sentence.
      a mention     shorter, with no sentence punctuation. A word being named
                    rather than used: DESIGN.md writes register drift into the
                    formal ("realised", "whilst") and LEDGER.md lists "unlock",
                    "harness", "leverage" as words to catch. Reporting those as
                    register findings is the checker flagging its own rule book.

    Quotes are paired left to right rather than matched by a regex. The regex
    could open on a CLOSING quote and run to the next opening one: on the line
    `"✨ New", "Beta" pill ceremony; vague benefit-copy ("Powerful insights at
    your fingertips")` it blanked ` pill ceremony; vague benefit-copy (` and
    left the actual quotation exposed, so DESIGN.md's don't-write-this column
    was reported as marketing vapor.
    """
    positions = [i for i, ch in enumerate(line) if ch in QUOTE_CHARS]
    if len(positions) < 2:
        return line
    out = list(line)
    for start, end in zip(positions[0::2], positions[1::2]):
        inner = line[start + 1:end]
        quotation = len(inner) >= 12
        mention = len(inner) <= MENTION_MAX and not re.search(r"[.!?]", inner)
        if not (quotation or mention):
            continue
        counter[0] += 1
        for i in range(start, end + 1):
            out[i] = " "
    return "".join(out)


# A run of short terms inside an emphasis span is a list of words being named,
# not a sentence: `*delve / leverage / seamless / robust*` in AGENTS.md is the
# tells-to-avoid list, and linting it reports the rule as a violation of
# itself. Requires three or more items so an emphasised phrase with a comma in
# it is untouched.
MENTION_LIST = re.compile(r"(?<!\w)(\*{1,2}|_)([^*_\n]{6,300}?)\1(?!\w)")


def _exempt_mention_lists(line: str, counter: List[int]) -> str:
    def sub(m: "re.Match[str]") -> str:
        inner = m.group(2)
        items = [x.strip() for x in re.split(r"\s*[,/]\s+|\s+/\s+", inner) if x.strip()]
        if len(items) < 3 or re.search(r"[.!?]", inner):
            return m.group(0)
        if any(len(x) > 40 for x in items):
            return m.group(0)
        counter[0] += 1
        return " " * len(m.group(0))
    return MENTION_LIST.sub(sub, line)


def extract_md(text: str) -> Extracted:
    out = Extracted(raw_words=word_count(text))
    body = blank_out(text, r"```.*?```")
    body = blank_out(body, r"~~~.*?~~~")
    counter = [0]
    frontmatter_end = 0
    fm = re.match(r"---\n.*?\n---\n", body, re.S)
    if fm:
        frontmatter_end = body[:fm.end()].count("\n")
    for i, raw in enumerate(body.splitlines(), 1):
        line = re.sub(r"`[^`]*`", " ", raw)
        line = re.sub(r"\]\([^)]*\)", "] ", line)
        line = re.sub(r"<[^>]*>", " ", line)
        line = _exempt_mention_lists(_exempt_quotes(strip_urls(line), counter), counter)
        if not line.strip():
            continue
        head = line.lstrip()
        if head.startswith("#"):
            kind = "heading"
            line = head.lstrip("#").strip()
        elif re.match(r"^[-*+]\s", head) or re.match(r"^\d+[.)]\s", head):
            kind = "bullet"
        else:
            kind = "line"
        out.chunks.append(Chunk(i, line, kind, SCOPE_DOC, "md"))
    out.quoted_exempt = counter[0]
    for start, block in paragraphs_of(body):
        if start <= frontmatter_end:
            continue
        head = block.lstrip()
        if head[:1] in {"#", "|", ">", "<"}:
            continue
        # A markdown list with no blank lines between items is ONE block, and
        # treating it as one paragraph is the worst modelling error this
        # extractor had: a 20-item, 1,312-word list in AGENTS.md scored as a
        # single paragraph with 13 em dashes and 102-word "sentences" (items
        # carry no terminal punctuation, so they ran together), and every
        # finding in it reported the list's first line. Split on item markers,
        # keep each item's own line number, and let any prose above the first
        # marker stay a paragraph of its own.
        for item_start, item_lines, kind in _split_list_block(start, block):
            clean = re.sub(r"`[^`]*`", " ", "\n".join(item_lines))
            clean = re.sub(r"\]\([^)]*\)", "] ", clean)
            clean = strip_urls(re.sub(r"<[^>]*>", " ", clean))
            if clean.strip():
                out.paragraphs.append(Chunk(item_start, clean, kind, SCOPE_DOC, "md"))
    return out


LIST_MARKER = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)")


def _split_list_block(start: int, block: str) -> List[Tuple[int, List[str], str]]:
    """Split a markdown block into its list items plus any leading prose.

    Returns (line number, lines, kind) where kind is "para" for prose above the
    first marker and "bullet" for each item. Sentence-level rules apply to
    both; the paragraph-shaped ones (wall, kicker, opening repeat) are guarded
    on kind == "para", because a list's length carries information and wrapping
    one is not the writer's choice.
    """
    out: List[Tuple[int, List[str], str]] = []
    lead: List[str] = []
    cur: List[str] = []
    cur_start = start
    for offset, raw_line in enumerate(block.splitlines()):
        if LIST_MARKER.match(raw_line):
            if cur:
                out.append((cur_start, cur, "bullet"))
            cur, cur_start = [raw_line], start + offset
        elif cur:
            cur.append(raw_line)          # a continuation line of the item
        else:
            lead.append(raw_line)
    if cur:
        out.append((cur_start, cur, "bullet"))
    if lead:
        out.insert(0, (start, lead, "para"))
    return out


BLOCK_CLOSE = re.compile(
    r"</(p|li|h[1-6]|div|section|article|td|th|dd|dt|figcaption|blockquote|"
    r"caption|summary|details|option|label|span|small|strong|em|b|i|a)>", re.I)


def extract_html(text: str) -> Extracted:
    out = Extracted(raw_words=word_count(text))
    body = blank_out(text, r"<!--.*?-->")
    body = blank_out(body, r"<script\b.*?</script>")
    body = blank_out(body, r"<style\b.*?</style>")
    body = blank_out(body, r"<head\b.*?</head>")
    counter = [0]
    for i, raw in enumerate(body.splitlines(), 1):
        heading = bool(re.search(r"<h[1-6][ >]", raw, re.I))
        line = re.sub(r"<[^>]*>", " ", raw)
        line = re.sub(r"&[a-z]+;|&#x?[0-9a-f]+;", " ", line, flags=re.I)
        line = _exempt_quotes(strip_urls(re.sub(r"\s+", " ", line)).strip(), counter)
        if not line.strip():
            continue
        out.chunks.append(Chunk(i, line, "heading" if heading else "line", SCOPE_COPY, "html"))
    out.quoted_exempt = counter[0]
    # Structure runs on block-joined text. Without breaking on block elements
    # first, table cells and list items run together into one unpunctuated blob
    # and every page reads as a single 900-word sentence.
    flat = BLOCK_CLOSE.sub("\n", body)
    flat = re.sub(r"<(br|hr)\s*/?>", "\n", flat, flags=re.I)
    flat = re.sub(r"<[^>]*>", " ", flat)
    flat = re.sub(r"&[a-z]+;|&#x?[0-9a-f]+;", " ", flat, flags=re.I)
    line_no = 1
    for block in flat.split("\n"):
        block = strip_urls(re.sub(r"[ \t]+", " ", block)).strip()
        line_no += 1
        if word_count(block) >= 12:
            out.paragraphs.append(Chunk(line_no, block, "para", SCOPE_COPY, "html"))
    return out


# The template-literal branch may span lines, and that is what made it
# dangerous: with only "not a backtick" bounding it, the engine could open on
# the CLOSING backtick of one literal and run to the next backtick anywhere in
# the file. src/App.tsx line 20 yielded a 267-word "sentence" that was the body
# of a React component, and every sentence length, burstiness figure and noun
# cluster computed from it was fiction. Interpolation is now barred too:
# `${...}` marks a builder, not display copy.
JS_STRING = re.compile(
    r"""(?<![\w$])(?:'((?:[^'\\\n]|\\.){12,})'|"((?:[^"\\\n]|\\.){12,})"|"""
    r"""`((?:[^`\\$\n]|\\.|\$(?!\{)){12,})`)""",
    re.S)
CODEY = re.compile(r"^[\w./#:@$&?=%~-]*$|^https?://|\{\{|^\s*[<{[]|;\s*$")

# A second line of defence, independent of the quoting rules: a string carrying
# code punctuation is not prose, however it was delimited.
CODE_SHAPE = re.compile(
    r"=>|\)\s*\{|\}\s*;|===|!==|\|\||&&|"
    r"\b(?:function|const|let|var|return|import|export|typeof|await|async)\b"
    r"[^.!?]{0,40}[({=]")


def looks_like_code(s: str) -> bool:
    """True when an extracted string is source, not prose."""
    return bool(CODE_SHAPE.search(s))


def _unescape_js(raw: str) -> str:
    r"""Resolve \uXXXX escapes without touching literal UTF-8.

    The obvious raw.encode().decode("unicode_escape") round-trips through
    latin-1 and mangles any character already written literally in the source:
    "100°C–200°C" came back mojibaked, which silently broke the
    em-dash count for every string in the file.
    """
    out = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), raw)
    return out.replace('\\"', '"').replace("\\'", "'").replace("\\n", " ").replace("\\\\", "\\")


def extract_js(text: str, scan_code: bool) -> Extracted:
    out = Extracted(raw_words=word_count(text))
    counter = [0]
    # Comments first, from the raw text, then blanked so their contents cannot
    # be re-read as string literals.
    if scan_code:
        for m in re.finditer(r"/\*.*?\*/|//[^\n]*", text, re.S):
            loc = text[:m.start()].count("\n") + 1
            body = re.sub(r"^\s*[/*]+|[*/]+\s*$", " ", m.group(0), flags=re.M)
            body = re.sub(r"\s+", " ", body).strip()
            if len(body) >= 12:
                out.chunks.append(Chunk(loc, body, "line", SCOPE_CODE, "js"))
    src = blank_out(text, r"/\*.*?\*/")
    src = re.sub(r"//[^\n]*", "", src)
    for m in JS_STRING.finditer(src):
        raw = next(g for g in m.groups() if g is not None)
        loc = src[:m.start()].count("\n") + 1
        s = _unescape_js(raw)
        s = re.sub(r"<[^>]*>", " ", s)              # JS strings carry markup
        s = re.sub(r"\s+", " ", s).strip()
        if " " not in s or CODEY.match(s) or looks_like_code(s):
            continue
        s = _exempt_quotes(strip_urls(s), counter)
        if len(s.strip()) < 12:
            continue
        out.chunks.append(Chunk(loc, s, "string", SCOPE_COPY, "js"))
        if word_count(s) >= 12:
            out.paragraphs.append(Chunk(loc, s, "para", SCOPE_COPY, "js"))
    out.quoted_exempt = counter[0]
    return out


JSON_SKIP_KEYS = {"url", "source_url", "href", "src", "id", "slug", "type",
                  "kind", "class", "className", "date", "created_at", "path",
                  "updated_at", "verified_at", "generated_at", "quote",
                  "$schema", "version", "sha", "hash", "email", "color"}

# Whole subtrees of external text. Source titles, publisher names and verbatim
# quotes are not ours to rewrite, and shape rules judged against them are
# judging somebody else's headline: "U.S. EIA - CBECS 2018: Health care
# buildings" is a citation, not a colon-setup.
JSON_SKIP_CONTAINERS = {"sources", "source", "citations", "references", "refs",
                        "_meta", "meta", "links", "quotes", "attribution",
                        "excerpt", "verbatim", "testimony", "quotation",
                        "statement", "statements"}

# Source metadata by naming convention rather than by exhaustive list, so a
# project inventing `source_title` or `publisher_name` is covered on the day it
# adds it. Calibrated 2026-08-29: `source_title` carried 3 of 10 FAIL findings
# on a corporate-claims corpus, all of them the publisher's own headline.
JSON_SKIP_KEY_RE = re.compile(r"^(?:source|publisher|author|citation)_|"
                              r"_(?:url|title|name|source|href|id|date)$", re.I)

# Keys whose value renders as a heading. "label" is deliberately absent: it is
# shared with timeline milestones, where "ANPI goal: one reactor operating" is
# the natural form and not a headline reveal.
JSON_HEADING_KEYS = {"title", "name", "heading", "scenario", "alternative",
                     "sector", "target"}


def extract_json(text: str, quote_keys: Iterable[str] = ()) -> Extracted:
    out = Extracted(raw_words=word_count(text))
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return out
    counter = [0]
    extra_skip = set(quote_keys)

    def walk(node: Any, path: str, key: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in JSON_SKIP_KEYS or k in JSON_SKIP_CONTAINERS \
                        or k in extra_skip or k.startswith("_") \
                        or JSON_SKIP_KEY_RE.search(k):
                    continue
                walk(v, "{}.{}".format(path, k), k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "{}[{}]".format(path, i), key)
        elif isinstance(node, str) and len(node) > 12 and " " in node:
            s = _exempt_quotes(strip_urls(re.sub(r"\s+", " ", node)).strip(), counter)
            if not s.strip():
                return
            kind = "heading" if key in JSON_HEADING_KEYS else "string"
            out.chunks.append(Chunk(0, s, kind, SCOPE_COPY, "json"))
            if word_count(s) >= 12:
                out.paragraphs.append(Chunk(0, s, "para", SCOPE_COPY, "json"))

    walk(doc, "", "")
    out.quoted_exempt = counter[0]
    return out


def extract_py(text: str, scan_code: bool) -> Extracted:
    """Comments and docstrings. Code itself is not scanned: a variable named
    robust_parser is a naming question, not a register question."""
    out = Extracted(raw_words=word_count(text))
    if not scan_code:
        return out
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                body = tok.string.lstrip("#").strip()
                if len(body) >= 12:
                    out.chunks.append(Chunk(tok.start[0], body, "line", SCOPE_CODE, "py"))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        pass
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node, clean=True)
        if not doc:
            continue
        base = getattr(node, "lineno", 1)
        for offset, line in enumerate(doc.splitlines()):
            if line.strip():
                out.chunks.append(Chunk(base + offset, line.strip(), "line", SCOPE_CODE, "py"))
        for start, block in paragraphs_of(doc):
            out.paragraphs.append(Chunk(base + start, block, "para", SCOPE_CODE, "py"))
    return out


def extract_text(text: str, scope: str = SCOPE_TEXT) -> Extracted:
    out = Extracted(raw_words=word_count(text))
    counter = [0]
    for i, raw in enumerate(text.splitlines(), 1):
        line = _exempt_quotes(strip_urls(raw), counter)
        if line.strip():
            out.chunks.append(Chunk(i, line, "line", scope, "text"))
    out.quoted_exempt = counter[0]
    for start, block in paragraphs_of(text):
        out.paragraphs.append(Chunk(start, block, "para", scope, "text"))
    return out


def extract(path: Path, text: str, scan_code: bool,
            quote_keys: Iterable[str] = ()) -> Extracted:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".rst"}:
        return extract_md(text)
    if suffix in {".html", ".htm"}:
        return extract_html(text)
    if suffix in {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"}:
        return extract_js(text, scan_code)
    if suffix == ".json":
        return extract_json(text, quote_keys)
    if suffix == ".py":
        return extract_py(text, scan_code)
    return extract_text(text, SCOPE_DOC)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
@dataclass
class Hit:
    rule: Rule
    level: str
    source: str
    line: int
    matched: str
    context: str

    def render(self, width: int = 150) -> str:
        loc = "{}:{}".format(self.source, self.line) if self.line else self.source
        return ("[{:4}] {:24} {:44} {!r}\n         {} → {}\n         … {}"
                .format(self.level, self.rule.id, loc[-44:], self.matched[:60],
                        self.rule.why, self.rule.fix,
                        re.sub(r"\s+", " ", self.context).strip()[:width]))

    def to_dict(self) -> Dict[str, Any]:
        return {"rule": self.rule.id, "level": self.level, "family": self.rule.family,
                "source": self.source, "line": self.line, "matched": self.matched,
                "why": self.rule.why, "fix": self.rule.fix,
                "context": re.sub(r"\s+", " ", self.context).strip()[:300]}

    def key(self) -> str:
        return "{}|{}|{}|{}".format(self.rule.id, self.source, self.line,
                                    self.matched.strip().lower())


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BAD_REASONS = {"", "todo", "n/a", "na", "-", "because", "reason", "fixme", "later"}


def _check_reason(what: str, reason: str) -> str:
    r = (reason or "").strip()
    if r.lower() in BAD_REASONS or len(r) < 15:
        raise SystemExit(
            "config: {} needs a real reason (>=15 chars), got {!r}.\n"
            "An unexplained exemption is how a ban quietly stops meaning anything."
            .format(what, reason))
    return r


@dataclass
class Config:
    exclude: Tuple[str, ...] = ()
    quote_keys: Dict[str, str] = field(default_factory=dict)
    disable: Dict[str, str] = field(default_factory=dict)
    promote: Dict[str, str] = field(default_factory=dict)
    demote: Dict[str, str] = field(default_factory=dict)
    allow: Tuple[Tuple[str, str], ...] = ()
    thresholds: Dict[str, float] = field(default_factory=dict)
    threshold_reasons: Dict[str, str] = field(default_factory=dict)
    path: Optional[Path] = None

    def threshold(self, name: str) -> float:
        return float(self.thresholds.get(name, DEFAULTS[name]))

    def level_of(self, rule: Rule) -> str:
        if rule.id in self.promote:
            return self.promote[rule.id]
        if rule.id in self.demote:
            return self.demote[rule.id]
        return rule.level


def load_config(root: Path) -> Config:
    path = root / ".slopcheck.json" if root.is_dir() else root.parent / ".slopcheck.json"
    if not path.exists():
        return Config()
    raw = json.loads(path.read_text())
    disable = {}
    for rid, reason in (raw.get("disable") or {}).items():
        if rid not in RULES_BY_ID:
            raise SystemExit("config: unknown rule id in disable: {}".format(rid))
        disable[rid] = _check_reason("disable[{}]".format(rid), reason)
    promote, demote = {}, {}
    for field_name, target in (("promote", promote), ("demote", demote)):
        for rid, level in (raw.get(field_name) or {}).items():
            if rid not in RULES_BY_ID:
                raise SystemExit("config: unknown rule id in {}: {}".format(field_name, rid))
            if level not in LEVELS:
                raise SystemExit("config: {}[{}] must be one of {}".format(field_name, rid, LEVELS))
            target[rid] = level
    quote_keys = {}
    for key, reason in (raw.get("quote_keys") or {}).items():
        quote_keys[key] = _check_reason("quote_keys[{}]".format(key), reason)
    allow = []
    for entry in raw.get("allow") or []:
        phrase = (entry.get("phrase") or "").strip()
        if not phrase:
            raise SystemExit("config: an allow entry has no phrase")
        allow.append((phrase, _check_reason("allow[{!r}]".format(phrase), entry.get("reason", ""))))
    thresholds, threshold_reasons = {}, {}
    for name, entry in (raw.get("thresholds") or {}).items():
        if name not in DEFAULTS:
            raise SystemExit("config: unknown threshold {!r}; known: {}"
                             .format(name, ", ".join(sorted(DEFAULTS))))
        if not isinstance(entry, dict):
            raise SystemExit(
                "config: thresholds[{}] must be {{\"value\": N, \"reason\": \"...\"}}.\n"
                "Moving a threshold is an exemption; record the measurement that "
                "justifies it.".format(name))
        thresholds[name] = float(entry.get("value"))
        threshold_reasons[name] = _check_reason(
            "thresholds[{}]".format(name), entry.get("reason", ""))
    return Config(tuple(raw.get("exclude") or ()), quote_keys, disable, promote,
                  demote, tuple(allow), thresholds, threshold_reasons, path)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------
class Checker:
    def __init__(self, cfg: Config, only: Optional[Sequence[str]] = None,
                 scan_code: bool = False) -> None:
        self.cfg = cfg
        self.scan_code = scan_code
        self.only = set(only) if only else None
        self.rules = [r for r in RULES
                      if r.id not in cfg.disable
                      and (self.only is None or r.id in self.only)]
        self.compiled = {r.id: r.compiled() for r in RULES if r.pattern}
        self._shape_ids = {r.id for r in SHAPE_RULES}
        self.hits: List[Hit] = []
        # An INFO rule that fires 1,200 times buries the twelve findings worth
        # reading. Cap per file and per rule, and print what was dropped.
        self.info_cap = 3
        self._info_seen: Counter = Counter()
        self.empty_files: List[str] = []
        # Counters. Printed every run: a gate that will not say how much it
        # looked at cannot be told from one that looked at nothing.
        self.stats: Counter = Counter()
        self.per_file: Dict[str, Dict[str, float]] = {}
        self.corpus_words: Counter = Counter()
        self.corpus_word_files: Dict[str, set] = defaultdict(set)
        self.corpus_trigrams: Counter = Counter()
        self.corpus_trigram_files: Dict[str, set] = defaultdict(set)
        self.corpus_total_words = 0
        self.corpus_intensifiers = 0
        self.corpus_passive = 0

    # -- helpers ----------------------------------------------------------
    def _level(self, rule: Rule) -> str:
        return self.cfg.level_of(rule)

    def _allowed_spans(self, line: str) -> List[Tuple[int, int]]:
        spans = []
        low = line.lower()
        for phrase, _reason in self.cfg.allow:
            p = phrase.lower()
            start = 0
            while True:
                idx = low.find(p, start)
                if idx == -1:
                    break
                spans.append((idx, idx + len(p)))
                start = idx + 1
        return spans

    @staticmethod
    def _in_proper_noun_run(text: str, start: int) -> bool:
        """True when a Title-Cased match continues a run of capitalized words.

        Calibrated 2026-08-29: "Example Township Solar Conversion" is
        the name of a federal grant, and reporting it as LLM register is the
        checker judging somebody else's proper noun.
        """
        if start >= len(text) or not text[start].isupper():
            return False
        before = text[:start].rstrip()
        if not before:
            return False
        prev = before.split()[-1] if before.split() else ""
        return bool(prev) and prev[0].isupper() and prev.lower() not in {"the", "a", "an"}

    def _is_slogan(self, line: str, start: int, end: int) -> bool:
        """True when the match sits at the tail of a short sentence.

        The same words inside a longer sentence are usually doing real
        explanatory work; it is the slogan position that reads as generated.
        """
        left = max(line.rfind(".", 0, start), line.rfind(";", 0, start)) + 1
        candidates = [i for i in (line.find(".", end), line.find(";", end)) if i != -1]
        right = min(candidates) if candidates else len(line)
        sentence = line[left:right]
        if len(sentence.strip()) > self.cfg.threshold("slogan_max_sentence"):
            return False
        return (right - end) <= self.cfg.threshold("slogan_tail_chars")

    @staticmethod
    def _window(context: str, matched: str, width: int = 150) -> str:
        flat = re.sub(r"\s+", " ", context).strip()
        at = flat.lower().find(matched.strip().lower()[:40])
        if at < 0 or len(flat) <= width:
            return flat[:width]
        start = max(0, at - width // 3)
        end = min(len(flat), start + width)
        return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")

    def _emit(self, rule: Rule, source: str, line: int, matched: str, context: str) -> None:
        level = self._level(rule)
        if level == "INFO":
            key = (rule.id, source)
            self._info_seen[key] += 1
            if self._info_seen[key] > self.info_cap:
                self.stats["info_suppressed"] += 1
                return
        self.hits.append(Hit(rule, level, source, line, matched,
                             self._window(context, matched)))

    # -- layer 1 ----------------------------------------------------------
    def scan_phrases(self, source: str, chunks: Iterable[Chunk]) -> None:
        for chunk in chunks:
            if not chunk.text.strip():
                continue
            self.stats["chunks"] += 1
            skip = self._allowed_spans(chunk.text)
            for rule in self.rules:
                if rule.computed or rule.id not in self.compiled:
                    continue
                if chunk.scope not in rule.scopes:
                    continue
                if rule.id in self._shape_ids:
                    continue
                rx = self.compiled[rule.id]
                if rx is None:
                    continue
                for m in rx.finditer(chunk.text):
                    # Overlap, not containment: a rule often matches a wider
                    # span than the allowed phrase, and requiring containment
                    # would let the suppression silently miss.
                    if any(m.start() < e and s < m.end() for s, e in skip):
                        self.stats["allowed"] += 1
                        continue
                    if rule.slogan_only and not self._is_slogan(chunk.text, m.start(), m.end()):
                        continue
                    if self._in_proper_noun_run(chunk.text, m.start()):
                        self.stats["proper_noun_skips"] += 1
                        continue
                    self._emit(rule, source, chunk.loc, m.group(0), chunk.text)
                    break   # one hit per rule per chunk; the fix is the same edit

    # -- layer 2 ----------------------------------------------------------
    def scan_shapes(self, source: str, chunks: Iterable[Chunk]) -> None:
        limit = self.cfg.threshold("shape_max_chars")
        for chunk in chunks:
            text = chunk.text.strip()
            if chunk.kind not in {"string", "heading", "bullet"} or len(text) > limit:
                continue
            self.stats["display_strings"] += 1
            skip = self._allowed_spans(text)
            for rule in SHAPE_RULES:
                if rule.id in self.cfg.disable:
                    continue
                if self.only is not None and rule.id not in self.only:
                    continue
                if chunk.scope not in rule.scopes:
                    continue
                # colon-setup only judges headings: "Equinix deal: no per-unit
                # price published" is a fine note and a bad headline.
                if rule.id == "colon-setup" and chunk.kind != "heading":
                    continue
                matched = ""
                if rule.id == "comma-staple":
                    if is_twin(text):
                        matched = text
                elif rule.id == "epigram":
                    if is_epigram(text):
                        matched = text
                else:
                    rx = self.compiled.get(rule.id)
                    m = rx.search(text) if rx else None
                    if m:
                        matched = m.group(0)
                if not matched:
                    continue
                if skip and self._allowed_spans(matched):
                    self.stats["allowed"] += 1
                    continue
                self._emit(rule, source, chunk.loc, matched.strip()[:70], text)

    # -- layer 3 ----------------------------------------------------------
    def scan_structure(self, source: str, paragraphs: List[Chunk]) -> Dict[str, float]:
        lengths: List[int] = []
        signatures: List[Tuple[str, int, bool]] = []
        kickers = 0
        para_openers: List[str] = []
        em_dashes = 0
        nominalizations = 0

        mega_by_origin = {
            "html": self.cfg.threshold("mega_sentence_words_copy"),
            "json": self.cfg.threshold("mega_sentence_words_data"),
            "js": self.cfg.threshold("mega_sentence_words_data"),
        }
        mega_doc = self.cfg.threshold("mega_sentence_words_doc")
        wall = self.cfg.threshold("wall_paragraph_words")

        for chunk in paragraphs:
            text = chunk.text
            self.stats["paragraphs"] += 1
            n_words = word_count(text)
            self.stats["words"] += n_words
            em = text.count("\u2014")
            em_dashes += em
            pileup = EM_DASH_PILEUP.search(text)
            if self._enabled("em-dash-pileup") and pileup:
                self._emit(RULES_BY_ID["em-dash-pileup"], source, chunk.loc,
                           "3 em dashes within {} chars".format(len(pileup.group(0))),
                           pileup.group(0))
            if self._enabled("wall-paragraph") and n_words > wall \
                    and chunk.kind == "para" and chunk.origin in {"md", "html", "text", "py"}:
                self._emit(RULES_BY_ID["wall-paragraph"], source, chunk.loc,
                           "{} words".format(n_words), text)

            sentences = split_sentences(text)
            self.stats["sentences"] += len(sentences)
            mega = mega_by_origin.get(chunk.origin, mega_doc)
            run_short = 0
            for s in sentences:
                n = word_count(s)
                if n >= 3:
                    lengths.append(n)
                    signatures.append(sentence_signature(s))
                run = longest_clause(s)
                if self._enabled("mega-sentence") and run > mega:
                    self._emit(RULES_BY_ID["mega-sentence"], source, chunk.loc,
                               "{} words unbroken (of {})".format(run, n), s)
                if self._enabled("hedge-stack"):
                    stacked = hedge_stack(s, int(self.cfg.threshold("hedge_distinct_min")))
                    if stacked:
                        self._emit(RULES_BY_ID["hedge-stack"], source, chunk.loc, stacked, s)
                if self._enabled("tricolon") and is_tricolon(s):
                    self._emit(RULES_BY_ID["tricolon"], source, chunk.loc,
                               tricolon_match(s), s)
                if self._enabled("noun-cluster"):
                    cluster = noun_cluster(s, int(self.cfg.threshold("noun_cluster_min")))
                    if cluster:
                        self._emit(RULES_BY_ID["noun-cluster"], source, chunk.loc, cluster, s)
                # parataxis: a run of short declaratives with no subordination
                if n and n <= self.cfg.threshold("parataxis_max_words") \
                        and "," not in s and not has_subordinator(s):
                    run_short += 1
                    if self._enabled("parataxis-run") and \
                            run_short == int(self.cfg.threshold("parataxis_run_min")):
                        self._emit(RULES_BY_ID["parataxis-run"], source, chunk.loc,
                                   "{} short declaratives in a row".format(run_short), text)
                else:
                    run_short = 0
                nominalizations += len(re.findall(r"\w+(?:tion|ment|ance|ence)\b", s, re.I))

            if sentences and chunk.kind == "para":
                last = sentences[-1]
                if len(sentences) >= 3 and word_count(last) <= self.cfg.threshold("kicker_max_words") \
                        and not re.search(r"\d", last):
                    kickers += 1
                first_word = (words_of(sentences[0]) or [""])[0].lower()
                para_openers.append(first_word)

            words = words_of(text)
            self.corpus_total_words += len(words)
            self.corpus_intensifiers += sum(1 for w in words if w.lower() in INTENSIFIERS)
            self.corpus_passive += len(re.findall(
                r"\b(?:is|are|was|were|been|being|be)\s+(?:\w+ly\s+)?\w+(?:ed|en)\b", text, re.I))
            seen_words = set()
            for w in words:
                lw = w.lower()
                if len(lw) >= 5 and lw not in STOPWORDS and not lw.isdigit():
                    self.corpus_words[lw] += 1
                    seen_words.add(lw)
            for lw in seen_words:
                self.corpus_word_files[lw].add(source)
            lowered = [w.lower() for w in words]
            for i in range(len(lowered) - 2):
                tri = tuple(lowered[i:i + 3])
                if all(t in STOPWORDS for t in tri):
                    continue
                if any(t.isdigit() for t in tri):
                    continue
                key = " ".join(tri)
                self.corpus_trigrams[key] += 1
                self.corpus_trigram_files[key].add(source)

        # Rhythm measurements, per file.
        loc0 = paragraphs[0].loc if paragraphs else 0
        burst = cv(lengths)
        if self._enabled("low-burstiness") and \
                len(lengths) >= self.cfg.threshold("burstiness_min_sentences") and \
                burst < self.cfg.threshold("burstiness_min_cv"):
            self._emit(RULES_BY_ID["low-burstiness"], source, loc0,
                       "CV {:.2f} over {} sentences".format(burst, len(lengths)),
                       "mean {:.1f} words, sd {:.1f}".format(
                           statistics.fmean(lengths), statistics.pstdev(lengths)))
        if self._enabled("uniform-template"):
            run, prev = 1, None
            for sig in signatures:
                if sig == prev:
                    run += 1
                    if run == int(self.cfg.threshold("template_run_min")):
                        self._emit(RULES_BY_ID["uniform-template"], source, loc0,
                                   "{} sentences on template {}".format(run, sig),
                                   "opener={} length-bucket={} comma={}".format(*sig))
                        run = 0
                else:
                    run, prev = 1, sig
        if self._enabled("kicker-cadence") and kickers >= self.cfg.threshold("kicker_min_paragraphs"):
            self._emit(RULES_BY_ID["kicker-cadence"], source, loc0,
                       "{} paragraphs end on a kicker".format(kickers),
                       "the reader learns the beat and stops hearing it")
        if self._enabled("paragraph-opening-repeat"):
            run, prev = 1, None
            for opener in para_openers:
                if opener and opener == prev:
                    run += 1
                    if run == 3:
                        self._emit(RULES_BY_ID["paragraph-opening-repeat"], source, loc0,
                                   "3 paragraphs open on {!r}".format(opener),
                                   "vary the entry point")
                        run = 0
                else:
                    run, prev = 1, opener

        return {"sentences": len(lengths), "burstiness": burst, "em_dashes": em_dashes,
                "nominalizations": nominalizations,
                "mean_sentence": statistics.fmean(lengths) if lengths else 0.0}

    def scan_markdown_shape(self, source: str, chunks: List[Chunk]) -> None:
        """Document-scale shapes: bullet uniformity, bold lead-ins, heading template."""
        bullets = [c.text for c in chunks if c.kind == "bullet"]
        headings = [c.text for c in chunks if c.kind == "heading"]
        loc0 = chunks[0].loc if chunks else 0
        if bullets:
            lens = [word_count(b) for b in bullets]
            min_items = int(self.cfg.threshold("uniform_bullets_min_items"))
            if self._enabled("uniform-bullets") and len(lens) >= min_items \
                    and cv(lens) < self.cfg.threshold("uniform_bullets_max_cv"):
                self._emit(RULES_BY_ID["uniform-bullets"], source, loc0,
                           "{} bullets, CV {:.2f}".format(len(lens), cv(lens)),
                           "mean {:.1f} words".format(statistics.fmean(lens)))
            bold = sum(1 for b in bullets if re.match(r"^\s*[-*+]\s*\*\*[^*]{1,60}\*\*", b))
            if self._enabled("bold-lead-in") and len(bullets) >= min_items \
                    and bold / len(bullets) >= self.cfg.threshold("bold_lead_in_ratio"):
                self._emit(RULES_BY_ID["bold-lead-in"], source, loc0,
                           "{}/{} bullets open on a bold label".format(bold, len(bullets)),
                           "the model's default outline format")
        if headings and len(headings) >= 3:
            colon = sum(1 for h in headings if re.match(r"^[^:(\[]{4,60}:\s+\S", h))
            if self._enabled("heading-colon") and \
                    colon / len(headings) >= self.cfg.threshold("heading_colon_ratio"):
                self._emit(RULES_BY_ID["heading-colon"], source, loc0,
                           "{}/{} headings run label:point".format(colon, len(headings)),
                           "promote the point into the heading")

    def _enabled(self, rule_id: str) -> bool:
        if rule_id in self.cfg.disable:
            return False
        return self.only is None or rule_id in self.only

    # -- corpus -----------------------------------------------------------
    def scan_corpus(self) -> List[Tuple[str, int, int]]:
        motifs: List[Tuple[str, int, int]] = []
        if self.corpus_total_words < self.cfg.threshold("corpus_min_words"):
            return motifs
        per_1k = self.corpus_total_words / 1000.0
        if self._enabled("motif-word"):
            for word, count in self.corpus_words.most_common(400):
                files = len(self.corpus_word_files[word])
                if count >= self.cfg.threshold("motif_min_count") \
                        and files >= self.cfg.threshold("motif_min_files") \
                        and count / per_1k >= self.cfg.threshold("motif_min_rate"):
                    motifs.append((word, count, files))
            for word, count, files in motifs[:12]:
                self._emit(RULES_BY_ID["motif-word"], "<corpus>", 0,
                           "{!r} ×{} across {} files".format(word, count, files),
                           "{:.1f} per 1,000 words".format(count / per_1k))
        if self._enabled("phrase-tic"):
            tics = [(phrase, count, len(self.corpus_trigram_files[phrase]))
                    for phrase, count in self.corpus_trigrams.most_common(400)
                    if count >= self.cfg.threshold("phrase_tic_min_count")
                    and len(self.corpus_trigram_files[phrase])
                    >= self.cfg.threshold("phrase_tic_min_files")]
            for phrase, count, files in tics[:10]:
                self._emit(RULES_BY_ID["phrase-tic"], "<corpus>", 0,
                           "{!r} ×{} across {} files".format(phrase, count, files),
                           "a phrase repeated is a phrase nobody chose")
            if len(tics) > 10:
                self.stats["info_suppressed"] += len(tics) - 10
        if self._enabled("intensifier-density"):
            rate = self.corpus_intensifiers / per_1k
            if rate >= self.cfg.threshold("intensifier_per_1k"):
                self._emit(RULES_BY_ID["intensifier-density"], "<corpus>", 0,
                           "{:.1f} per 1,000 words".format(rate),
                           "{} padding adverbs in {} words".format(
                               self.corpus_intensifiers, self.corpus_total_words))
        if self._enabled("passive-density"):
            rate = self.corpus_passive / per_1k
            if rate >= self.cfg.threshold("passive_per_1k"):
                self._emit(RULES_BY_ID["passive-density"], "<corpus>", 0,
                           "{:.1f} per 1,000 words".format(rate),
                           "{} passive constructions in {} words".format(
                               self.corpus_passive, self.corpus_total_words))
        return motifs

    # -- driver -----------------------------------------------------------
    def scan_source(self, name: str, text: str) -> Dict[str, float]:
        """Run all three layers over one named source. The extractor is chosen
        by the name's suffix, so a fixture and a real file take one code path."""
        path = Path(name)
        ex = extract(path, text, self.scan_code, self.cfg.quote_keys)
        self.stats["files"] += 1
        self.stats["quoted_exempt"] += ex.quoted_exempt
        if not ex.chunks:
            self.stats["files_empty"] += 1
            self.empty_files.append(name)
            return {}
        before = len(self.hits)
        self.scan_phrases(name, ex.chunks)
        self.scan_shapes(name, ex.chunks)
        metrics = self.scan_structure(name, ex.paragraphs)
        if path.suffix.lower() in {".md", ".markdown", ".rst"}:
            self.scan_markdown_shape(name, ex.chunks)
        metrics["findings"] = len(self.hits) - before
        metrics["words"] = float(sum(word_count(c.text) for c in ex.chunks))
        return metrics

    def scan_file(self, path: Path, root: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            self.stats["unreadable"] += 1
            print("  ! unreadable: {} ({})".format(path, exc), file=sys.stderr)
            return
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        # The suffix drives extraction, so the relative name has to keep it.
        metrics = self.scan_source(rel, text)
        if metrics:
            self.per_file[rel] = metrics

    def scan_units(self, source: str, text: str, scope: str) -> None:
        ex = extract_text(text, scope)
        self.stats["files"] += 1
        self.stats["quoted_exempt"] += ex.quoted_exempt
        self.scan_phrases(source, ex.chunks)
        self.scan_shapes(source, ex.chunks)
        self.scan_structure(source, ex.paragraphs)


# ---------------------------------------------------------------------------
# Computed predicates
# ---------------------------------------------------------------------------
def hedge_stack(sentence: str, minimum: int) -> str:
    """N or more DISTINCT hedges inside one sentence. The same qualifier
    repeated is emphasis, not a stack."""
    found = {h for h in HEDGES if re.search(r"\b{}\b".format(h), sentence, re.I)}
    return ", ".join(sorted(found)) if len(found) >= minimum else ""


TWIN_CONJ = {"if", "when", "because", "so", "and", "but", "while", "though",
             "unless", "since", "after", "before", "then", "which", "who",
             "where", "as", "for", "with", "or", "yet", "until", "whereas"}


def _name_like(fragment: str) -> bool:
    """True when a fragment is a proper name rather than authored phrasing.

    Calibrated 2026-08-29 against 225 comma-staple hits on a research corpus,
    every one of which was an organization, a place or a program id: "Central
    Valley Research Station, Ohio", "Meridian Logistics Group, Inc.",
    "Clearwater Basin, Example Territory". The staple is a cadence defect in
    copy somebody wrote; a name that happens to contain a comma is neither.
    """
    alpha = [w for w in fragment.split() if w[:1].isalpha()]
    if len(alpha) < 2:
        return bool(alpha) and alpha[0][:1].isupper()
    caps = sum(1 for w in alpha if w[0].isupper())
    return caps / len(alpha) >= 0.6


def is_twin(s: str) -> bool:
    """A "short phrase, short phrase" comma staple.

    One comma, no internal period, a short trailing fragment. Excludes bylines
    ("J. Alvarez, for the client") and conjunction-led second clauses
    ("the benefits pass through, if the numbers are public"), which are real
    sentences rather than mannered staples.
    """
    s = s.strip().rstrip(".")
    if s.count(",") != 1 or "." in s or ":" in s:
        return False
    # Parentheses, figures and dash separators mark a data field, not a display
    # string somebody composed. So does an all-caps token like LLC or AFB.
    if re.search(r"[()\[\]{}+&]|\d|\$| [-–—] ", s):
        return False
    a, b = (x.strip() for x in s.split(",", 1))
    if not a or not b:
        return False
    bw = b.split()
    if bw and bw[0].lower() in TWIN_CONJ:
        return False
    # A serial comma is a list, not a staple: "meat, dairy and frozen-food
    # plants" reads as three items because it is three items.
    if re.search(r"\b(and|or|plus)\b", b, re.I):
        return False
    # A byline is Proper Noun(s), then a role or an org.
    if re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+$", a):
        return False
    if _name_like(a) or _name_like(b):
        return False
    return 2 <= len(a.split()) <= 8 and 1 <= len(bw) <= 6


def is_epigram(s: str) -> bool:
    """A short aphoristic "X is Y" line with no number in it."""
    s = s.strip()
    words = s.split()
    return (len(words) <= 11
            and not re.search(r"\d", s)
            and re.search(r"\b(is|are|was|were)\b", s, re.I) is not None
            and s.endswith("."))


TRICOLON = re.compile(
    r"\b([A-Za-z][a-z]{2,14}),\s+([A-Za-z][a-z]{2,14}),\s+and\s+([A-Za-z][a-z]{2,14})\b")


def tricolon_match(s: str) -> str:
    m = TRICOLON.search(s)
    return m.group(0) if m else ""


LINKING_VERB = re.compile(r"\b(?:is|are|was|were|be|been|feels?|seems?|looks?|"
                          r"remains?|stays?|reads?)\s+$", re.I)


def is_tricolon(s: str) -> bool:
    """Three bare predicate adjectives in a row: the rule-of-three cadence.

    Calibrated 2026-08-29 against six hits in this repo, all of them real
    enumerations: "Loading, error, and empty states", "a future bug, migration,
    and advisory", "Separate facts, estimates, and judgments". What separates
    the cadence from a list is that the cadence is a PREDICATE with nothing
    between the verb and the first item, and it ends the clause. A determiner
    ("a future ...") or a following head noun ("... states") makes it a list.
    """
    m = TRICOLON.search(s)
    if not m:
        return False
    if word_count(s) > 14 or re.search(r"\d", m.group(0)):
        return False
    if not LINKING_VERB.search(s[:m.start()]):
        return False
    tail = s[m.end():].lstrip()
    return tail[:1] in {"", ".", "!", "?", ",", ";", ":"}


NOUNISH_BAD_SUFFIX = ("ly", "ing", "ed")

# Units and quantities read as nouns and stack harmlessly in technical prose.
UNIT_WORDS = {"kwh", "mwh", "gwh", "kw", "mw", "gw", "btu", "psi", "gpm", "cfm",
              "percent", "million", "billion", "thousand", "kilowatt", "megawatt",
              "gigawatt", "hour", "hours", "year", "years", "day", "days", "unit",
              "units", "acre", "acres", "mile", "miles", "foot", "feet", "ton",
              "tons", "tonne", "tonnes", "usd", "dollar", "dollars"}


def noun_cluster(s: str, minimum: int) -> str:
    """A run of stacked noun-modifiers, approximated without a POS tagger.

    A word counts as nounish when it is not a function word, not an adverb or
    participle by suffix, and at least three characters. Four in a row is the
    "user engagement optimization framework" shape.
    """
    run: List[str] = []
    # Words and punctuation only. Whitespace is skipped rather than treated as
    # a break: tokenizing it as a token reset the run on every space, which is
    # why this could never reach four and reported nothing for weeks.
    for token in re.findall(r"[A-Za-z][A-Za-z\-']*|[^A-Za-z\s]+", s):
        w = token.strip()
        if not w or not w[0].isalpha():
            run = []
            continue
        lw = w.lower()
        # A possessive or a unit breaks the cluster: "the village power
        # plant's fossil fuel use" is ordinary technical prose, not a stack.
        nounish = (len(lw) >= 3 and lw not in FUNCTION_WORDS and lw not in UNIT_WORDS
                   and "'" not in w and not any(c.isdigit() for c in w)
                   and not lw.endswith(NOUNISH_BAD_SUFFIX) and not w[0].isupper())
        if nounish:
            run.append(w)
            if len(run) >= minimum:
                return " ".join(run[-minimum:])
        else:
            run = []
    return ""


def has_subordinator(s: str) -> bool:
    low = " " + s.lower() + " "
    return any(" {} ".format(k) in low for k in SUBORDINATORS)


def sentence_signature(s: str) -> Tuple[str, int, bool]:
    """A coarse syntactic fingerprint: what opens it, how long, does it turn.

    Three sentences sharing one signature read as generated even when each is
    individually fine.
    """
    ws = words_of(s)
    first = ws[0].lower() if ws else ""
    if first in DET:
        cls = "DET"
    elif first in PRO:
        cls = "PRO"
    elif first in CONJ:
        cls = "CONJ"
    elif first in PREP:
        cls = "PREP"
    elif first.endswith("ing"):
        cls = "GER"
    else:
        cls = "OTHER"
    return (cls, min(len(ws) // 8, 4), "," in s)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def density_score(metrics: Dict[str, float], fails: int, warns: int) -> float:
    """Findings per 1,000 words, weighted. A signal, not a verdict.

    Components are printed alongside it: a composite number with no visible
    parts is a black box, and nobody should act on one.
    """
    words = max(metrics.get("words", 0.0), 1.0)
    return round((fails * 3 + warns) / words * 1000, 2)


def report(checker: Checker, args: argparse.Namespace, scanned_label: str) -> int:
    hits = checker.hits
    seen = set()
    unique: List[Hit] = []
    info_files: Dict[Tuple[str, str], set] = defaultdict(set)
    for h in hits:
        if h.level == "INFO":
            info_files[(h.rule.id, h.matched.strip().lower())].add(h.source)
        k = h.key()
        if k in seen:
            continue
        seen.add(k)
        unique.append(h)
    # Second pass: one line per distinct INFO finding, annotated with its reach.
    collapsed: List[Hit] = []
    info_seen: set = set()
    for h in unique:
        if h.level != "INFO":
            collapsed.append(h)
            continue
        key = (h.rule.id, h.matched.strip().lower())
        if key in info_seen:
            continue
        info_seen.add(key)
        n = len(info_files[key])
        if n > 1:
            h.matched = "{} (in {} files)".format(h.matched, n)
        collapsed.append(h)
    unique = collapsed
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    unique.sort(key=lambda h: (order.get(h.level, 3), h.rule.family, h.source, h.line))

    fails = [h for h in unique if h.level == "FAIL"]
    warns = [h for h in unique if h.level == "WARN"]
    infos = [h for h in unique if h.level == "INFO"]

    if args.calibrate:
        print_calibration(unique, checker)
    else:
        show = unique if args.show_info else fails + warns
        for h in show:
            print(h.render())
        if infos and not args.show_info:
            by_rule = Counter(h.rule.id for h in infos)
            print("\n{} INFO finding(s) not shown (--info to see them): {}".format(
                len(infos), ", ".join("{}×{}".format(k, v) for k, v in by_rule.most_common())))

    if args.stats:
        print_stats(checker)

    s = checker.stats
    print("\n" + "=" * 78)
    print("scanned {} · {} chunks · {} display strings · {} paragraphs "
          "· {} sentences · {} words".format(
              scanned_label, s["chunks"], s["display_strings"], s["paragraphs"],
              s["sentences"], s["words"]))
    print("{} FAIL · {} WARN · {} INFO   ({} quoted spans exempt, "
          "{} allowlist suppressions, {} proper-noun skips, {} INFO capped, "
          "{} rules active)".format(
              len(fails), len(warns), len(infos), s["quoted_exempt"], s["allowed"],
              s["proper_noun_skips"], s["info_suppressed"], len(checker.rules)))
    if s["files_empty"]:
        print("{} file(s) yielded no prose at all, so nothing in them was "
              "checked: {}".format(
                  s["files_empty"],
                  ", ".join(checker.empty_files[:5]) +
                  (", …" if len(checker.empty_files) > 5 else "")))
    for name, value in sorted(checker.cfg.thresholds.items()):
        print("threshold {} = {:g} (default {:g}): {}".format(
            name, value, DEFAULTS[name], checker.cfg.threshold_reasons.get(name, "")))
    if checker.cfg.disable:
        print("disabled by config: " + ", ".join(
            "{} ({})".format(k, v[:48]) for k, v in checker.cfg.disable.items()))
    if not unique:
        print("no slop found")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"version": VERSION,
             "counts": {k: s[k] for k in
                        ("files", "files_empty", "unreadable", "chunks",
                         "display_strings", "paragraphs", "sentences", "words",
                         "quoted_exempt", "allowed", "proper_noun_skips",
                         "info_suppressed")},
             "levels": {"FAIL": len(fails), "WARN": len(warns), "INFO": len(infos)},
             "findings": [h.to_dict() for h in unique]}, indent=1))
        print("full detail → {}".format(args.json))

    # Scanning nothing is an error, never a pass.
    if s["chunks"] == 0:
        print("\nERROR: scanned 0 chunks of prose. A gate with an empty input set "
              "reports success and measures nothing.", file=sys.stderr)
        return 2
    if args.fail_on == "NEVER":
        return 0
    threshold = LEVEL_RANK[args.fail_on]
    worst = max((LEVEL_RANK[h.level] for h in unique), default=0)
    return 1 if worst >= threshold else 0


def print_calibration(hits: List[Hit], checker: Checker) -> None:
    """The first-run triage table.

    Calibration means sorting findings into fix-now, exempt-with-a-stated-
    reason, and turn-off-with-a-stated-reason. It does not mean widening the
    allowlist until the output is empty.
    """
    by_rule: Dict[str, List[Hit]] = defaultdict(list)
    for h in hits:
        by_rule[h.rule.id].append(h)
    print("CALIBRATION — triage each rule into fix-now / exempt-with-reason / off-with-reason")
    print("=" * 78)
    rows = sorted(by_rule.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    print("{:26} {:5} {:>6}  {}".format("rule", "level", "hits", "files"))
    print("-" * 78)
    for rid, group in rows:
        files = len({h.source for h in group})
        print("{:26} {:5} {:>6}  {}".format(rid, group[0].level, len(group), files))
        for h in group[:2]:
            print("      {}:{}  {!r}".format(h.source[-40:], h.line, h.matched[:56]))
            print("        … {}".format(re.sub(r"\s+", " ", h.context).strip()[:110]))
    print("-" * 78)
    print("To exempt a phrase, add it to .slopcheck.json with a reason you would defend:")
    print('  {"allow": [{"phrase": "...", "reason": "..."}]}')
    print("To turn a rule off, the reason is the value:")
    print('  {"disable": {"rule-id": "why this rule does not apply to this project"}}')


def print_stats(checker: Checker) -> None:
    per_file_fail: Counter = Counter()
    per_file_warn: Counter = Counter()
    for h in checker.hits:
        if h.level == "FAIL":
            per_file_fail[h.source] += 1
        elif h.level == "WARN":
            per_file_warn[h.source] += 1
    print("\nRHYTHM — per file (burstiness: human prose runs uneven; low is the tell)")
    print("-" * 78)
    print("{:38} {:>6} {:>6} {:>7} {:>4} {:>9} {:>7}".format(
        "file", "sent", "mean", "burst", "em", "F/W/I", "per-1k"))
    rows = sorted(checker.per_file.items(),
                  key=lambda kv: (kv[1].get("burstiness", 1.0), -kv[1].get("findings", 0)))
    for name, m in rows[:30]:
        if m.get("sentences", 0) < 5:
            continue
        flag = "*" if m.get("burstiness", 1) < checker.cfg.threshold("burstiness_min_cv") else " "
        f, w = per_file_fail.get(name, 0), per_file_warn.get(name, 0)
        i = max(int(m.get("findings", 0)) - f - w, 0)
        print("{:38} {:>6} {:>6.1f} {:>6.2f}{} {:>4} {:>9} {:>7.2f}".format(
            name[-38:], int(m.get("sentences", 0)), m.get("mean_sentence", 0),
            m.get("burstiness", 0), flag, int(m.get("em_dashes", 0)),
            "{}/{}/{}".format(f, w, i),
            density_score(m, f, w)))
    print("* = below the burstiness floor ({}). per-1k weights each FAIL ×3 and each "
          "WARN ×1 per 1,000 words;\n    its components are the columns beside it. "
          "A report, not a verdict: the editor judges it."
          .format(checker.cfg.threshold("burstiness_min_cv")))
    if checker.corpus_total_words:
        print("\nCORPUS — {} words".format(checker.corpus_total_words))
        top = [(w, c) for w, c in checker.corpus_words.most_common(15)]
        print("  most-repeated content words: " +
              ", ".join("{}×{}".format(w, c) for w, c in top))


# ---------------------------------------------------------------------------
# Self-test: prove the rules still fire
# ---------------------------------------------------------------------------
CORPUS_FIXTURE = {
    "a.md": "The receipts are attached. Energy receipts matter here.\n",
    "b.md": "Community receipts followed. The receipts told the story.\n",
    "c.md": "Delivery receipts closed it out. More receipts arrived later.\n",
}


def fixture_harness(rule: Rule) -> str:
    """Which extractor a rule's fixtures must travel through.

    A shape rule judges display strings, so its fixture has to arrive as one; a
    structural rule judges paragraphs and headings, so its fixture has to be a
    document. Feeding every fixture through the same plain-text path is how
    ten rules "passed" a selftest while never firing at all.
    """
    if rule.fixture_as:
        return rule.fixture_as
    if SCOPE_DOC in rule.scopes:
        return "md"
    if SCOPE_COPY in rule.scopes:
        return "json"
    if SCOPE_CODE in rule.scopes:
        return "py"
    return "txt"


def _hits_for(text: str, rule: Rule, cfg: Config) -> List[Hit]:
    checker = Checker(cfg, scan_code=True)
    kind = fixture_harness(rule)
    if kind == "json":
        # A "title" value lands as a heading-kind display string in copy scope,
        # which is what the shape rules read.
        checker.scan_source("fixture.json", json.dumps({"title": text}))
    elif kind == "py":
        body = "\n".join("# " + line for line in text.splitlines())
        checker.scan_source("fixture.py", body + "\n")
    elif kind == "txt":
        checker.scan_units("fixture.txt", text, SCOPE_TEXT)
    else:
        checker.scan_source("fixture.md", text)
    return checker.hits


def selftest(verbose: bool = False) -> int:
    """Every rule must fire on its own known-bad fixture and stay silent on its
    known-good one.

    This exists because the failure mode of a checker is silence. A pattern
    "improved" until it stops matching passes everything and looks exactly like
    clean code, so the two fixtures ship with the rule, not after it.
    """
    cfg = Config()
    failures: List[str] = []
    checked = 0
    no_fixture: List[str] = []

    for rule in RULES:
        if not rule.bad and not rule.good:
            if rule.family != "corpus":
                no_fixture.append(rule.id)
            continue
        for sample in rule.bad:
            checked += 1
            hits = _hits_for(sample, rule, cfg)
            if not any(h.rule.id == rule.id for h in hits):
                failures.append("{}: known-BAD fixture did not fire: {!r}"
                                .format(rule.id, sample[:70]))
            elif verbose:
                print("  fires  {:26} {!r}".format(rule.id, sample[:60]))
        for sample in rule.good:
            checked += 1
            hits = _hits_for(sample, rule, cfg)
            bad = [h for h in hits if h.rule.id == rule.id]
            if bad:
                failures.append("{}: known-GOOD fixture fired: {!r} ← {!r}"
                                .format(rule.id, sample[:70], bad[0].matched))
            elif verbose:
                print("  quiet  {:26} {!r}".format(rule.id, sample[:60]))

    # Corpus rules need a corpus, so they get their own fixture.
    checker = Checker(Config(thresholds={"motif_min_count": 4, "motif_min_files": 3,
                                         "motif_min_rate": 0.5, "corpus_min_words": 10}))
    for name, body in CORPUS_FIXTURE.items():
        ex = extract_md(body)
        checker.stats["files"] += 1
        checker.scan_structure(name, ex.paragraphs)
    checker.scan_corpus()
    checked += 1
    if not any(h.rule.id == "motif-word" and "receipts" in h.matched for h in checker.hits):
        failures.append("motif-word: corpus fixture did not surface the repeated word")
    elif verbose:
        print("  fires  {:26} corpus fixture".format("motif-word"))

    # Extractors must not be vacuous. A silent extractor makes every rule pass.
    checked += 1
    probes = {
        "x.md": ("# Title\n\nIt's worth noting that this delves into things.\n", 1),
        "x.html": ("<html><body><p>A seamless tapestry of value.</p></body></html>", 1),
        "x.js": ('const copy = { title: "A seamless tapestry of value here" };', 1),
        "x.json": ('{"summary": "A seamless tapestry of value in prose"}', 1),
        "x.py": ('def f():\n    """A seamless tapestry of value in a docstring."""\n', 1),
    }
    for name, (body, want) in probes.items():
        ex = extract(Path(name), body, True)
        if len(ex.chunks) < want:
            failures.append("extractor for {} returned {} chunks; it would make every "
                            "rule pass vacuously".format(name, len(ex.chunks)))
        elif verbose:
            print("  extract {:25} {} chunk(s)".format(name, len(ex.chunks)))

    # Allowlist discipline: a reason that would not survive review is rejected.
    checked += 1
    try:
        _check_reason("selftest", "TODO")
        failures.append("_check_reason accepted a placeholder reason")
    except SystemExit:
        pass

    print("\nSELFTEST: {} assertions over {} rules".format(checked, len(RULES)))
    if no_fixture:
        print("rules with no fixture (each one is a rule that cannot be proven to "
              "fire): " + ", ".join(no_fixture))
        failures.append("{} rule(s) ship without a fixture".format(len(no_fixture)))
    for f in failures:
        print("  FAIL  " + f)
    if failures:
        print("\n{} selftest failure(s)".format(len(failures)))
        return 1
    print("all rules fire on their known-bad fixture and stay quiet on their known-good one")
    return 0


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
def collect_files(paths: Sequence[str], cfg: Config, root: Path,
                  include_agent_docs: bool = False) -> List[Path]:
    out: List[Path] = []
    skipped_agent_docs: List[str] = []
    excludes = set(DEFAULT_EXCLUDE_PARTS)
    extra = [e.rstrip("/") for e in cfg.exclude]
    self_name = Path(__file__).name
    for raw in paths:
        p = Path(raw)
        candidates = [p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else []
        for f in candidates:
            if not f.is_file() or f.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            if set(f.parts) & excludes:
                continue
            rel = str(f)
            if any(e and (rel.startswith(e) or "/{}/".format(e) in rel or
                          "/{}".format(e) == rel[-len(e) - 1:]) for e in extra):
                continue
            # The rule table has to quote the phrases it bans. A checker that
            # scans itself reports its own vocabulary as slop.
            if f.name == self_name or f.name == ".slopcheck.json":
                continue
            if not include_agent_docs and f.name.lower() in AGENT_DOCS:
                skipped_agent_docs.append(str(f))
                continue
            try:
                if f.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(f)
    if skipped_agent_docs:
        print("skipped {} contributor doc(s) that quote banned phrases by design "
              "(--include-agent-docs to scan them): {}".format(
                  len(skipped_agent_docs),
                  ", ".join(sorted(Path(x).name for x in set(skipped_agent_docs))[:8])))
    return sorted(set(out))


def git_commit_messages(rev_range: str, root: Path) -> List[Tuple[str, str]]:
    sep = "\x1e"
    proc = subprocess.run(["git", "log", "--format=%H%x1f%B{}".format(sep), rev_range],
                          cwd=str(root), capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("git log failed: " + (proc.stderr.strip() or "?"))
    out = []
    for chunk in proc.stdout.split(sep):
        if not chunk.strip():
            continue
        sha, _, body = chunk.strip().partition("\x1f")
        out.append((sha[:9], body))
    return out


def changed_files(rev_range: str, root: Path) -> List[str]:
    proc = subprocess.run(["git", "diff", "--name-only", rev_range],
                          cwd=str(root), capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("git diff failed: " + (proc.stderr.strip() or "?"))
    return [line for line in proc.stdout.splitlines() if line.strip()]


def pr_text(number: int, root: Path) -> str:
    proc = subprocess.run(["gh", "pr", "view", str(number), "--json", "title,body"],
                          cwd=str(root), capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("gh pr view failed: " + (proc.stderr.strip() or "?"))
    d = json.loads(proc.stdout)
    return "{}\n\n{}".format(d.get("title", ""), d.get("body", ""))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=None,
                    help="files or directories to scan (default: .)")
    ap.add_argument("--stdin", action="store_true", help="scan text on stdin")
    ap.add_argument("--commits", metavar="RANGE", help="scan commit messages in a git range")
    ap.add_argument("--changed", metavar="RANGE", help="scan files changed in a git range")
    ap.add_argument("--pr", type=int, help="scan a PR title and body via gh")
    ap.add_argument("--include-agent-docs", action="store_true",
                    help="also scan CLAUDE.md, AGENTS.md, issues.md and friends")
    ap.add_argument("--code", action="store_true",
                    help="also scan comments and docstrings (off by default: a "
                         "comment is a different register from shipped copy)")
    ap.add_argument("--only", metavar="IDS",
                    help="run only these rule ids (comma-separated)")
    ap.add_argument("--fail-on", default="FAIL", choices=["FAIL", "WARN", "INFO", "NEVER"])
    ap.add_argument("--info", dest="show_info", action="store_true",
                    help="print INFO findings inline")
    ap.add_argument("--stats", action="store_true", help="rhythm and corpus statistics")
    ap.add_argument("--calibrate", action="store_true",
                    help="triage table for a first run against a new corpus")
    ap.add_argument("--json", metavar="PATH", help="write all findings as JSON")
    ap.add_argument("--list", action="store_true", help="list the rules and exit")
    ap.add_argument("--explain", metavar="RULE_ID", help="explain one rule and exit")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every rule still fires on its known-bad fixture")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        print("slopcheck {}  ({} rules)".format(VERSION, len(RULES)))
        return 0

    if args.list:
        print("{:26} {:5} {:12} {}".format("rule", "level", "family", "why"))
        print("-" * 100)
        for r in sorted(RULES, key=lambda r: ({"FAIL": 0, "WARN": 1, "INFO": 2}[r.level],
                                              r.family, r.id)):
            print("{:26} {:5} {:12} {}".format(r.id, r.level, r.family, r.why))
        print("\n{} rules · {} FAIL · {} WARN · {} INFO".format(
            len(RULES), *[sum(1 for r in RULES if r.level == lv) for lv in LEVELS]))
        return 0

    if args.explain:
        rule = RULES_BY_ID.get(args.explain)
        if not rule:
            print("unknown rule {!r}. --list shows them all.".format(args.explain),
                  file=sys.stderr)
            return 2
        print("{}  [{}]  family={}".format(rule.id, rule.level, rule.family))
        print("  why    {}".format(rule.why))
        print("  fix    {}".format(rule.fix))
        print("  scopes {}".format(", ".join(rule.scopes)))
        if rule.origin:
            print("  origin {}".format(rule.origin))
        if rule.pattern:
            print("  regex  {}".format(rule.pattern))
        for b in rule.bad:
            print("  BAD    {!r}".format(b[:110]))
        for g in rule.good:
            print("  GOOD   {!r}".format(g[:110]))
        return 0

    if args.selftest:
        return selftest(args.verbose)

    paths = list(args.paths or [])
    root = Path(paths[0]).resolve() if paths else Path.cwd()
    if root.is_file():
        root = root.parent
    if len(paths) > 1:
        root = Path.cwd()
    cfg = load_config(root)
    only = [r.strip() for r in args.only.split(",")] if args.only else None
    for rid in only or []:
        if rid not in RULES_BY_ID:
            print("unknown rule id {!r}; --list shows them all".format(rid),
                  file=sys.stderr)
            return 2
    checker = Checker(cfg, only, scan_code=args.code)
    if cfg.path:
        print("config: {}".format(cfg.path))

    label = ""
    if args.stdin:
        checker.scan_units("<stdin>", sys.stdin.read(), SCOPE_TEXT)
        label = "stdin"
    elif args.commits:
        msgs = git_commit_messages(args.commits, Path.cwd())
        for sha, body in msgs:
            checker.scan_units("commit {}".format(sha), body, SCOPE_TEXT)
        label = "{} commit message(s)".format(len(msgs))
    elif args.pr is not None:
        checker.scan_units("PR #{}".format(args.pr), pr_text(args.pr, Path.cwd()), SCOPE_TEXT)
        label = "PR #{}".format(args.pr)
    else:
        if args.changed:
            names = changed_files(args.changed, Path.cwd())
            files = collect_files(names, cfg, Path.cwd(), args.include_agent_docs)
            root = Path.cwd()
        else:
            files = collect_files(paths or ["."], cfg, root, args.include_agent_docs)
        if not files:
            if args.changed:
                print("0 prose files in {}: nothing was checked, and nothing was "
                      "claimed clean.".format(args.changed))
                return 0
            print("ERROR: no scannable files matched. Supported: {}"
                  .format(" ".join(sorted(SUPPORTED_SUFFIXES))), file=sys.stderr)
            return 2
        for f in files:
            checker.scan_file(f, root)
        checker.scan_corpus()
        label = "{} file(s)".format(len(files))

    return report(checker, args, label)


if __name__ == "__main__":
    sys.exit(main())
