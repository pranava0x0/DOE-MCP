"""Discovery routes must point to files served by the static deployment."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from tools.site_metadata import BASE, markdown_page
from tools.render_site import _quickstart

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.descriptions = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link":
            self.links.append(attrs)
        if tag == "meta" and attrs.get("name") == "description":
            self.descriptions.append(attrs.get("content"))


def test_sitemap_canonicals_and_markdown_alternates_resolve_to_public_files():
    sitemap = ET.parse(DOCS / "sitemap.xml")
    urls = [el.text for el in sitemap.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
    assert len(urls) == len(set(urls))
    assert {BASE, BASE + "guide.html", BASE + "reference.html", BASE + "demos.html"} <= set(urls)
    for url in urls:
        assert url.startswith(BASE)
        page = DOCS / (url.removeprefix(BASE) or "index.html")
        markup = page.read_text()
        head = Head()
        head.feed(markup)
        assert [x["href"] for x in head.links if x.get("rel") == "canonical"] == [url]
        assert len(head.descriptions) == 1 and head.descriptions[0]
        alternates = [x["href"] for x in head.links if x.get("type") == "text/markdown"]
        assert len(alternates) == 1 and (DOCS / alternates[0]).is_file()
        structured = re.findall(r'<script type="application/ld\+json">(.*?)</script>', markup, re.S)
        assert len(structured) == 1
        assert json.loads(structured[0])["url"] == url
    assert (DOCS / ".nojekyll").exists()


def test_markdown_headings_and_local_links_survive_rendering():
    markup = markdown_page("# Guide\n\n## A heading\n\n## A heading\n\n[Tools](reference.md#energy)\n\n[Client](../examples/README.md)\n", "guide.html", "Guide")
    assert 'id="a-heading"' in markup and 'id="a-heading-1"' in markup
    assert 'href="reference.html#energy"' in markup
    assert 'href="#a-heading-1"' in markup
    assert 'href="https://github.com/pranava0x0/DOE-MCP/blob/main/examples/README.md"' in markup


def test_preflight_is_linked_from_guide_and_homepage_check_step():
    guide = markdown_page((DOCS / "guide.md").read_text(), "guide.html", "Local guide")
    quickstart = _quickstart({"counts": {"records_reachable": 0}, "sources": []}, [])
    check_step = quickstart.split('id="start-check"', 1)[1].split('</li>', 1)[0]
    for markup in (guide, check_step):
        assert 'href="profile-preflight.html"' in markup
        assert "doe-mcp doctor --profile research:default --json" in markup
        assert "without reading credentials or client" in markup
        assert "doe-mcp doctor --online" in markup
    assert (DOCS / "profile-preflight.md").is_file()


def test_llms_file_links_only_existing_public_docs_and_inventory():
    text = (DOCS / "llms.txt").read_text()
    for url in re.findall(r'\]\((https://[^)]+)\)', text):
        if url.startswith(BASE):
            assert (DOCS / url.removeprefix(BASE)).is_file(), url
    assert "not an access policy" in text
    assert "Proposed sources are inventory only" in text


def test_bpa_inputs_and_receipts_render_as_browser_downloads():
    filenames = ["actuals.csv", "observation-receipts.json", "bpa-envelope-01.json"]
    source = "\n\n".join(f"[Download {name}](data/apps/bpa-observations/{name})" for name in filenames)
    markup = markdown_page(source, "applications.html", "Applications")
    class Anchors(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = {}
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "a":
                self.links[attrs.get("href")] = attrs
    anchors = Anchors()
    anchors.feed(markup)
    for name in filenames:
        assert anchors.links[f"data/apps/bpa-observations/{name}"]["download"] == f"bpa-observations-{name}"
