"""Static evidence walkthroughs; untrusted publisher content is escaped."""
from __future__ import annotations

import html
import json
import math
from urllib.parse import urlsplit

from doe_mcp.core.assemble import strip_inline_markup
from doe_mcp.workflows import CASE_SKILLS


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


def board_time(value):
    """NERSC's `2026-10-21T06:00:00` as `2026-10-21 06:00`: the same
    publisher value, readable in a narrow column, still with no zone."""
    if not value:
        return None
    text = str(value).replace("T", " ")
    return text[:-3] if text.endswith(":00") and text.count(":") == 2 else text


def table(headers, rows, caption="") -> str:
    head = ''.join(f'<th scope="col">{esc(x)}</th>' for x in headers)
    body = ''.join('<tr>'+''.join(f'<td data-label="{esc(headers[i])}">{esc(x if x is not None else "Unknown")}</td>' for i,x in enumerate(row))+'</tr>' for row in rows)
    return f'<div class="scroll"><table class="{"stackable" if len(headers)>2 else "compact"}"><caption>{esc(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


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
        return f"{data['record_count']} distinct records returned. Followed record {record['id']}: {strip_inline_markup(record['title'])}."
    if case == 'dataset-card':
        return f"{len(report['drafts'])} metadata drafts from DOE Data Explorer. File checksums, reuse permissions and governance still need review."
    if case == 'microbiome':
        # The workflow may try more than one study; the last attempt is the one that listed samples.
        tries = [s for s in report['steps'] if s['tool'] == 'bio.search_biosamples']
        samples = tries[-1]['envelope']['data']
        total = samples.get("total_matches")
        extent = f"{total} matches" if total is not None else "an unknown total"
        followed = ("Followed one returned study." if len(tries) == 1 else
                    f"Tried {len(tries)} returned studies; only the last listed samples.")
        return f"{followed} Showing {samples['record_count']} biosamples out of {extent}."
    if case == 'rulemaking':
        document = report['steps'][1]['envelope']['data']
        return f"Followed document {document['document_number']}. Publisher action: {(document.get('action') or 'Unknown').rstrip('.')}. Out-of-scope records excluded: {data.get('excluded_documents',0)}."
    if case == 'outage-history':
        count = len(data["releases_for_year"])
        return f"Found {count} {'release' if count == 1 else 'releases'} covering {data['requested_year']}. Documentation and customer-count metadata are shown separately; no outage rate is calculated."
    if case == 'grid':
        latest = data['latest']
        return f"Latest recorded load: {latest['values']['Load']:,} MW at {latest['timestamp']} Pacific Time. This is a historical replay."
    if case == 'eia':
        return f"{data['record_count']} hourly observations of CISO demand, in MWh. These are recorded observations, not a current grid report."
    if case == 'site':
        solar = report['steps'][1]['envelope']['data']
        return f"Sample: {data['record_count']} wind records of {data['total_matches']} matches; {solar['record_count']} solar records of {solar['total_matches']} matches. Site suitability remains unassessed."
    if case == 'find-data':
        org = data['resolved']
        catalogs = report['steps'][2]['envelope']['data']['per_catalog']
        hits = sum(1 for c in catalogs if c['returned'])
        return (f"NREL resolves to {org['name']}, renamed {org.get('renamed_on') or 'on an unstated date'}. "
                f"{hits} of {len(catalogs)} DOE catalogs returned geothermal records; the empty ones are older harvests.")
    if case == 'facility-status':
        active = sum(1 for s in data['systems'] if s['status'] == 'active')
        outages = data.get('planned_outages') or []
        nxt = (f" Next planned outage: {outages[0]['description']}, starting {outages[0]['start_at']} (zone not stated)."
               if outages else " No planned outage on the board.")
        return f"{active} of {data['record_count']} NERSC systems active on the recorded board.{nxt}"
    if case == 'blocked':
        return "The registry names a proposed source for solar resource data. No solar-resource measurement was retrieved."
    if case == 'earth':
        return "Permafrost dataset results and five days of modelled Oak Ridge weather are shown separately. No study-site match is established."
    return f"{data['record_count']} computed structures returned, with one full structure lookup and a separate H/C/O basis-set example."


def scope_line(report) -> str:
    """Always visible: how much of the result set was read, and that this
    is a recording. Both used to sit in closed disclosures, so a reader saw
    "2 metadata drafts" with no sign that 1,035 records matched."""
    parts = []
    for step in report['steps']:
        if step['tool'].startswith('registry.'):
            continue  # the project's own inventory, not a publisher read
        data = step['envelope']['data']
        per = data.get('per_source') or data.get('per_catalog')
        if per:
            parts += [f"{r['returned']} of {r['total_matches']:,} from {r['system']}" if isinstance(r.get('total_matches'), int)
                      else f"{r['returned']} from {r['system']}, total unknown" for r in per]
        elif isinstance(data.get('total_matches'), int) and 'record_count' in data:
            total = data['total_matches']
            parts.append(f"{data['record_count']} of {total:,} match{'es' if total != 1 else ''}")
        if parts:
            break
    read = ("Read " + "; ".join(parts) + ". ") if parts else ""
    if report['mode'] == 'recorded':
        return f"{read}A recorded replay at registry revision {report['registry_revision']}, not current data."
    return f"{read}A live run; publishers can change between runs."


def step_view(step, number, case="demo") -> str:
    env = step['envelope']
    data = env['data']
    coverage = env['coverage']
    sources={s['id']:s for s in env['provenance']}
    records=data.get('records',data.get('datasets',data.get('structures',data.get('studies',data.get('biosamples',[])))))
    if 'record' in data and isinstance(data['record'],dict):
        records=[data['record']]
    if step['tool']=='docs.search_rulemakings':
        records=data['documents']
    elif step['tool']=='docs.get_rulemaking':
        records=[data]
    if step['tool']=='grid.find_outage_history':
        roles={}
        for key,role in [('releases_for_year',f"release covering {data.get('requested_year')}"),('documentation','documentation'),('customer_counts','customer-count denominator'),('releases','another year\'s release')]:
            for r in data.get(key,[]):
                for oid in r.get('osti_ids',[]):
                    roles.setdefault(str(oid),{'id':str(oid),'title':f"{r['title']} ({role})"})
        records=list(roles.values())
    cards=[]
    for ev in env['evidence']:
        record=next((r for r in records if str(r.get('id',r.get('case_id',r.get('study_id',r.get('sample_id',r.get('document_number','')))))) == ev['record_id']),{})
        title=record.get('title',record.get('name',record.get('chemical_formula_reduced',ev['record_id'])))
        source=sources[ev['source_ref']]
        locator=link(ev['locator'],'Publisher record') if ev.get('locator') else 'No publisher locator supplied'
        cards.append(f'<li><strong>{esc(strip_inline_markup(str(title)))}</strong><br>{esc(source["system"])} · record {esc(ev["record_id"])}<br>{locator}<br>Record date: {esc(ev.get("effective_at") or record.get("publication_date") or "Unknown")}</li>')
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
    recipes = ''.join(f'<li>{link(r.get("uri"),r.get("label") or "Publisher access recipe")}{" — "+esc(r["instructions"]) if r.get("instructions") else ""}</li>' for r in env.get('access_recipes',[]))
    extra=''
    if step['tool'] in {'docs.search_rulemakings','docs.get_rulemaking'}:
        extra=table(['Document','Action','Published','Effective','Comments close','Dockets'],[(r['document_number'],r.get('action'),r.get('publication_date'),r.get('effective_on'),r.get('comments_close_on'),', '.join(r.get('docket_ids',[]))) for r in records], 'Publisher metadata; dates do not establish current legal effect')
        extra += '<p>'+esc(data.get('total_matches_is_unfiltered',''))+'</p>'
    if step['tool']=='grid.find_outage_history':
        for key,label in [('releases_for_year','Releases covering the requested year'),('documentation','How to interpret the data'),('customer_counts','Customer denominator — check its vintage')]:
            extra += '<h4>'+esc(label)+'</h4><ul>'+''.join('<li>'+link(r.get('doi_url'),r['title'])+'<br>Published: '+esc(r.get('published') or 'Unknown')+'</li>' for r in data.get(key,[]))+'</ul>'
        extra += '<p>'+esc(data.get('note',''))+'</p>'
    if step['tool']=='registry.resolve_org' and data.get('resolved'):
        org=data['resolved']
        extra=table(['Asked for','Resolved to','Renamed','Former names'],[(step['args'].get('query'),org.get('name'),org.get('renamed_on') or 'Not renamed',', '.join(org.get('former_names',[])))])
    if step['tool']=='discovery.search_all_catalogs':
        extra=table(['Catalog','Vintage','Matches','Returned'],[(c['system'],c['vintage'],c.get('total_matches'),c['returned']) for c in data['per_catalog']],'A dated vintage is a harvested file; live means the catalog was queried')
        extra+=table(['Catalog','Title','Date'],[(r['catalog'],strip_inline_markup(r['title']),r.get('date') or 'Unknown') for r in data['results']])
    if step['tool']=='compute.facility_status':
        extra=table(['System','Type','Status','Last changed (zone not stated)'],[(s.get('full_name') or s['name'],s.get('type'),s['status'],board_time(s.get('updated_at'))) for s in data['systems']],'NERSC public status board, recorded')
        extra+=table(['System','Planned outage','Starts','Ends'],[(o['system'],o.get('description'),board_time(o.get('start_at')),board_time(o.get('end_at'))) for o in data.get('planned_outages') or []],'Planned outages as NERSC wrote them; times in an unstated zone')
        extra+='<p>'+esc(data.get('other_facilities_note',''))+'</p>'
    if step['tool']=='bio.search_biosamples':
        extra=table(['Sample','Place','Collected','Environment'],[(r['sample_id'],r.get('place'),r.get('collected'),', '.join(r.get('ecosystem',[]))) for r in data['biosamples']], 'Sample metadata; no sequence data retrieved')
    if step['tool']=='grid.get_bpa_operations':
        extra=series_chart([(r['timestamp'],r['values'].get('Load')) for r in data['intervals']], 'BPA load · Pacific Time', 'MW')+f'<p>{esc(data.get("pending_note",""))}</p>'
    if step['tool']=='energy.grid_status':
        extra=table(['Period','Demand','Units'],[(r.get('period'),r.get('value'),r.get('value-units',r.get('units',data.get('units','Unknown')))) for r in data.get('observations',[])], 'CISO measured demand; publisher periods')
    if step['tool']=='earth.get_daymet_point':
        extra=series_chart([(r['date'],r['values'].get('tmax')) for r in data['days']],'Oak Ridge · modelled daily maximum temperature','°C')
    evidence='<ul class="evidence">'+''.join(cards)+'</ul>'
    if extra and cards:
        evidence='<details><summary>Record sources</summary>'+evidence+'</details>'
    label={'bio.search_studies':'Study','bio.search_biosamples':'Samples','registry.search_sources':'Coverage','registry.list_neighbors':'Other tools','compute.facility_status':'Systems'}.get(step['tool'],step['tool'].split('.')[-1].replace('_',' ').capitalize())
    return (f'<section class="step" id="{esc(case)}-step-{number}" tabindex="-1" data-label="{esc(label)}"><h3>{number}. {esc(label)}</h3>'
            f'<details><summary>Coverage and source notes</summary><p class="mono">{esc(step["tool"])} {esc(json.dumps(step["args"],sort_keys=True))}</p>'
            +table(['Coverage dimension','Value'],[(k,coverage[k]) for k in ['registry','execution','pagination','source_claim','result']])
            +f'<p>Searched: {esc(", ".join(coverage.get("sources_searched",[])) or "Local registry only")}</p>'
            +('<h4>Unavailable sources</h4><ul>'+gaps+'</ul>' if gaps else '')
            +('<ul>'+warns+'</ul>' if warns else '')+'</details>'
            +totals+extra+('<h4>Publisher access recipes</h4><ul>'+recipes+'</ul>' if recipes else '')+evidence+proposed+handoff
            +f'<details><summary>Full response and evidence</summary><pre>{esc(json.dumps(env,indent=2))}</pre></details></section>')


STYLE='''
:root{color-scheme:light;font-family:system-ui,sans-serif;color:#1c302b;background:#f5f5ef}
*{box-sizing:border-box}body{margin:0}main,header,footer{max-width:1120px;margin:auto;padding:24px}
a{color:#145b4e;text-underline-offset:3px}h1{font-size:clamp(2rem,5vw,3.2rem);line-height:1.08}h1{margin:12px 0}h2{font-size:1.8rem}.answer{font-size:1.1rem;font-weight:600;border-bottom:1px solid #c5cec6;padding-bottom:16px}p,li{line-height:1.6}h1,h2,h3,p,li,td{overflow-wrap:break-word}.draft{border-top:2px solid #176559;margin-top:24px}.downloads a{padding:10px 0}.scope{color:#46564f;font-size:.95rem;margin-top:-4px}.skill{margin:0 0 8px;color:#46564f;font-size:.9rem}
#picker{position:sticky;top:0;background:#f5f5ef;z-index:2;padding:8px 0;border-bottom:1px solid #c5cec6}
#picker label{display:inline;margin-right:10px}#step-nav{display:flex;flex-wrap:wrap;gap:6px;padding:6px 3px}
#step-nav button{white-space:nowrap;font-size:.85rem;min-height:44px;padding:8px 10px;flex-shrink:0}
article,.step{scroll-margin-top:var(--toolbar-height,160px)}.step:focus,article:focus{outline:2px solid #176559;outline-offset:3px}
header{border-bottom:2px solid #bbc6bd}
select,button{font:inherit;padding:12px;background:white;border:1px solid #687e72;border-radius:4px;max-width:100%}
label{display:block;font-weight:650;margin:10px 0}article{background:white;border:1px solid #c5cec6;border-radius:12px;padding:clamp(16px,4vw,36px);margin:20px 0}
.notice{background:#edf3eb;padding:16px;border-left:4px solid #176559}.limits{background:#fff5df;padding:16px 24px;border-left:4px solid #99600e}.step{border-top:1px solid #c5cec6;margin-top:28px;padding-top:16px}
.evidence{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:12px}.evidence li{border:1px solid #c5cec6;border-radius:8px;padding:16px;overflow-wrap:anywhere}
.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:.9rem;margin:12px 0}caption{text-align:left;font-weight:650;padding:8px 0}td,th{text-align:left;padding:10px;border-bottom:1px solid #d7ded7;vertical-align:top}th{background:#f2f5ef}
pre{overflow:auto;max-height:420px;background:#f2f5ef;padding:14px;font-size:.8rem}svg{display:block;width:100%;max-width:800px;background:#fbfcf8}svg text{font-size:12px;fill:#31443b}
summary{cursor:pointer;padding:12px 0;font-weight:650}.mono{font-family:monospace;overflow-wrap:anywhere;font-size:.85rem}.downloads{display:flex;gap:20px;flex-wrap:wrap}a:focus-visible,summary:focus-visible,select:focus-visible{outline:3px solid #b05c0e;outline-offset:4px}
@media(max-width:600px){
header{padding:12px 16px}header h1{font-size:1.6rem}header p{margin:8px 0}
#picker label{display:none}#picker select{width:100%;min-height:44px}h2{font-size:1.35rem;margin:8px 0}
.stackable thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.stackable,.stackable tbody,.stackable tr{display:block;width:100%}
.stackable tr{border:1px solid #c5cec6;border-radius:6px;margin-bottom:10px;padding:6px}
.stackable td{display:grid;grid-template-columns:100px minmax(0,1fr);gap:8px;border:0;overflow-wrap:anywhere;padding:5px}
.stackable td::before{content:attr(data-label);font-weight:650;overflow-wrap:normal}
.stackable caption{display:block}.downloads{gap:12px}.downloads a{min-height:44px}
}
[hidden]{display:none!important}@media(max-width:600px){main,header,footer{padding:16px}td,th{padding:8px}article{padding:16px}}
'''


def render(reports: list[dict]) -> str:
    articles=[]
    for r in reports:
        drafts = ''
        for draft in r.get('drafts',[]):
            metadata = draft['metadata']
            drafts += '<section class="draft"><h3>'+esc(metadata['title'])+'</h3>'
            drafts += table(['Catalog field','Value'],[(k, ', '.join(map(str,v)) if isinstance(v,list) else v) for k,v in metadata.items()], 'Draft metadata only — not schema validated')
            drafts += '<h4>Still needs owner review</h4><ul>'+''.join('<li>'+esc(x)+'</li>' for x in draft['needs_owner_review'])+'</ul></section>'
        command = 'doe-mcp demo '+r['case']+' --output ./demo-output'
        reproduce = '<details><summary>Run this example locally</summary><p>After installing DOE-MCP, replay without a key, model or network request:</p><pre>'+esc(command)+'</pre>'
        if r['case'] != 'partial':
            reproduce += '<p>Add <code>--live</code> to query publishers. '+('EIA requires your own key.' if r['case']=='eia' else 'No model is called.')+' Live results and availability can differ.</p>'
        reproduce += '</details>'
        captures=table(['Fixture','Capture time (UTC)','SHA-256'],[(f['file'],f.get('captured_at') or 'Unknown; old recording',f['sha256']) for f in r['fixtures']])
        articles.append(f'<article id="{esc(r["case"])}" tabindex="-1">'
                        f'<h2>{esc(r["title"])}</h2>'
                        + (f'<p class="skill">Skill: <code>{esc(CASE_SKILLS[r["case"]])}</code></p>' if CASE_SKILLS.get(r["case"]) else '')
                        + f'<p class="answer">{esc(outcome(r))}</p>'
                        f'<p class="scope">{esc(scope_line(r))}</p>'
                        +('<p class="notice">Simulated source outage. The publisher status was changed only in this isolated replay.</p>' if r['simulation'] else '')
                        +'<details open><summary>Limits of this example</summary><ul>'+''.join(f'<li>{esc(x)}</li>' for x in r['limitations'])+'</ul></details>'
                        +f'<p class="downloads"><a download href="data/demos/{esc(r["case"])}.json">Download JSON evidence</a><a download href="data/demos/{esc(r["case"])}.csv">Download CSV evidence index</a></p>'
                        +reproduce+drafts
                        +(point_map(r['steps']) if r['case']=='site' else '')
                        +''.join(step_view(step,i,r['case']) for i,step in enumerate(r['steps'],1))
                        +'<details><summary>Recording manifest and capture times</summary>'+f'<p>{esc(r["timestamp_note"])} Registry revision: {esc(r["registry_revision"])}.</p>'+captures+'</details></article>')
    options=''.join(f'<option value="{esc(r["case"])}">{esc(r["title"])}</option>' for r in reports)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DOE-MCP demos</title><link rel="icon" href="data:,"><style>{STYLE}</style></head>
<body><header><a href="index.html">DOE-MCP home</a><h1>DOE-MCP demos</h1><p>Explore recorded results for free, without an account or API key.</p></header>
<main><div id="picker" hidden><label for="workflow">Choose a walkthrough</label><select id="workflow">{options}</select><nav id="step-nav" aria-label="Jump to result"></nav></div>{''.join(articles)}</main><footer><p>Independent project; not affiliated with the US Department of Energy.</p><a href="guide.html">Run these workflows locally</a> · <a href="reference.html">Tool reference</a></footer>
<script>
const select=document.getElementById('workflow');
const panels=[...document.querySelectorAll('article')];
const nav=document.getElementById('step-nav');
function show(id){{
  if(!panels.some(p=>p.id===id))id='evidence';
  select.value=id; panels.forEach(p=>p.hidden=p.id!==id);
  nav.replaceChildren();
  const panel=document.getElementById(id);
  const targets=[{{el:panel,label:'Overview'}},...Array.from(panel.querySelectorAll('.step'),(el,i)=>({{el,label:(i+1)+'. '+el.dataset.label}}))];
  for(const target of targets){{
    const button=document.createElement('button');
    button.type='button';button.textContent=target.label;
    button.addEventListener('click',()=>{{target.el.scrollIntoView({{block:'start'}});target.el.focus({{preventScroll:true}});}});
    nav.append(button);
  }}
}}
document.getElementById('picker').hidden=false;
select.addEventListener('change',()=>{{history.replaceState(null,'','#'+select.value);show(select.value);window.scrollTo(0,0);}});
window.addEventListener('hashchange',()=>{{show(location.hash.slice(1));window.scrollTo(0,0);}});
show(location.hash.slice(1));
new ResizeObserver(()=>document.documentElement.style.setProperty('--toolbar-height',(document.getElementById('picker').offsetHeight+16)+'px')).observe(document.getElementById('picker'));
</script></body></html>'''
