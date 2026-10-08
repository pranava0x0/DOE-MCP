"""Static evidence walkthroughs; untrusted publisher content is escaped."""
from __future__ import annotations

import html
import json
import math
from urllib.parse import urlsplit


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def link(url, label) -> str:
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return esc(label)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username:
        return esc(label)
    return f'<a href="{esc(url)}" rel="noreferrer">{esc(label)}</a>'


def table(headers, rows, caption="") -> str:
    head = ''.join(f'<th scope="col">{esc(x)}</th>' for x in headers)
    body = ''.join('<tr>'+''.join(f'<td>{esc(x if x is not None else "Unknown")}</td>' for x in row)+'</tr>' for row in rows)
    return f'<div class="scroll"><table><caption>{esc(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def series_chart(rows, label: str, units: str) -> str:
    """Break the line at missing values; never turn a missing value into zero."""
    if not rows:
        return '<p>No values returned.</p>'
    values = [float(v) for _, v in rows if isinstance(v, (int, float)) and math.isfinite(v)]
    if not values:
        return '<p>No numeric observations returned.</p>'
    low, high = min(values), max(values)
    span = high-low or 1
    segments, current = [], []
    for i, (_, v) in enumerate(rows):
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            if current:
                segments.append(current)
            current = []
            continue
        x = 65 + i * 570 / max(1, len(rows)-1)
        y = 180 - (v-low)*140/span
        current.append(f'{x:.2f},{y:.2f}')
    if current:
        segments.append(current)
    lines = ''.join(f'<polyline points="{" ".join(s)}" fill="none" stroke="#176559" stroke-width="3"/>' for s in segments)
    dots = ''.join(f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="3" fill="#176559"/>' for s in segments for p in s)
    return (f'<svg viewBox="0 0 680 235" role="img" aria-label="{esc(label)}. Values in the table below.">'
            f'<title>{esc(label)}</title><path d="M65 25 V185 H640" fill="none" stroke="#6a7773"/>'
            f'<text x="5" y="42">{high:g}</text><text x="5" y="180">{low:g}</text>'
            f'{lines}{dots}<text x="65" y="220">{esc(rows[0][0])}</text>'
            f'<text x="635" y="220" text-anchor="end">{esc(rows[-1][0])}</text></svg>'
            +table(['Publisher time/date',units],rows,label))


def point_map(steps) -> str:
    points=[]
    for step in steps:
        tool=step['tool']
        if not tool.startswith('facility.'):
            continue
        for row in step['envelope']['data'].get('records',[]):
            x,y=row.get('xlong'),row.get('ylat')
            if isinstance(x,(int,float)) and isinstance(y,(int,float)) and -180 <= x <= 180 and -90 <= y <= 90:
                points.append((x,y,row.get('p_name','Unnamed facility'), 'Wind' if 'wind' in tool else 'Solar',row.get('case_id')))
    if not points:
        return '<p>No mappable coordinates in this sample.</p>'
    xmin,xmax=min(p[0] for p in points),max(p[0] for p in points)
    ymin,ymax=min(p[1] for p in points),max(p[1] for p in points)
    circles=''
    for x,y,name,kind,_ in points:
        px=55+550*(x-xmin)/max(.01,xmax-xmin)
        py=220-175*(y-ymin)/max(.01,ymax-ymin)
        color='#176559' if kind=='Wind' else '#ae6410'
        circles+=f'<circle cx="{px:.2f}" cy="{py:.2f}" r="6" fill="{color}"><title>{esc(kind+": "+name)}</title></circle>'
    return (f'<h3>Facility coordinate map</h3><p>Returned-point bounds: {xmin:.4f}, {ymin:.4f} to {xmax:.4f}, {ymax:.4f}. '
            'Longitude/latitude plot; no parcel boundaries or base map. Green: wind. Amber: solar.</p>'
            f'<svg viewBox="0 0 680 270" role="img" aria-label="Sample facility coordinates, listed in the table below">'
            f'<rect x="50" y="35" width="560" height="190" fill="#eef3ef" stroke="#6a7773"/>{circles}'
            f'<text x="50" y="252">West {xmin:.4f}</text><text x="610" y="252" text-anchor="end">East {xmax:.4f}</text>'
            f'<text x="615" y="45">North</text></svg>'
            +table(['Type','Facility','Record','Longitude','Latitude'],[(k,n,i,x,y) for x,y,n,k,i in points],'Returned sample, not all Rhode Island facilities'))


def outcome(report) -> str:
    case = report['case']
    data = report['steps'][0]['envelope']['data']
    if case in {'evidence', 'partial'}:
        record = report['steps'][1]['envelope']['data']['record']
        return f"{data['record_count']} distinct records returned. Followed record {record['id']}: {record['title']}."
    if case == 'grid':
        latest = data['latest']
        return f"Latest recorded load: {latest['values']['Load']:,} MW at {latest['timestamp']} Pacific Time. This is a historical replay."
    if case == 'eia':
        return f"{data['record_count']} hourly observations of CISO demand, in MWh. These are recorded observations, not a current grid report."
    if case == 'site':
        solar = report['steps'][1]['envelope']['data']
        return f"Sample: {data['record_count']} wind records of {data['total_matches']} matches; {solar['record_count']} solar records of {solar['total_matches']} matches. Site suitability remains unassessed."
    if case == 'blocked':
        return "The registry names a proposed source for solar resource data. No solar-resource measurement was retrieved."
    if case == 'earth':
        return "Permafrost dataset results and five days of modelled Oak Ridge weather are shown separately. No study-site match is established."
    return f"{data['record_count']} computed structures returned, with one full structure lookup and a separate H/C/O basis-set example."


def step_view(step, number) -> str:
    env = step['envelope']
    data = env['data']
    coverage = env['coverage']
    sources={s['id']:s for s in env['provenance']}
    records=data.get('records',data.get('datasets',data.get('structures',[])))
    if 'record' in data and isinstance(data['record'],dict):
        records=[data['record']]
    cards=[]
    for ev in env['evidence']:
        record=next((r for r in records if str(r.get('id',r.get('case_id',''))) == ev['record_id']),{})
        title=record.get('title',record.get('name',record.get('chemical_formula_reduced',ev['record_id'])))
        source=sources[ev['source_ref']]
        locator=link(ev['locator'],'Publisher record') if ev.get('locator') else 'No publisher locator supplied'
        cards.append(f'<li><strong>{esc(title)}</strong><br>{esc(source["system"])} · record {esc(ev["record_id"])}<br>{locator}<br>Record date: {esc(ev.get("effective_at") or record.get("publication_date") or "Unknown")}</li>')
    gaps=''.join(f'<li>{esc(g["source_id"])}: {esc(g["reason"])}</li>' for g in coverage.get('sources_unavailable',[]))
    warns=''.join(f'<li>{esc(w["message"])}</li>' for w in env['warnings'])
    totals = ''
    if 'per_source' in data:
        totals = table(['Collection','Matches','Returned'], [(r['system'],r.get('total_matches'),r['returned']) for r in data['per_source']], 'Counts reported by each collection; totals can overlap')
    elif 'total_matches' in data:
        totals = table(['Returned','Total matches'], [(data.get('record_count'),data.get('total_matches'))])
    proposed=''
    if step['tool']=='registry.search_sources':
        proposed=table(['Source','State','Blocked reason'],[(r.get('name',r.get('id')),r.get('declared_state'),r.get('blocked_reason')) for r in data.get('sources',[])],'Registry inventory; not a publisher query')
    handoff=''
    if step['tool']=='registry.list_neighbors':
        handoff='<ul>'+''.join(f'<li>{link(r.get("repository"),r.get("name",r["id"]))}</li>' for r in data.get('neighbors',[]) if r['id']=='nepa-mcp')+'</ul>'
    extra=''
    if step['tool']=='grid.get_bpa_operations':
        extra=series_chart([(r['timestamp'],r['values'].get('Load')) for r in data['intervals']], 'BPA load · Pacific Time', 'MW')+f'<p>{esc(data.get("pending_note",""))}</p>'
    if step['tool']=='energy.grid_status':
        extra=table(['Period','Demand','Units'],[(r.get('period'),r.get('value'),r.get('value-units',r.get('units',data.get('units','Unknown')))) for r in data.get('observations',[])], 'CISO measured demand; publisher periods')
    if step['tool']=='earth.get_daymet_point':
        extra=series_chart([(r['date'],r['values'].get('tmax')) for r in data['days']],'Oak Ridge · modelled daily maximum temperature','°C')
    return (f'<section class="step"><h3>{number}. {esc(step["tool"])}</h3>'
            f'<p class="mono">{esc(json.dumps(step["args"],sort_keys=True))}</p>'
            +table(['Coverage dimension','Value'],[(k,coverage[k]) for k in ['registry','execution','pagination','source_claim','result']])
            +f'<p>Searched: {esc(", ".join(coverage.get("sources_searched",[])) or "Local registry only")}</p>'
            +('<h4>Unavailable sources</h4><ul>'+gaps+'</ul>' if gaps else '')
            +('<h4>Warnings</h4><ul>'+warns+'</ul>' if warns else '')
            +totals+extra+'<ul class="evidence">'+''.join(cards)+'</ul>'+proposed+handoff
            +f'<details><summary>Full response and evidence</summary><pre>{esc(json.dumps(env,indent=2))}</pre></details></section>')


STYLE='''
:root{color-scheme:light;font-family:system-ui,sans-serif;color:#1c302b;background:#f5f5ef}
*{box-sizing:border-box}body{margin:0}main,header,footer{max-width:1120px;margin:auto;padding:24px}
a{color:#145b4e;text-underline-offset:3px}h1{font-size:clamp(2rem,5vw,3.2rem);line-height:1.08}h1{margin:12px 0}h2{font-size:1.8rem}.answer{font-size:1.1rem;font-weight:600;border-bottom:1px solid #c5cec6;padding-bottom:16px}p,li{line-height:1.6}
.kicker{font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:#4b6158}header{border-bottom:2px solid #bbc6bd}
select,button{font:inherit;padding:12px;background:white;border:1px solid #687e72;border-radius:4px;max-width:100%}
label{display:block;font-weight:650;margin:10px 0}article{background:white;border:1px solid #c5cec6;border-radius:12px;padding:clamp(16px,4vw,36px);margin:20px 0}
.notice{background:#edf3eb;padding:16px;border-left:4px solid #176559}.limits{background:#fff5df;padding:16px 24px;border-left:4px solid #99600e}.step{border-top:1px solid #c5cec6;margin-top:28px;padding-top:16px}
.evidence{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:12px}.evidence li{border:1px solid #c5cec6;border-radius:8px;padding:16px;overflow-wrap:anywhere}
.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:.9rem;margin:12px 0}caption{text-align:left;font-weight:650;padding:8px 0}td,th{text-align:left;padding:10px;border-bottom:1px solid #d7ded7;vertical-align:top}th{background:#f2f5ef}
pre{overflow:auto;max-height:420px;background:#f2f5ef;padding:14px;font-size:.8rem}svg{display:block;width:100%;max-width:800px;background:#fbfcf8}svg text{font-size:12px;fill:#31443b}
summary{cursor:pointer;padding:12px 0;font-weight:650}.mono{font-family:monospace;overflow-wrap:anywhere;font-size:.85rem}.downloads{display:flex;gap:20px;flex-wrap:wrap}a:focus-visible,summary:focus-visible,select:focus-visible{outline:3px solid #b05c0e;outline-offset:4px}
[hidden]{display:none!important}@media(max-width:600px){main,header,footer{padding:16px}td,th{padding:8px}article{padding:16px}}
'''


def render(reports: list[dict]) -> str:
    articles=[]
    for r in reports:
        captures=table(['Fixture','Capture time (UTC)','SHA-256'],[(f['file'],f.get('captured_at') or 'Unknown; old recording',f['sha256']) for f in r['fixtures']])
        articles.append(f'<article id="{esc(r["case"])}"><p class="kicker">{esc(r["mode"])} walkthrough</p>'
                        f'<h2>{esc(r["title"])}</h2><p>{esc(r["question"])}</p><p class="answer">{esc(outcome(r))}</p>'
                        +('<p class="notice">Simulated source outage. The publisher status was changed only in this isolated replay.</p>' if r['simulation'] else '')
                        +f'<p class="notice">{esc(r["timestamp_note"])} Registry revision: {esc(r["registry_revision"])}.</p>'
                        +'<ul class="limits">'+''.join(f'<li>{esc(x)}</li>' for x in r['limitations'])+'</ul>'
                        +f'<p class="downloads"><a download href="data/demos/{esc(r["case"])}.json">Download JSON evidence</a><a download href="data/demos/{esc(r["case"])}.csv">Download CSV evidence index</a></p>'
                        +(point_map(r['steps']) if r['case']=='site' else '')
                        +''.join(step_view(step,i) for i,step in enumerate(r['steps'],1))
                        +'<details><summary>Recording manifest and capture times</summary>'+captures+'</details></article>')
    options=''.join(f'<option value="{esc(r["case"])}">{esc(r["title"])}</option>' for r in reports)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DOE-MCP evidence walkthroughs</title><style>{STYLE}</style></head>
<body><header><a href="index.html">DOE-MCP home</a><p class="kicker">Inspect the answer</p><h1>Public data. Visible evidence.</h1><p>Recorded examples across DOE research, energy, earth and materials. Inspect the returned records and what each search leaves unknown. No live requests run in this page.</p><p>Independent project. Not affiliated with the US Department of Energy.</p></header>
<main><div id="picker" hidden><label for="workflow">Choose a walkthrough</label><select id="workflow">{options}</select></div>{''.join(articles)}</main><footer><a href="guide.md">Run these workflows locally</a> · <a href="reference.md">Tool reference</a></footer>
<script>const select=document.getElementById('workflow');const panels=[...document.querySelectorAll('article')];function show(id){{if(!panels.some(p=>p.id===id))id='evidence';select.value=id;panels.forEach(p=>p.hidden=p.id!==id);}}document.getElementById('picker').hidden=false;select.addEventListener('change',()=>{{history.replaceState(null,'','#'+select.value);show(select.value);}});window.addEventListener('hashchange',()=>show(location.hash.slice(1)));show(location.hash.slice(1));</script></body></html>'''
