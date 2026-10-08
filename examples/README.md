# DOE-MCP Examples

Real-world command and tool call examples across DOE-MCP's four shipping servers:
`doe-research`, `doe-energy-data`, `doe-earth`, and `doe-materials`.

Every tool returns a structured provenance envelope with data, publisher
citations, coverage metrics, and caveats.

---

## 1. Quick Start Demos

### Finding literature and full-text links (keyless)
Search across both OSTI.GOV and DOE PAGES for DOE-funded research papers:

```bash
doe-mcp tools call research.search_literature \
  --args '{"query": "perovskite tandem solar cell", "year_from": 2024, "rows": 3}'
```

Fetch the full record and check full-text availability:

```bash
doe-mcp tools call research.get_record \
  --args '{"record_id": "3377207", "collection": "pages"}'
```

### Resolving renamed laboratories and historical domains
Map historical names like NREL or old domains to their current identity:

```bash
doe-mcp tools call registry.resolve_org \
  --args '{"query": "NREL"}'
```

Inspect what a specific national laboratory publishes across all repositories:

```bash
doe-mcp tools call registry.lab_crosswalk \
  --args '{"lab": "ornl"}'
```

---

## 2. Clean Energy & Grid Operations

### Electricity demand and grid status (EIA API v2)
Retrieve hourly demand for a balancing authority like CAISO (California ISO):

```bash
doe-mcp tools call energy.grid_status \
  --args '{"balancing_authority": "CISO", "metric": "demand", "hours": 3}'
```

*Note: Requires `EIA_API_KEY` set via `doe-mcp configure credentials`.*

### Pacific Northwest five-minute operations (BPA, keyless)
Read near-live SCADA observations from the Bonneville Power Administration:

```bash
doe-mcp tools call grid.get_bpa_operations \
  --args '{"intervals": 3}'
```

### Wind turbine and solar facility screening
Locate wind turbines in a given state or bounding box with turbine specifications:

```bash
doe-mcp tools call facility.find_wind_turbines \
  --args '{"state": "RI", "rows": 3}'
```

---

## 3. Earth & Environmental Science

### Single-pixel daily weather extraction (Daymet, keyless)
Extract daily surface weather estimates for any point in North America:

```bash
doe-mcp tools call earth.get_daymet_point \
  --args '{"latitude": 35.93, "longitude": -84.31, "start": "2023-06-01", "end": "2023-06-03", "variables": "tmax,tmin,prcp"}'
```

### Environmental System Science data (ESS-DIVE, keyless)
Search multidisciplinary environmental datasets from DOE field studies:

```bash
doe-mcp tools call earth.search_datasets \
  --args '{"text": "permafrost", "rows": 5}'
```

---

## 4. Materials & Chemistry

### Inorganic crystal structure search (Materials Project OPTIMADE, keyless)
Search computed crystal structures matching elemental compositions:

```bash
doe-mcp tools call materials.search_structures \
  --args '{"elements": "Li,Fe", "rows": 2}'
```

### Quantum chemistry basis sets (Basis Set Exchange, keyless)
Discover basis sets supporting specific elements for quantum chemistry codes:

```bash
doe-mcp tools call chemistry.search_basis_sets \
  --args '{"query": "def2-TZVP", "covers_elements": "Fe,O", "rows": 2}'
```

Fetch the NWChem-formatted basis set text directly:

```bash
doe-mcp tools call chemistry.get_basis_set \
  --args '{"name": "6-31g", "output_format": "nwchem", "elements": "H,C"}'
```

---

## 5. Discovery, Software & Technology Transfer

### Cross-catalog federated discovery (keyless)
Search DOE Data Explorer, OpenEnergyHub, DOE data.json, and DOE code.json:

```bash
doe-mcp tools call discovery.search_all_catalogs \
  --args '{"query": "geothermal", "per_catalog": 2}'
```

### DOE scientific software and simulation codes
Search DOE CODE for open-source repositories and packages:

```bash
doe-mcp tools call research.search_software \
  --args '{"query": "lattice QCD", "rows": 3}'
```

### Federal Register rulemakings and efficiency standards
Search active Department of Energy regulatory actions and public comment deadlines:

```bash
doe-mcp tools call docs.search_rulemakings \
  --args '{"query": "heat pump", "since": "2024-01-01", "until": "2024-12-31"}'
```

### Licensable patents and national laboratory intellectual property
Discover commercializable patents and technologies available for licensing:

```bash
doe-mcp tools call tech.find_licensable_ip \
  --args '{"lab": "ornl", "query": "carbon fiber", "rows": 3}'
```

### Alternative fuel and vehicle fuel economy ratings
Query official EPA/DOE fuel economy metrics and electric vehicle specifications:

```bash
doe-mcp tools call fuel.find_vehicle \
  --args '{"make": "Chevrolet", "model": "Bolt", "year": "2023"}'
```

---

## 6. Runnable Python Demos

The repository includes runnable Python scripts in this directory:

### Direct programmatic invocation (`quickstart.py`)
Queries tools directly in Python using the DOE-MCP runtime context and inspects returned structured envelopes:

```bash
python examples/quickstart.py
```

### JSON-RPC stdio client integration (`mcp_client_demo.py`)
Starts a server through the SDK client, initializes the session, calls a
registry tool, validates structured content, checks a typed error, and
closes the subprocess under a timeout:

```bash
python examples/mcp_client_demo.py
```


## Recorded workflows and installed skills

```bash
doe-mcp demo evidence --output ./evidence-run
doe-mcp demo grid --output ./grid-run
doe-mcp demo site --output ./site-run
doe-mcp demo earth --output ./earth-run
doe-mcp demo materials --output ./materials-run
doe-mcp skills find-doe-data --show
```

These use recorded publisher responses, with capture-time limits stated in
the exports. Add `--live` to query publishers locally. The
[workflow guide](../docs/guide.md) documents all cases, model evaluation and
the distinction between replay checks and current publisher availability.
