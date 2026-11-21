"""Published sample bundles retain the exact files their manifests describe."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest
from doe_mcp.applications.samples import APPLICATIONS

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('application', APPLICATIONS)
def test_published_application_bundle_has_verified_files_and_mode_provenance(application):
    directory = ROOT / 'docs' / 'data' / 'apps' / application
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    report = json.loads((directory / 'report.json').read_bytes())
    expected_mode = 'simulation' if application == 'forecast-panel' else 'recorded'
    assert manifest['mode'] == report['mode'] == expected_mode
    if expected_mode == 'recorded':
        assert report['steps'] and report['fixtures']
        assert 'normalized' in report['retrieval_note']
    else:
        assert not report['steps'] and not report['fixtures']
        assert report['provenance']['verification'] == 'not_publisher_verified'
        assert 'site_reference_clock' not in report
    with zipfile.ZipFile(directory / 'bundle.zip') as bundle:
        assert set(bundle.namelist()) == {r['file'] for r in manifest['files']} | {'manifest.json'}
        for row in manifest['files']:
            content = (directory / row['file']).read_bytes()
            assert bundle.read(row['file']) == content
            assert len(content) == row['bytes']
            assert hashlib.sha256(content).hexdigest() == row['sha256']
    for step in report['steps']:
        assert step['envelope']['provenance']
    if application == 'docket-tracker':
        assert all(step['tool'] == 'docs.search_rulemakings' for step in report['steps'])
        assert report['source'] == 'federal-register'
    if application == 'gcam-comparison':
        assert report['arithmetic'] == 'not_performed'
        assert len(report['receipts']) == 6
        assert all(r['evidence_ref'] and r['file_url'] for r in report['receipts'])
        assert report['comparisons'][-1]['relationship'] == 'different_table_or_field'
    elif application == 'research-linkage':
        assert len(report['records']) == len(report['unmatched_records']) == 10
        assert report['explicit_relationships'] == report['shared_identity'] == []
        assert {step['tool'] for step in report['steps']} == {
            'research.search_literature', 'research.search_datasets', 'research.search_software'}

    elif application == 'energy-assumptions':
        assert report['edition'] == 2024 and report['asset']['sha256']
        assert report['records'] and report['pvwatts']['model_output'] is None
