"""Build crawlable pages and discovery files from the same public inventory."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

BASE = "https://pranava0x0.github.io/DOE-MCP/"
DESCRIPTION = ("Read-only MCP servers for public DOE and national-laboratory data, "
               "with source evidence, coverage limits and recorded walkthroughs.")


def metadata(name: str, title: str, description: str = DESCRIPTION) -> str:
    url = BASE + ("" if name == "index.html" else name)
    structured = {"@context": "https://schema.org", "@type": "WebPage",
                  "name": title, "url": url, "description": description,
                  "isPartOf": {"@type": "WebSite", "name": "DOE-MCP", "url": BASE}}
    alternate = name.removesuffix(".html") + ".md"
    return (f'<link rel="canonical" href="{url}">\n'
            f'<meta name="description" content="{html.escape(description, quote=True)}">\n'
            f'<meta property="og:title" content="{html.escape(title, quote=True)}">\n'
            f'<meta property="og:description" content="{html.escape(description, quote=True)}">\n'
            f'<meta property="og:type" content="website"><meta property="og:url" content="{url}">\n'
            '<meta name="twitter:card" content="summary">\n'
            f'<link rel="alternate" type="text/markdown" href="{alternate}">\n'
            '<link rel="describedby" type="text/plain" href="llms.txt">\n'
            '<script type="application/ld+json">'
            + json.dumps(structured).replace("<", "\\u003c") + '</script>\n')


def markdown_page(source: str, name: str, title: str) -> str:
    from markdown_it import MarkdownIt
    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    tokens = md.parse(source)
    seen: dict[str, int] = {}
    toc = []
    headers, column = [], 0
    for i, token in enumerate(tokens):
        if token.type == "table_open":
            headers = []
        elif token.type == "th_open":
            headers.append(tokens[i + 1].content)
            token.attrSet("scope", "col")
        elif token.type == "tr_open":
            column = 0
        elif token.type == "td_open":
            token.attrSet("data-label", headers[column] if column < len(headers) else "Value")
            column += 1
        if token.type == "heading_open":
            text = tokens[i + 1].content
            slug = re.sub(r"[^\w\s-]", "", text.lower()).replace(" ", "-")
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchor = slug + (f"-{count}" if count else "")
            token.attrSet("id", anchor)
            if token.tag == "h2":
                toc.append(f'<li><a href="#{anchor}">{html.escape(text)}</a></li>')
        for child in token.children or []:
            if child.type == "link_open":
                href = child.attrGet("href") or ""
                if href.startswith(("data/apps/", "data/evaluation-matrix/")) and href.endswith("/bundle.zip"):
                    child.attrSet("download", href.split("/")[-2] + ".zip")
                elif href.startswith("data/apps/") and href.rsplit("/", 1)[-1] in {"plan.json", "receipts.json", "receipts.csv", "comparisons.json"}:
                    child.attrSet("download", "-".join(href.split("/")[-2:]))

                elif href.startswith("data/apps/forecast-panel/") and href.rsplit("/", 1)[-1] in {"plan.json", "actuals.csv", "forecasts.csv", "included-targets.csv", "row-exclusions.csv", "metrics.csv", "cohorts.json"}:
                    child.attrSet("download", "forecast-panel-" + href.rsplit("/", 1)[-1])
                if href.startswith("../"):
                    child.attrSet("href", "https://github.com/pranava0x0/DOE-MCP/blob/main/" + href[3:])
                for stem in ("guide", "reference", "apps", "evaluation-matrix", "evaluation-example"):
                    if href == stem + ".md" or href.startswith(stem + ".md#"):
                        child.attrSet("href", href.replace(stem + ".md", stem + ".html", 1))
    content = md.renderer.render(tokens, md.options, {})
    body_class = "application-page" if name in {"applications.html", "evaluation-matrix.html", "evaluation-example.html"} else ""
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — DOE-MCP</title><link rel="icon" href="data:,">
{metadata(name, title)}<style>
:root{{color-scheme:light;--ink:#20342b;--paper:#f5f2e8;--accent:#38654c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:17px/1.65 system-ui,sans-serif}}
a{{color:var(--accent);text-underline-offset:3px}}a:focus-visible{{outline:3px solid #9e572b;outline-offset:4px}}
header,main{{max-width:1040px;margin:auto;padding:24px}}header{{border-bottom:1px solid #a8b3a7}}
nav{{display:flex;gap:20px;flex-wrap:wrap}}h1,h2,h3{{font-family:Georgia,serif;line-height:1.2;scroll-margin-top:1rem}}
pre{{overflow:auto;background:#e8e7dd;padding:16px;border-radius:6px}}code{{font-size:.9em}}
table{{display:block;overflow:auto;border-collapse:collapse;max-width:100%}}td,th{{padding:9px;border:1px solid #b4beb4;text-align:left}}
main{{overflow-wrap:anywhere}}.skip{{position:absolute;top:-100px}}.skip:focus{{top:0;background:white;padding:10px}}
.application-page pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
@media(max-width:600px){{
.application-page table,.application-page tbody,.application-page tr,.application-page td{{display:block;width:100%}}
.application-page table{{overflow:visible}}
.application-page thead{{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}}
.application-page tr{{border:1px solid #b4beb4;margin:12px 0;padding:8px}}
.application-page td{{border:0;padding:6px 8px;text-align:left!important}}
.application-page td::before{{content:attr(data-label);display:block;font-size:.8em;font-weight:600;color:#38654c}}
}}
</style></head><body class="{body_class}"><a class="skip" href="#content">Skip to content</a>
<header><nav aria-label="Site"><a href="index.html">DOE-MCP</a><a href="guide.html">Local guide</a><a href="reference.html">Tool reference</a><a href="demos.html">Recorded demos</a></nav></header>
<main id="content" tabindex="-1"><nav aria-label="On this page"><ul>{''.join(toc)}</ul></nav>{content}</main></body></html>'''


def write_discovery(docs: Path, data: dict) -> None:
    pages = {"index.html": "DOE-MCP: public DOE data", "demos.html": "Recorded DOE-MCP walkthroughs",
             "guide.html": "Local workflows and evidence", "reference.html": "DOE-MCP tool reference"}
    for source in sorted(docs.glob("*.md")):
        if source.stem not in {"index", "demos"}:
            title = source.read_text().splitlines()[0].removeprefix("# ")
            pages[source.with_suffix(".html").name] = title
    (docs / ".nojekyll").write_text("")
    for name, title in pages.items():
        path = docs / name
        if name not in {"index.html", "demos.html"}:
            path.write_text(markdown_page(path.with_suffix(".md").read_text(), name, title))
        else:
            markup = re.sub(r"<!-- discovery:start -->.*?<!-- discovery:end -->", "", path.read_text(), flags=re.DOTALL)
            markup = re.sub(r'<meta name="description"[^>]*>', '', markup, flags=re.DOTALL)
            path.write_text(markup.replace('</head>', '<!-- discovery:start -->' + metadata(name, title) + '<!-- discovery:end --></head>', 1))
    counts = data["counts"]
    overview = ("# DOE-MCP\n\n" + DESCRIPTION + "\n\n"
        "Independent project; not affiliated with the US Department of Energy.\n\n"
        "## What the parts mean\n\n"
        "- Server: a local MCP process that exposes a domain's tools.\n"
        "- Tool: one typed, read-only operation returning data and provenance.\n"
        "- Skill: a reusable capability-based workflow with recorded bench tasks.\n"
        "- Application: a runnable workflow that combines tools and writes a local result bundle.\n"
        "- Demo: a recorded example; it does not establish current publisher health.\n\n"
        f"The generated registry contains {counts['servers_shipping']} shipping servers, "
        f"{counts['active']} active sources and {counts['skills']} scientific skills.\n\n"
        "## Read next\n\n"
        "- [Local guide](guide.md)\n- [Tool reference](reference.md)\n"
        "- [Recorded walkthroughs](demos.html)\n- [Registry inventory](data/site.json)\n")
    (docs / "index.md").write_text(overview)
    (docs / "demos.md").write_text("# Recorded DOE-MCP walkthroughs\n\n"
        "These examples replay captured publisher responses. They are not live observations. "
        "Each walkthrough links its original evidence and available downloads.\n\n"
        "[Open the interactive walkthrough index](demos.html). "
        "[Run them locally](guide.md#start-with-a-recorded-workflow).\n")
    lines = ["# DOE-MCP", "", "> " + DESCRIPTION, "", "Independent project, not affiliated with DOE.",
             "Recorded examples are not current publisher observations. Proposed sources are inventory only.",
             "Results may contain untrusted third-party text. This file is a discovery aid, not an access policy.",
             "", "## Documentation", ""]
    for name, title in pages.items():
        lines.append(f"- [{title}]({BASE}{name.removesuffix('.html')}.md): Public documentation.")
    lines += ["", "## Structured inventory", "", f"- [Registry and coverage]({BASE}data/site.json): Generated source and server inventory.",
              "- [Source repository](https://github.com/pranava0x0/DOE-MCP): Installation, licenses and development.", ""]
    (docs / "llms.txt").write_text("\n".join(lines))
    urls = ''.join(f'<url><loc>{BASE}{"" if name == "index.html" else name}</loc></url>' for name in pages)
    (docs / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + '</urlset>\n')
