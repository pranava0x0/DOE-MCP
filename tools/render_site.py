"""Render docs/index.html from the generated site data.

Modelled on two working precedents, studied in research/10:

- **NEPA-MCP's Pages site** — the long-scroll shape with a sticky utility bar
  and anchor nav, a hero carrying the build inventory and a "works with"
  client strip, a numbered quick-start of copy-pasteable commands with real
  output beside them, and a card grid for a large inventory, grouped under
  <details> the reader opens rather than hidden behind a hover.
- **A sibling provenance server's site** — the sticky non-affiliation bar
  with a "view source" link, the coverage-and-warning decoder that teaches the envelope instead
  of only printing it, and status pills in matched ink/background pairs.

What this page adds that neither precedent has: an inventory that shows its
own `proposed` rows and what is blocking each one, and a 17-laboratory
crosswalk. Both fall out of the registry, which is why the page is generated
from it and a CI test fails the build when the two disagree.

Plain HTML and CSS in one file, no build framework, one small script for the
registry filter. The page's job is to let a stranger decide in two minutes
whether this is worth installing, and what decides that is the coverage
table — including what is NOT covered — plus one real annotated answer.
"""
from __future__ import annotations

import html
import json

STATE_LABEL = {"active": "active", "proposed": "inventory",
               "retired": "retired"}

# (anchor, label, [(sub-anchor, sub-label)]). The sub-items are headings
# inside a section, so the menu reaches a part of a collapsed section
# directly rather than only its top.
NAV = [
    ("overview", "Overview", [("overview-about", "About DOE-MCP"),
                              ("overview-queries", "Sample queries")]),
    ("start", "Quick start", [("start-install", "Install"),
                              ("start-check", "Check it"),
                              ("start-connect", "Connect a client"),
                              ("start-ask", "Ask something")]),
    ("servers", "Servers", [("servers-arch", "Architecture"),
                            ("servers-list", "Server list")]),
    ("answer", "Response format", [("answer-fields", "Field by field"),
                                   ("answer-more", "More calls")]),
    ("registry", "Data registry", [("registry-orgs", "By organization"),
                                   ("registry-sources", "By source")]),
    ("neighbours", "Other servers", []),
    ("governance", "Governance", []),
]

CLIENTS = ["CLAUDE CODE", "CLAUDE DESKTOP", "VS CODE", "CURSOR"]


def e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _style() -> str:
    # Palette from energy.gov's own design tokens, read from its
    # stylesheets on 2026-09-09: the named brand colours Absolute Zero
    # (#003ecc), Lightning Green (#26a769) and Sunglow (#ffd23f) over the
    # DOE grey ramp. Type is Inter for text and Crimson Pro for display —
    # the pairing NEPA-MCP's own Pages site uses — kept because a serif
    # display face against a plain grotesk body is doing real work
    # (product name vs. everything else), not because it is theirs.
    #
    # Colours and type, and deliberately nothing else. No seal, no logo, no
    # wordmark, no eagle: those identify the department and this project is
    # not it. Decision 0016 put the non-affiliation notice in a bar above
    # the masthead rather than in a footer, and looking at home in the
    # ecosystem is the reason that bar has to stay where it is. The same
    # reasoning keeps this page off Tailwind, flip-cards and an analytics
    # tag: those are NEPA-MCP's own vendor's marketing site, not the part of
    # NEPA-MCP this project borrowed the shape from, and flip-cards were
    # tried here once (see the card comment below) and reverted.
    #
    # The web fonts have full system fallbacks and load with `display=swap`,
    # so the page is legible and correctly laid out before they arrive and
    # if they never do.
    return """
:root{
  /* DOE grey ramp */
  --paper:#f9faff; --ink:#232429; --muted:#55565b; --faint:#828388;
  --line:#dedfe4; --card:#fff; --panel:#f3f4f9;
  /* Absolute Zero, the department's link and primary blue */
  --accent:#003ecc; --accent-soft:#e5edff; --accent-rgb:0,62,204;
  --navy:#001f66;
  /* Lightning Green, its focus and success colour */
  --ok:#1c7a4d; --ok-bg:#e6f4ed; --lightning:#26a769;
  /* Sunglow */
  --warn:#7a5300; --warn-bg:#fff6da; --sunglow:#ffd23f;
  --dim-bg:#f3f4f9;
  --measure:64rem; --pad:1.5rem;
  --sans:"Inter",BlinkMacSystemFont,-apple-system,"Segoe UI",Roboto,
         "Helvetica Neue",Arial,sans-serif;
  --display:"Crimson Pro","Crimson Text",Georgia,Cambria,
            "Times New Roman",serif;
  --mono:"Roboto Mono","Roboto Mono Web",ui-monospace,SFMono-Regular,
         Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.62 var(--sans);
     -webkit-font-smoothing:antialiased}
.wrap{max-width:var(--measure);margin:0 auto;padding:0 var(--pad)}

@keyframes fadeInUp{from{opacity:0;transform:translateY(10px)}
  to{opacity:1;transform:translateY(0)}}
.funnel .step{animation:fadeInUp .5s cubic-bezier(.16,1,.3,1) both}
.funnel .step:nth-child(3){animation-delay:.06s}
.funnel .step:nth-child(5){animation-delay:.12s}
.funnel .step:nth-child(7){animation-delay:.18s}
/* A section's body fades in the moment it opens, so paging through the
   report reads as responsive rather than an instant snap — CSS only, tied
   to the <details> the page already uses for disclosure, no scroll
   listener or IntersectionObserver needed. */
.sect[open]>.sect-body{animation:fadeInUp .45s cubic-bezier(.16,1,.3,1) both}

/* sticky utility bar: the disclaimer is not a footnote */
.util{background:var(--navy);color:#fff;font-size:.82rem}
.util-inner{max-width:var(--measure);margin:0 auto;padding:.45rem var(--pad);
  display:flex;gap:1rem;align-items:center;justify-content:space-between}
.util a{color:#ccdbff;text-decoration:none;white-space:nowrap}
.util a:hover{text-decoration:underline}
.util .short{display:none}
nav.toc{position:sticky;top:0;z-index:10;background:var(--card);
  border-bottom:1px solid var(--line)}
.toc-inner{max-width:var(--measure);margin:0 auto;padding:.55rem var(--pad);
  display:flex;align-items:center;gap:.9rem;justify-content:space-between}
.toc-brand{font-weight:700;font-size:.95rem;color:var(--ink);text-decoration:none;
  letter-spacing:-.01em;flex-shrink:0}
.toc-links{display:flex;gap:.2rem;overflow-x:auto;scrollbar-width:none}
.toc-links::-webkit-scrollbar{display:none}
.toc-item{position:relative}
.toc-item>a{display:block;color:var(--muted);text-decoration:none;
  font-size:.83rem;white-space:nowrap;padding:.35rem .5rem;border-radius:4px}
.toc-item>a:hover,.toc-item:focus-within>a{color:var(--accent);
  background:var(--accent-soft)}
/* Sub-tabs: the headings inside a section, so the menu reaches a part of a
   collapsed section rather than only its top. Shown on hover AND on
   focus-within, so the keyboard reaches them too. */
.toc-sub{display:none;position:absolute;top:100%;left:0;z-index:20;
  background:var(--card);border:1px solid var(--line);border-radius:6px;
  padding:.3rem;min-width:11rem;box-shadow:0 6px 18px rgba(0,31,102,.08)}
.toc-item:hover .toc-sub,.toc-item:focus-within .toc-sub{display:block}
.toc-sub a{display:block;padding:.3rem .5rem;font-size:.8rem;
  color:var(--muted);text-decoration:none;border-radius:4px;
  white-space:nowrap}
.toc-sub a:hover,.toc-sub a:focus{color:var(--accent);
  background:var(--accent-soft)}

header{padding:3rem 0 2.25rem;border-bottom:1px solid var(--line);
  background:linear-gradient(135deg,var(--paper) 0%,#fff 55%,
    var(--accent-soft) 130%)}
h1{font-family:var(--display);font-weight:600;font-size:2.3rem;
   margin:0 0 .6rem;letter-spacing:-.01em;line-height:1.2}
.tagline{font-size:1.08rem;color:var(--muted);margin:0 0 1.6rem;max-width:44rem}
.funnel{display:flex;flex-wrap:wrap;gap:.4rem;align-items:stretch;
  margin:0 0 .6rem}
.funnel .step{background:var(--card);border:1px solid var(--line);
  border-radius:5px;padding:.5rem .8rem;min-width:7rem;
  transition:transform .3s cubic-bezier(.16,1,.3,1),
    box-shadow .3s cubic-bezier(.16,1,.3,1),border-color .3s}
.funnel .step:hover{transform:translateY(-3px);
  box-shadow:0 10px 28px rgba(var(--accent-rgb),.14);
  border-color:rgba(var(--accent-rgb),.3)}
.funnel .step b{display:block;font-size:1.45rem;line-height:1.15;
  font-variant-numeric:tabular-nums;letter-spacing:-.02em;color:var(--accent)}
.funnel .step span{color:var(--faint);font-size:.76rem;display:block;
  margin-top:.1rem}
.funnel .arrow{align-self:center;color:var(--lightning);font-size:1.05rem}
.inventory{font-size:.85rem;color:var(--muted);margin:0 0 1.1rem}

/* Collapsible groups. The page is a reference and most readers want one
   part of it, so the long card grids and the 83-row registry table are
   closed until asked for. <details> rather than script: it works with
   JavaScript off, it is keyboard-operable and screen-reader-announced
   without any ARIA, and the browser's own find-in-page opens it. */
details:not(.sect){border:1px solid var(--line);border-radius:6px;
  background:var(--card);margin:.55rem 0}
details:not(.sect)[open]{background:transparent}
summary{cursor:pointer;padding:.6rem .8rem;font-weight:600;font-size:.92rem;
  list-style:none;display:flex;gap:.6rem;align-items:baseline;
  border-radius:6px}
summary::-webkit-details-marker{display:none}
details:not(.sect)>summary::before{content:"\u25b8";color:var(--faint);
  font-size:.8em;transition:transform .15s}
details[open]:not(.sect)>summary::before{transform:rotate(90deg);
  display:inline-block}
summary:hover{color:var(--accent)}
summary:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
summary .count{color:var(--faint);font-weight:400;font-size:.8rem;
  font-variant-numeric:tabular-nums}
details:not(.sect)>.grid,details:not(.sect)>table,
details:not(.sect)>div{margin:.2rem .8rem .8rem}
.works{font-family:var(--mono);font-size:.72rem;letter-spacing:.07em;
  color:var(--faint);text-transform:uppercase}
.works b{color:var(--muted);font-weight:600}

section{border-bottom:1px solid var(--line);scroll-margin-top:4.2rem}
/* Every section collapses. Closed by default but for the first, because
   this page is a reference and a reader wants one part of it. <details>
   rather than script: keyboard-operable and announced without any ARIA,
   and the browser's own find-in-page opens it. */
.sect>summary{list-style:none;cursor:pointer;display:flex;
  align-items:baseline;gap:.6rem;padding:1rem 0}
.sect>summary::-webkit-details-marker{display:none}
.sect>summary::after{content:"▾";margin-left:auto;color:var(--faint);
  font-size:.95rem;transition:transform .15s}
.sect[open]>summary::after{transform:rotate(180deg)}
.sect>summary:hover h2{color:var(--accent)}
.sect>summary:focus-visible{outline:2px solid var(--accent);
  outline-offset:2px}
.sect>summary h2{display:inline;margin:0}
.sect-body{padding:0 0 1.9rem}
h3[id]{scroll-margin-top:4.5rem}
h2{font-family:var(--display);font-weight:700;font-size:1.28rem;
   margin:0 0 .5rem;letter-spacing:-.01em}
h2 .num{color:var(--faint);font-weight:400;margin-right:.5rem;
  font-variant-numeric:tabular-nums;font-size:.9em}
h3{font-size:.98rem;margin:1.6rem 0 .5rem}
.lede{color:var(--muted);margin:0 0 1.1rem;max-width:45rem}

table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{text-align:left;padding:.5rem .6rem;border-bottom:1px solid var(--line);
  vertical-align:top}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;
  color:var(--faint);font-weight:600}
code,.mono{font-family:var(--mono);font-size:.86em}
.tool{color:var(--accent);white-space:nowrap}
pre{background:var(--panel);border:1px solid var(--line);border-radius:6px;
  padding:.8rem .9rem;overflow-x:auto;font-family:var(--mono);font-size:.78rem;
  line-height:1.55;margin:.5rem 0}
pre.out{background:var(--card);color:var(--muted)}

.pill{display:inline-block;font-size:.67rem;text-transform:uppercase;
  letter-spacing:.05em;padding:.1rem .42rem;border-radius:3px;font-weight:700;
  white-space:nowrap}
.pill.active,.pill.shipping{background:var(--ok-bg);color:var(--ok)}
.pill.proposed,.pill.planned{background:var(--dim-bg);color:var(--faint)}
.pill.outreach{background:var(--warn-bg);color:var(--warn)}

/* Cards show their contents. They used to be flip-cards: a front face for
   scanning and a back face that appeared on hover. That hid the part a
   reader came for behind a gesture no touch device has, kept it out of the
   browser's find-in-page, and made the card a fixed height whatever it
   held. The grouping <details> above them is where scroll length is
   managed now, which is a control the reader operates deliberately. */
.grid{display:grid;gap:.7rem;
  grid-template-columns:repeat(auto-fill,minmax(17rem,1fr))}
.card{border:1px solid var(--line);border-radius:6px;background:var(--card);
  padding:.75rem .85rem;display:flex;flex-direction:column;gap:.35rem;
  transition:transform .3s cubic-bezier(.16,1,.3,1),
    box-shadow .3s cubic-bezier(.16,1,.3,1),border-color .3s}
.card:hover{transform:translateY(-4px);
  box-shadow:0 12px 32px rgba(0,31,102,.10);
  border-color:rgba(var(--accent-rgb),.28)}
.card h4{margin:0;font-size:.9rem;letter-spacing:-.01em}
.card .sub{font-size:.74rem;color:var(--faint);margin:0}
.card .foot{font-size:.72rem;color:var(--faint);
  font-variant-numeric:tabular-nums}
.card ul{margin:.15rem 0 0;padding-left:1rem;font-size:.78rem;
  color:var(--muted)}
.card li{margin:.18rem 0}
.card .more{font-size:.74rem;color:var(--faint)}
.hint{font-size:.78rem;color:var(--faint);margin:0 0 .8rem}

details:not(.sect){border:1px solid var(--line);border-radius:6px;
  margin:.35rem 0;background:var(--card)}
details>summary{cursor:pointer;padding:.55rem .8rem;font-size:.9rem;list-style:none;
  display:flex;gap:.55rem;align-items:baseline}
details>summary::-webkit-details-marker{display:none}
details:not(.sect)>summary::before{content:"\u25b8";color:var(--faint)}
details[open]:not(.sect)>summary::before{content:"\u25be"}
details .body{padding:0 .8rem .75rem 1.85rem;font-size:.86rem;color:var(--muted)}
.count{margin-left:auto;color:var(--faint);font-size:.78rem;
  font-variant-numeric:tabular-nums}

.annot{display:grid;grid-template-columns:9.5rem 1fr;gap:.3rem .9rem;
  font-size:.85rem;margin:1rem 0 0}
.annot dt{font-family:var(--mono);color:var(--accent);font-size:.79rem}
.annot dd{margin:0;color:var(--muted)}
.note{background:var(--panel);border-left:3px solid var(--line);
  padding:.65rem .85rem;font-size:.86rem;color:var(--muted);margin:1rem 0}
.note.warn{background:var(--warn-bg);border-left-color:var(--warn);color:var(--warn)}
.steps{counter-reset:s;list-style:none;padding:0;margin:0}
.steps>li{counter-increment:s;position:relative;padding-left:2rem;margin:0 0 1.2rem}
.steps>li::before{content:counter(s);position:absolute;left:0;top:.1rem;
  width:1.4rem;height:1.4rem;border-radius:50%;background:var(--accent-soft);
  color:var(--accent);font-size:.78rem;font-weight:700;display:grid;place-items:center}
.steps h3{margin:0 0 .3rem}
.arch-flow{margin:1.4rem 0 2rem;display:flex;flex-direction:column;gap:1.2rem}
.arch-tier{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:1.1rem 1.2rem}
.arch-tier-title{font-family:var(--mono);font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);margin:0 0 .8rem;font-weight:600}
.arch-boxes{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.8rem}
.arch-box{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:.8rem .9rem;box-shadow:0 1px 3px rgba(0,0,0,.03)}
.arch-box-title{font-size:.88rem;font-weight:600;margin:0 0 .3rem}
.arch-box-desc{font-size:.76rem;color:var(--muted);margin:0;line-height:1.4}
.arch-arrow{display:flex;justify-content:center;color:var(--faint);font-size:1.2rem;margin:-.3rem 0}
#q{width:100%;padding:.5rem .7rem;border:1px solid var(--line);border-radius:5px;
  font:inherit;font-size:.88rem;margin:0 0 .8rem;background:var(--card)}
tr[hidden]{display:none}
footer{padding:2.5rem 0 4rem;font-size:.84rem;color:var(--faint)}
footer p{margin:.4rem 0}
a{color:var(--accent)}
@media(max-width:44rem){
  .annot{grid-template-columns:1fr}
  .annot dt{margin-top:.5rem}
  .util .long{display:none}
  .util .short{display:inline}
  h1{font-size:1.8rem}
}
@media(prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  .funnel .step,.sect[open]>.sect-body{animation:none}
  .funnel .step,.card{transition:none}
}
"""


def _inventory(c: dict) -> str:
    """What is built, as the pipeline it actually is.

    The steps are this project's own layering: public systems fan into
    domain servers, which expose read-only tools, which a skill composes
    into a walk. Every step is a thing that exists and every number is
    counted from the tree.

    Three earlier steps are gone and stay gone, because they were not
    measurements. Counting the registry's whole inventory put unbuilt
    sources in the headline, where they overstate what ships; that count
    belongs in the registry section, whose subject it is. Counting the
    protocols is a slogan — there is one MCP, and the number tells a reader
    nothing they can act on. And "labs, offices, PMAs" under a single count
    was three things sharing one number; they get their own line below.
    """
    skills = c["skills"]
    steps = [(c["active"], "live systems"),
             (c["servers_shipping"], "domain servers"),
             (c["tools"], "read-only tools"),
             (skills, f"skill{'s' if skills != 1 else ''}")]
    parts = []
    for i, (big, small) in enumerate(steps):
        if i:
            parts.append('<span class="arrow">\u2192</span>')
        parts.append(f'<div class="step"><b>{big}</b>'
                     f'<span>{e(small)}</span></div>')
    return (f'<div class="funnel">{"".join(parts)}</div>'
            f'<p class="inventory">Published by {c["labs"]} national '
            f'laboratories, {c["program_offices"]} program offices and '
            f'{c["pmas"]} power marketing administrations.</p>')


def _crosswalk(data: dict) -> str:
    """Every organization, grouped by what kind of thing it is.

    Collapsed per group because forty-one cards is a scroll, and the groups
    are what a reader is choosing between: a laboratory publishes data, a
    program office funds it, a power marketing administration operates a
    grid. Open by default only for the laboratories, which is where most of
    the data is.
    """
    groups = []
    for kind, label in [("national_lab", "National laboratories"),
                        ("program_office", "Program offices"),
                        ("power_marketing_administration",
                         "Power marketing administrations"),
                        ("headquarters", "Headquarters")]:
        rows = [o for o in data["crosswalk"] if o["kind"] == kind]
        if not rows:
            continue
        carrying = sum(1 for o in rows if o["sources"])
        groups.append(
            f'<details{" open" if kind == "national_lab" else ""}>'
            f'<summary>{e(label)} <span class="count">{len(rows)}, '
            f'{carrying} carrying data</span></summary>'
            f'{_lab_cards(data, kind)}</details>')
    return "".join(groups)


def _questions(data: dict) -> str:
    rows = "".join(
        f"<tr><td>{e(q['question'])}</td>"
        f"<td><code class='tool'>{e(q['tool'])}</code></td>"
        f"<td class='mono'>{e(q['server'])}</td></tr>"
        for q in data["questions"])
    return ("<table><thead><tr><th>Question</th><th>Tool</th><th>Server</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>")


def _quickstart(data: dict, examples: list) -> str:
    """Real commands with the output they actually produce."""
    counts = data["counts"]
    probe_out = "\n".join(
        f"[  ok  ] {s['id']}: {s['record_count']:,} records"
        for s in data["sources"]
        if s["state"] == "active" and s["record_count"])[:900]
    first = examples[0]["envelope"] if examples else None
    sample = ""
    if first:
        rec = (first["data"].get("records") or [{}])[0]
        sample = json.dumps({k: rec[k] for k in ("title", "doi",
                                                 "publication_date")
                             if k in rec}, indent=2)
    return f"""
<ol class="steps">
<li id="start-install"><h3>Install</h3>
<pre>pipx install .        # from a checkout of the repository; not on PyPI yet</pre></li>

<li id="start-check"><h3>Check it</h3>
<pre>doe-mcp doctor --online</pre>
<pre class="out">{e(probe_out)}</pre></li>

<li id="start-connect"><h3>Connect a client</h3>
<pre>doe-mcp configure claude-code        # or claude-desktop, vscode, cursor
doe-mcp configure claude-code --all  # every server at once</pre>
<p class="lede" style="margin:.4rem 0 0">When a source requires an API key (such as EIA), run <code>doe-mcp configure credentials</code> to store it locally in <code>~/.config/doe-mcp/credentials.env</code> with restricted file permissions. Keys stay on your machine and are never written into client configuration files or shared repositories.</p></li>

<li id="start-ask"><h3>Ask something</h3>
<pre>doe-mcp tools call research.search_literature \\
  --args '{{"query":"perovskite tandem solar cell","year_from":2024}}'</pre>
<pre class="out">{e(sample)}</pre>
<p class="lede" style="margin:.4rem 0 0">The full answer carries provenance,
evidence, five coverage dimensions, and typed warnings — see
<a href="#answer">Response format</a>.</p></li>
</ol>
<p class="note">No credential is needed for any of this.
{counts['records_reachable']:,} literature, dataset, and software records are
reachable keyless.</p>
"""


def _arch_diagram(data: dict) -> str:
    """A clean, styled architecture diagram explaining the layers."""
    return """
<div class="arch-flow" id="servers-arch">
  <div class="arch-tier">
    <div class="arch-tier-title">Layer 1: Agent Workflows &amp; Skills</div>
    <div class="arch-boxes">
      <div class="arch-box">
        <div class="arch-box-title">find-doe-data</div>
        <div class="arch-box-desc">Identifies whether DOE publishes data on a topic, resolves lab naming changes, and locates active endpoints.</div>
      </div>
      <div class="arch-box">
        <div class="arch-box-title">grid-status-brief</div>
        <div class="arch-box-desc">Synthesizes balancing authority demand and 5-minute grid operations with preliminary-data caveats.</div>
      </div>
      <div class="arch-box">
        <div class="arch-box-title">energy-project-site-screen</div>
        <div class="arch-box-desc">First-pass screen of existing wind/solar assets and grid demand surrounding a prospective site.</div>
      </div>
      <div class="arch-box">
        <div class="arch-box-title">materials-structure-and-code</div>
        <div class="arch-box-desc">Explores computed inorganic crystal structures and exports quantum-chemistry basis sets.</div>
      </div>
    </div>
  </div>

  <div class="arch-arrow">&#8595;</div>

  <div class="arch-tier">
    <div class="arch-tier-title">Layer 2: Shipping MCP Servers (Profiles: 8&ndash;12 tools default, &le;20 ceiling)</div>
    <div class="arch-boxes">
      <div class="arch-box">
        <div class="arch-box-title"><code class="tool">doe-research</code></div>
        <div class="arch-box-desc">11 tools: literature, datasets, software, rulemakings, patents, lab crosswalk, and catalog fan-out.</div>
      </div>
      <div class="arch-box">
        <div class="arch-box-title"><code class="tool">doe-energy-data</code></div>
        <div class="arch-box-desc">10 tools: EIA-930 grid demand, BPA operations, wind/solar facility screening, and vehicle fuel economy.</div>
      </div>
      <div class="arch-box">
        <div class="arch-box-title"><code class="tool">doe-earth</code></div>
        <div class="arch-box-desc">8 tools: Daymet single-pixel daily weather, ESS-DIVE field datasets, ESGF CMIP6 climate models, and Sage sensor nodes.</div>
      </div>
      <div class="arch-box">
        <div class="arch-box-title"><code class="tool">doe-materials</code></div>
        <div class="arch-box-desc">7 tools: Materials Project OPTIMADE crystal structures and Basis Set Exchange quantum-chemistry sets.</div>
      </div>
    </div>
  </div>

  <div class="arch-arrow">&#8595;</div>

  <div class="arch-tier">
    <div class="arch-tier-title">Layer 3: Core Registry, Adapters &amp; Structured Responses</div>
    <div class="arch-boxes">
      <div class="arch-box">
        <div class="arch-box-title">Source Registry (CC0)</div>
        <div class="arch-box-desc">83 source manifests, 22 active, 54 organizations. Validated activation gates and blocked reasons.</div>
      </div>
      <div class="arch-box">
        <div class="arch-box-title">17 Publisher Adapters</div>
        <div class="arch-box-desc">Read-only GET-only clients translating publisher dialects and enforcing concurrency and pagination invariants.</div>
      </div>
      <div class="arch-box">
        <div class="arch-box-title">Structured Provenance Response</div>
        <div class="arch-box-desc">Consistent response with 5-part contract: data payload, source provenance, record citations, 5-dimensional coverage, and typed warnings.</div>
      </div>
    </div>
  </div>
</div>
"""


def _server_cards(data: dict) -> str:
    cards = []
    for s in data["servers"]:
        tools = s["tools"]
        back = ("<ul>" + "".join(
            f"<li><code>{e(t['name'])}</code></li>" for t in tools)
            + "</ul>") if tools else (
            "<p class='more'>Not built. The sources it would serve are in "
            "the registry, each with what is blocking it.</p>")
        cards.append(f"""
<div class="card">
  <h4><code>{e(s['name'])}</code></h4>
  <p class="sub">{e(s['description'])}</p>
  <div class="foot"><span class="pill {e(s['status'])}">{e(s['status'])}</span>
    &nbsp;{len(tools)} tools</div>
  {back}
</div>""")
    return f'<div class="grid">{"".join(cards)}</div>'


def _lab_cards(data: dict, kind: str = "") -> str:
    cards = []
    for lab in data["crosswalk"]:
        if kind and lab["kind"] != kind:
            continue
        rows = "".join(
            f"<li><code>{e(s['id'])}</code> "
            f"<span class='pill {e(s['state'])}'>"
            f"{e(STATE_LABEL.get(s['state'], s['state']))}</span></li>"
            for s in lab["sources"][:7])
        more = (f"<li>+{len(lab['sources']) - 7} more</li>"
                if len(lab["sources"]) > 7 else "")
        former = (f"<p class='sub'>formerly {e(', '.join(lab['former_names']))}"
                  + (f" · renamed {e(lab['renamed_on'])}"
                     if lab["renamed_on"] else "") + "</p>"
                  ) if lab["former_names"] else ""
        cards.append(f"""
<div class="card">
  <h4>{e(lab['name'])}</h4>
  {former}
  <div class="foot">{len(lab['sources'])} sources · {lab['active']} active</div>
  <ul>{rows}{more}</ul>
</div>""")
    return f'<div class="grid">{"".join(cards)}</div>'


def _registry(data: dict) -> str:
    rows = []
    for s in data["sources"]:
        pill = ("active" if s["state"] == "active"
                else "outreach" if s["automation_status"] == "outreach_pending"
                else "proposed")
        label = ("active" if s["state"] == "active"
                 else "outreach pending"
                 if s["automation_status"] == "outreach_pending"
                 else "inventory")
        publisher = e(s["steward"])
        for role in ("funder", "host"):
            if s[role]:
                publisher += (f"<br><span class='mono' "
                              f"style='color:var(--faint)'>{role}: "
                              f"{e(s[role])}</span>")
        detail = e(s["scope"])
        if s["blocked_reason"]:
            detail += (f"<br><span style='color:var(--warn)'>Blocked: "
                       f"{e(s['blocked_reason'])}</span>")
        count = f"{s['record_count']:,}" if s["record_count"] else "—"
        haystack = e(" ".join([s["id"], s["name"], s["domain"], s["steward"],
                               " ".join(s["labs"]), label]).lower())
        rows.append(
            f"<tr data-s=\"{haystack}\"><td class='mono'>{e(s['id'])}<br>"
            f"<span class='pill {pill}'>{e(label)}</span></td>"
            f"<td>{publisher}</td><td>{detail}</td>"
            f"<td class='mono' style='text-align:right'>{count}</td></tr>")
    return (
        '<input id="q" type="search" placeholder="Filter '
        f'{len(data["sources"])} sources — try &quot;ornl&quot;, '
        '&quot;inventory&quot;, &quot;grid&quot;, &quot;outreach&quot;" '
        'aria-label="Filter sources">'
        "<table><thead><tr><th>Source</th><th>Publisher</th>"
        "<th>What it holds / what is blocking it</th>"
        "<th style='text-align:right'>Records</th></tr></thead>"
        f"<tbody id='reg'>{''.join(rows)}</tbody></table>")


ANNOTATIONS = [
    ("data", "The answer. Held to a soft budget of about 2,000 tokens, so ten "
             "records arrive as ten readable answers rather than one "
             "truncated blob."),
    ("provenance", "One entry per system that contributed, with the publisher "
                   "triple — steward, funder, host — because at least five "
                   "flagship “lab assets” are funded by another agency "
                   "entirely."),
    ("dataset_version", "Required, never blank. ATB 2024 and ATB 2026 are "
                        "different data with the same name, and two versions "
                        "in one answer raise <code>mixed_vintages</code>."),
    ("evidence", "One entry per record, pointing back at a provenance entry "
                 "and carrying the locator the publisher actually supplied. A "
                 "guessed link is worse than none, so none is emitted."),
    ("coverage.registry", "The dimension that matters most. "
                          "<code>none</code> means DOE-MCP has no source and "
                          "the data may well exist; <code>covered</code> with "
                          "an empty result means the searched systems hold no "
                          "record. Different answers, never reported the "
                          "same way."),
    ("coverage.pagination", "<code>truncated</code> whenever more matched "
                            "than you received. OSTI returns its match count "
                            "in a header and one page in the body; reading "
                            "the page as the answer turns 4.4 million matches "
                            "into “20 results”."),
    ("warnings", "Typed, not prose. <code>alias_match</code>, "
                 "<code>domain_migrated</code>, <code>catalog_vintage</code>, "
                 "<code>derived_layer</code>, "
                 "<code>citation_required</code> — each named for a confusion "
                 "this ecosystem measurably produces."),
    ("access_recipes", "Typed pointers for data too big to inline: a DOI, an "
                       "S3 prefix, an OPeNDAP URL, a Globus endpoint. A "
                       "4.8-petabyte lake gets a recipe, not an attempt."),
    ("_execution", "Which server, which tool contract, which adapter "
                   "versions, which registry revision. Enough to reproduce "
                   "the call."),
]


def _provenance(data: dict) -> str:
    examples = data.get("examples") or []
    if not examples:
        return ("<p class='note warn'>No example captured. Run "
                "<code>python tools/build_site.py --fixtures</code>.</p>")
    first = examples[0]
    body = json.dumps(first["envelope"], indent=2)
    if len(body) > 4000:
        body = body[:4000] + "\n  … trimmed for the page …\n}"
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in ANNOTATIONS)
    others = "".join(
        f"<details><summary><code class='tool'>{e(x['tool'])}</code>"
        f"<span class='count'>{e(x.get('server') or 'response')}</span>"
        "</summary>"
        f"<div class='body'><p>{e(x['why'])}</p>"
        f"<pre>{e(json.dumps(x['envelope'], indent=2)[:2800])}…</pre>"
        f"</div></details>" for x in examples[1:])
    return (f"<p class='note'><b><code>{e(first['tool'])}</code></b> — "
            f"{e(first['why'])} A real response, captured at build time by "
            "replaying recorded publisher bytes; the replay's timestamps are "
            "pinned to the registry revision date and the request id is "
            "fixed, so the page rebuilds identically.</p>"
            f"<pre>{e(body)}</pre><dl class='annot' id='answer-fields'>{dl}</dl>"
            f"<h3 id='answer-more'>More calls, one from each shipping "
            f"server</h3>{others}")


def _neighbours(data: dict) -> str:
    rows = "".join(
        f"<tr><td><b>{e(n['name'])}</b><br><span class='mono' "
        f"style='color:var(--faint)'>{e(n['maintainer'])}</span></td>"
        f"<td><span class='pill "
        f"{'active' if n['status'] == 'listed' else 'proposed'}'>"
        f"{e(n['status'])}</span><br><span class='mono' "
        f"style='color:var(--faint)'>{e(n['compliance'])}</span></td>"
        f"<td>{e(n['why_not_absorbed'])}</td>"
        f"<td><code>{e(n['install'] or '—')}</code></td></tr>"
        for n in data["neighbors"])
    return ("<table><thead><tr><th>Server</th><th>Status</th>"
            "<th>Why DOE-MCP does not duplicate it</th><th>Install</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>")


def _script() -> str:
    return """
(function(){
  var q = document.getElementById('q');
  if(!q) return;
  var rows = Array.prototype.slice.call(
    document.querySelectorAll('#reg tr'));
  q.addEventListener('input', function(){
    var terms = q.value.toLowerCase().split(/\\s+/).filter(Boolean);
    rows.forEach(function(r){
      var hay = r.getAttribute('data-s') || '';
      r.hidden = !terms.every(function(t){ return hay.indexOf(t) !== -1; });
    });
  });
})();
"""


def render(data: dict) -> str:
    c = data["counts"]
    nav = "".join(
        f'<div class="toc-item"><a href="#{i}">{e(label)}</a>'
        + (('<div class="toc-sub">'
            + "".join(f'<a href="#{si}">{e(sl)}</a>' for si, sl in subs)
            + '</div>') if subs else "")
        + '</div>'
        for i, label, subs in NAV)
    works = " · ".join(f"<b>{e(x)}</b>" for x in CLIENTS)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DOE-MCP — public DOE data over the Model Context Protocol</title>
<meta name="description" content="MCP servers over public US Department of
 Energy and national-laboratory data. Every answer names the systems it came
 from and what it did not cover. An independent project, not affiliated with
 the US Department of Energy.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?\
family=Inter:wght@400;500;600;700&family=Crimson+Pro:wght@400;600&\
family=Roboto+Mono:wght@400;500&display=swap">
<style>{_style()}</style>
</head>
<body>

<div class="util" id="top"><div class="util-inner">
  <p style="margin:0">
    <span class="long"><b>Not affiliated with the US Department of
    Energy.</b> An independent project. No answer it returns is an official
    government statement.</span>
    <span class="short">⚠ Not affiliated with the US DOE</span></p>
  <a href="https://github.com/pranava0x0/DOE-MCP">View source ↗</a>
</div></div>

<nav class="toc"><div class="toc-inner">
  <a class="toc-brand" href="#top">DOE-MCP</a>
  <div class="toc-links">{nav}</div>
</div></nav>

<header><div class="wrap">
  <h1>MCP servers over public DOE data</h1>
  <p class="tagline">Public Department of Energy and national-laboratory
  data, reachable by an agent. Every answer names the systems it came from,
  when they were read, and what it did not cover.</p>
  {_inventory(c)}
  <p class="works">Works with: {works}</p>
</div></header>

<div class="wrap">

<section id="overview">
<details class="sect" open>
  <summary><h2><span class="num">01</span>Overview</h2></summary>
  <div class="sect-body">
  <h3 id="overview-about">What is MCP and why use DOE-MCP?</h3>
  <p class="lede">The <b>Model Context Protocol (MCP)</b> is an open standard that allows AI assistants—such as Claude Code, Claude Desktop, Cursor, and VS Code—to securely connect to external tools and live data systems without custom one-off integrations.</p>
  <p class="lede">The US Department of Energy and its 17 national laboratories publish large collections of scientific literature, power grid metrics, climate simulations, and materials data. However, these datasets are distributed across separate portals, REST APIs, and catalog formats. <b>DOE-MCP</b> provides curated, read-only MCP servers that connect AI assistants directly to these public resources, returning structured results with citations, data vintages, and coverage indicators.</p>
  <h3 id="overview-queries">Sample queries</h3>
  <p class="lede">Common questions an AI assistant can resolve using the active tools:</p>
  {_questions(data)}
  </div>
</details>
</section>

<section id="start">
<details class="sect">
  <summary><h2><span class="num">02</span>Quick start</h2></summary>
  <div class="sect-body">
  <p class="lede">Install, verify, and connect to an MCP client in four steps. No API keys are required for default operations.</p>
  {_quickstart(data, data.get("examples") or [])}
  </div>
</details>
</section>

<section id="servers">
<details class="sect">
  <summary><h2><span class="num">03</span>Servers</h2></summary>
  <div class="sect-body">
  <p class="lede">Four domain servers provide focused tools for research literature, power grid operations, earth systems, and materials science. {c['servers_shipping']} servers are active today, with {c['servers_planned']} additional servers planned.</p>
  <h3 id="servers-arch">Architecture</h3>
  {_arch_diagram(data)}
  <h3 id="servers-list">Server list</h3>
  {_server_cards(data)}
  </div>
</details>
</section>

<section id="answer">
<details class="sect">
  <summary><h2><span class="num">04</span>Response format</h2></summary>
  <div class="sect-body">
  <p class="lede">Every tool returns a consistent JSON response containing query results, data citations, vintage timestamps, and coverage indicators. Below is an annotated real response:</p>
  {_provenance(data)}
  </div>
</details>
</section>

<section id="registry">
<details class="sect">
  <summary><h2><span class="num">05</span>Data registry</h2></summary>
  <div class="sect-body">
  <p class="lede">The CC0 registry indexes {c['sources']} data systems across {c['organizations']} national laboratories and offices. You can browse by publishing organization to see which facilities steward each system, or search the complete catalog of {c['active']} active sources and planned integrations with notes on automation status and access protocols.</p>
  <h3 id="registry-orgs">By organization</h3>
  {_crosswalk(data)}
  <h3 id="registry-sources">By source</h3>
  <details><summary>All {c['sources']} systems
    <span class="count">{c['active']} queryable</span></summary>
  {_registry(data)}</details>
  </div>
</details>
</section>

<section id="neighbours">
<details class="sect">
  <summary><h2><span class="num">06</span>Other MCP servers</h2></summary>
  <div class="sect-body">
  <p class="lede">DOE-MCP is not the only MCP server over DOE-adjacent data
  and does not try to be. MCP offers no mechanism for one server to advertise
  or install another (checked against the protocol on 2026-09-01), so these
  carry executable install commands rather than links — and each says plainly
  why this project does not duplicate it.</p>
  {_neighbours(data)}
  </div>
</details>
</section>

<section id="governance">
<details class="sect">
  <summary><h2><span class="num">07</span>Licensing and governance</h2></summary>
  <div class="sect-body">
  <p class="lede">Code Apache-2.0. <b>The source registry is CC0</b> — the
  inventory of what exists, who stewards it, what its terms say, and what it
  does not cover is more useful unencumbered than as anything this project
  owns. Documentation CC-BY-4.0. Recorded publisher responses keep their
  publishers' terms, itemised in <code>THIRD_PARTY_DATA.yml</code>, with
  contact details redacted before anything reaches disk.</p>
  <p class="lede">In the repository: <code>CONTRIBUTING.md</code>,
  <code>CODE_OF_CONDUCT.md</code>, <code>SECURITY.md</code>,
  <code>CITATION.cff</code>, <code>NOTICE</code>, <code>AGENTS.md</code>, and
  <code>design/architecture.md</code> — the system in Part 1 and all eighteen
  decisions in Part 2, each keeping the options it rejected, because six
  months from now the useful question is not what was chosen but what was
  given up and whether the reason still holds.</p>
  </div>
</details>
</section>

</div>

<footer><div class="wrap">
  <p>DOE-MCP {e(data['version'])} · registry revision
  {e(data['registry_revision'])} · {c['sources']} sources ·
  {c['organizations']} organizations · {c['adapters']} adapters ·
  {c['capabilities_served']}/{c['capabilities']} capabilities served.</p>
  <p>Generated from the same source registry the servers run on. A CI test
  fails the build when this page and the registry disagree.</p>
  <p>Citing this: see <code>CITATION.cff</code>. If you use DATA reached
  through it, cite that data's publisher — the string is in every answer's
  provenance block, and for some sources citation is a condition of use
  rather than a courtesy.</p>
  <p>Packaging shape borrowed with thanks from PNNL's NEPA-MCP. That project
  does not endorse this one.</p>
</div></footer>

<script>{_script()}</script>
<script>
// In-page navigation, done without touching location.hash.
//
// Two reasons. A section is closed by default, so a plain anchor lands on a
// collapsed summary and the reader sees nothing move — the ancestors have
// to be opened first. And the hash itself is not always available: served
// from a data: URL, as a local preview pane does, assigning location.hash
// is refused as a top-frame navigation and every nav link silently does
// nothing. Handling the click directly works in both places, and the URL is
// still updated where the browser allows it.
//
// Braces are doubled because this sits inside an f-string template.
(function () {{
  function reveal(id) {{
    var el = document.getElementById(id);
    if (!el) return false;
    // Ancestors, for a sub-heading that sits inside a collapsed section.
    for (var n = el; n; n = n.parentElement) {{
      if (n.tagName === "DETAILS") n.open = true;
    }}
    // And the section's OWN group, because a top-level nav link targets the
    // <section> while the <details> is inside it — walking up from there
    // never reaches it, which left every top-level link doing nothing.
    var own = el.querySelector ? el.querySelector("details.sect") : null;
    if (own) own.open = true;
    el.scrollIntoView({{block: "start", behavior: "smooth"}});
    return true;
  }}
  document.addEventListener("click", function (ev) {{
    var target = ev.target instanceof Element ? ev.target : null;
    if (!target) return;
    var link = target.closest('a[href^="#"]');
    if (!link) return;
    var id = decodeURIComponent(link.getAttribute("href").slice(1));
    if (!id || !reveal(id)) return;
    ev.preventDefault();
    try {{ history.replaceState(null, "", "#" + id); }} catch (err) {{ /* data: URL */ }}
  }});
  // A link followed into the page from outside still has to open its
  // section.
  addEventListener("hashchange", function () {{
    reveal(decodeURIComponent((location.hash || "").slice(1)));
  }});
  if (location.hash) reveal(decodeURIComponent(location.hash.slice(1)));
}})();
</script>
</body>
</html>
"""
