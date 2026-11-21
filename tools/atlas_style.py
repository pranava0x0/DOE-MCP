"""Shared masthead and demo styling for the static site."""

FIELD_CASES = {
    "world-desalination-electricity": "water",
    "world-coefficient-gaps": "emissions",
    "world-retirement-parameters": "power",
    "world-crop-rates": "land",
    "world-capture-residual": "power",
    "world-industry-response": "economy",
}


def globe():
    # A decorative engraved globe. The arcs depict no geographic boundaries or measurements.
    parallels = ''.join(f'<path d="M45 {y} Q160 {bend} 275 {y}"/>' for y, bend in
                        [(55, 100), (80, 126), (107, 151), (135, 174), (163, 194), (192, 212), (220, 232)])
    meridians = ''.join(f'<ellipse cx="160" cy="140" rx="{rx}" ry="102"/>' for rx in (22, 48, 73, 92, 102))
    return f'''<svg viewBox="0 0 320 280" aria-hidden="true" focusable="false">
    <defs><clipPath id="atlas-disc"><circle cx="160" cy="140" r="102"/></clipPath>
    <pattern id="atlas-dots" width="12" height="12" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r=".9" fill="#63887f"/></pattern></defs>
    <rect width="320" height="280" fill="url(#atlas-dots)"/>
    <circle cx="160" cy="140" r="102" fill="#25584b" stroke="#b0d7aa" stroke-width="1.4"/>
    <g clip-path="url(#atlas-disc)"><g transform="rotate(-24 160 140)" fill="none" stroke="#99c69f" stroke-width=".85">{meridians}{parallels}</g></g>
    <path d="M25 218 C62 271 319 104 289 74 C270 54 221 75 194 86" fill="none" stroke="#f8b473" stroke-width="1.8"/>
    <circle cx="194" cy="86" r="4" fill="#ffc58b"/><circle cx="26" cy="219" r="3" fill="#ffc58b"/>
    <path d="M12 29v-15h15m266 0h15v15M12 251v15h15m266 0h15v-15" stroke="#a6bdb1" fill="none"/></svg>'''


ATLAS_CSS = """
:root{--paper:#faf8f0;--ink:#18352e;--muted:#495f54;--faint:#58695f;--line:#cdd5c8;
 --panel:#edf1e8;--accent:#205d4e;--accent-rgb:32,93,78;--accent-soft:#e0eee2;--navy:#102f2a;--measure:72rem}
.util{background:#102f2a}.util a{color:#fff0ca}
.toc-brand{font:bold 1.15rem Georgia,serif}.toc-brand:before{content:"";display:inline-block;width:.7em;height:.7em;border-radius:50%;background:#bf5d33;margin-right:.4em}
header{background:#153e35;color:#f7f5e6;padding:clamp(24px,4vw,52px) 0 18px}
.atlas-top{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(200px,.83fr);gap:clamp(20px,5vw,72px);align-items:center}
header h1{font:500 clamp(2.8rem,6vw,5.3rem)/.99 Georgia,serif;letter-spacing:-.055em;max-width:14ch;margin:0 0 18px}
header h1{text-wrap:balance}header .tagline{color:#dde9de;max-width:43ch;font-size:1.05rem}header .free{color:#d0dfd1;max-width:56ch;font-size:.87rem}
.atlas-actions{display:flex;align-items:center;flex-wrap:wrap;gap:1.1rem;margin:16px 0}
.atlas-actions a{color:#fff2d6;font-weight:600;text-underline-offset:4px}
.atlas-actions .primary{display:inline-block;background:#ffcd94;color:#243b31;border-radius:3px;padding:9px 14px;text-decoration:none}
.atlas-plate{max-width:340px;justify-self:center;width:100%;margin:0}.atlas-plate svg{display:block;width:100%}
.atlas-inventory{margin-top:18px;padding-top:16px;border-top:1px solid #517368}
header .funnel .step{background:transparent;border-color:#648175;color:white}header .funnel .step b{color:#fff1d2}header .funnel .step span,header .works,header .works b,header .inventory,header .inventory a{color:#d7e5d7}
header a:focus-visible{outline:3px solid #ffcd94;outline-offset:3px}
#genesis-mission{font:500 clamp(1.7rem,4vw,2.8rem)/1.15 Georgia,serif;margin:24px 0 0;scroll-margin-top:var(--location-offset,4rem)}
.demo-link{border-radius:4px;box-shadow:0 3px 0 #e3e8de}.demo-link strong{color:#183e33;font:600 1.25rem/1.2 Georgia,serif}
.demo-link:hover{background:#f4f7ee}
.demo-link[data-field]{border-top:3px solid #b97539;background:#f8edd8}
.demo-link[data-field="land"]{background:#e5efde;border-top-color:#577c46}
.demo-link[data-field="economy"],.demo-link[data-field="water"]{background:#e5edf2;border-top-color:#537991}
.demo-link[data-field="emissions"]{background:#eee7ec;border-top-color:#93627b}
@media(max-width:650px){.atlas-top{grid-template-columns:1fr;gap:0}.atlas-plate{display:none}header h1{max-width:15ch;font-size:clamp(2.35rem,10vw,3.7rem)}
.atlas-actions{display:grid;grid-template-columns:max-content max-content;justify-content:start;gap:8px 20px;font-size:.91rem}.atlas-actions .primary{grid-column:1/-1;text-align:center}.atlas-inventory{margin-top:8px}.demo-link[data-field] span{display:block}.demo-link[data-field]{gap:7px}}
"""

DEMO_CSS = """
:root{background:#faf8f0;color:#193b32}
header{background:#153e35;color:#f7f5e6;border:0;border-bottom:4px solid #dba571}
header a{color:#f7d7a8}header h1,article h2{font-family:Georgia,serif;letter-spacing:-.035em}
header h1{font-weight:500}#picker{background:#faf8f0}
article{border-radius:5px;box-shadow:0 4px 0 #e1e6da}.answer{color:#174737}
.atlas-chart{padding:clamp(14px,2.5vw,26px);background:#f2f5eb;border:1px solid #cbd7c6;border-radius:4px;margin:16px 0}
.atlas-chart figcaption{font:600 1.12rem Georgia,serif;margin-bottom:14px}
.atlas-bar{display:grid;grid-template-columns:minmax(6rem,1fr) 2fr 3.5rem;align-items:center;gap:10px;margin:9px 0;font-size:.9rem}
.atlas-track{height:12px;background:#dce4d6}.atlas-track i{display:block;height:100%;background:#3d7359}
.atlas-bar b{font-variant-numeric:tabular-nums;text-align:right}.atlas-chart small{color:#40564b}
"""
