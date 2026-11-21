"""Published sample bundles retain the exact files their manifests describe."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('application', ['forecast-benchmark', 'weather-package', 'docket-tracker'])
def test_published_application_bundle_has_verified_files_and_recorded_provenance(application):
    directory = ROOT / 'docs' / 'data' / 'apps' / application
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    report = json.loads((directory / 'report.json').read_bytes())
    assert manifest['mode'] == report['mode'] == 'recorded'
    assert report['steps'] and report['fixtures']
    assert 'normalized' in report['retrieval_note']
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
