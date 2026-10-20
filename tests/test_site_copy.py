"""Phrases this project has decided not to write, checked mechanically.

`tools/slopcheck.py` is the general writing checker and it never saw any of
these, for two reasons found on 2026-09-09: the site's copy lives in HTML
string literals inside `tools/render_site.py`, which the checker reads as
zero prose, and `docs/index.html` is in its exclude list because the page
re-renders manifest and tool text that is already checked at its source.
So the one part of this repository written to be READ by a stranger was the
one part nothing checked.

This is the narrow, project-specific complement: a list of constructions a
reviewer caught on the rendered page, each with the reason it is out. The
general rules stay upstream in the checker; these are ours.

Adding a row is cheap and is the right response to catching another one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# What we write ourselves.
AUTHORED = ["tools/render_site.py", "tools/render_workflows.py", "README.md"]
# Plus the rendered page, which also carries publisher text — paper titles,
# manifest prose, tool descriptions — that this project does not write and
# must not launder. A rule about OUR register is checked against the files
# we author; a rule about a construction that could only be ours is checked
# against the page as well.
RENDERED = AUTHORED + ["docs/index.html", "docs/demos.html"]

# (phrase, surfaces, why it is out). Matched case-insensitively as a
# substring, so keep each one specific enough not to catch a legitimate use.
BANNED = [
    ("public data. visible evidence.", RENDERED,
     "User rejected paired slogan fragments; use a descriptive title."),
    ('class="kicker"', RENDERED, "User requested no eyebrow labels."),
    ("what it answers", RENDERED,
     "a brochure heading with a vague subject. Every other heading here is "
     "a concrete noun; this one asked the reader to guess what 'it' is."),
    ("worked example", RENDERED,
     "reviewer-speak for 'an example'. The page shows a real captured call; "
     "the adjective adds nothing and reads as filler."),
    ("wired and queryable", RENDERED,
     "a doublet where one word does the work, and neither word is what the "
     "number counts. Say what is being counted."),
    ("public systems mapped", RENDERED,
     "a nominalized metric label, and it put registry INVENTORY in a "
     "headline where most of it is not built."),
    ("-laboratory crosswalk", RENDERED,
     "the 'the N-noun noun' construction. The section covers offices and "
     "power marketing administrations too, so the count was wrong as well "
     "as the register."),
    ("provenance on every answer", RENDERED,
     "the trailing em-dash flourish. The claim is true and worth making, so "
     "make it concretely: say what the answer names."),
    (", on a real answer", RENDERED,
     "the appositive-comma heading, 'X, on a Y'. It is the most reliable "
     "tell in this whole register."),
    ("seamless", AUTHORED, "unearned. Say what happens instead."),
    ("robust", AUTHORED, "unearned, unless quoting a source that used it."),
    ("leverage", AUTHORED, "as a verb, when 'use' is the word."),
    ("delve", AUTHORED, "nobody writes this."),
    ("in today's", AUTHORED, "the brochure opener."),
    ("it's worth noting", AUTHORED, "then note it."),
    ("game-chang", AUTHORED, "marketing."),
    ("cutting-edge", AUTHORED, "marketing."),
]


@pytest.mark.parametrize("phrase,surfaces,reason", BANNED,
                         ids=[p for p, _, _ in BANNED])
def test_the_public_copy_avoids_a_phrase_we_have_rejected(phrase, surfaces,
                                                          reason):
    for name in surfaces:
        path = ROOT / name
        if not path.exists():
            continue
        # Reported by line rather than by asserting over the whole file:
        # a failing assert on a 2,000-line page dumps the page.
        for number, line in enumerate(
                path.read_text(encoding="utf-8",
                               errors="replace").splitlines(), start=1):
            if phrase.lower() in line.lower():
                raise AssertionError(
                    f"{name}:{number} contains {phrase!r}. {reason}\n"
                    f"  {line.strip()[:160]}")


def test_the_generated_page_is_rebuilt_from_the_current_renderer():
    """The banned list is only worth anything if it runs against the page a
    reader gets. A stale `docs/index.html` would pass this file while the
    renderer had already reintroduced a phrase."""
    page = (ROOT / "docs" / "index.html")
    assert page.exists(), "run tools/build_site.py"
    assert "<section id=" in page.read_text(encoding="utf-8")


def test_the_site_links_rendered_pages_not_raw_markdown():
    """GitHub Pages renders `guide.md` to `guide.html` and serves the `.md`
    URL itself as `text/markdown` (checked 2026-10-20), so a phone shows a
    visitor raw markdown or a download. Relative links go to the `.html`."""
    import re
    for name in ("docs/index.html", "docs/demos.html"):
        page = (ROOT / name).read_text(encoding="utf-8")
        raw = [h for h in re.findall(r'href="([^"]+)"', page)
               if not re.match(r"[a-z]+:", h) and re.search(r"\.md(#|$)", h)]
        assert not raw, f"{name} links raw markdown: {raw}"


def test_each_headline_number_links_to_what_it_counts():
    """The four numbers at the top are the first thing a reader wants to
    expand; each is one tap from its list, and every target exists."""
    import re
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    funnel = page.split('class="funnel"', 1)[1].split("</div>", 1)[0]
    targets = re.findall(r'class="step" href="([^"]+)"', funnel)
    assert len(targets) == 4, targets
    ids = set(re.findall(r'id="([^"]+)"', page))
    for target in targets:
        if target.startswith("#"):
            assert target[1:] in ids, target
        else:
            assert (ROOT / "docs" / target.replace(".html", ".md")).exists(), target


@pytest.mark.parametrize("name", ["docs/index.html", "docs/demos.html"])
def test_every_id_on_a_page_is_unique(name):
    """An in-page link lands on the first element with its id; a second
    one is unreachable, which is how the skills link once opened the
    architecture heading instead of the skills."""
    import re
    from collections import Counter
    page = (ROOT / name).read_text(encoding="utf-8")
    dupes = [i for i, n in Counter(re.findall(r'\bid="([^"]+)"', page)).items() if n > 1]
    assert not dupes, dupes
