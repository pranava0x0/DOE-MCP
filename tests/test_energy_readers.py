"""Pinned publisher Parquet data and a gated, unrecorded PVWatts contract."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from doe_mcp.adapters.atb import AtbAdapter
from doe_mcp.adapters.base import BytesResponse, TTLCache
from doe_mcp.adapters.pvwatts import validate_inputs, validate_output
from doe_mcp.applications.bundle import Run
from doe_mcp.applications.energy_assumptions import build
from doe_mcp.atb_releases import RELEASES
from doe_mcp.core.credentials import Credentials
from doe_mcp.core.errors import InvalidQuery, SourceNotActivated, SourceSchemaChanged
from doe_mcp.core.registry import DeclaredState
from doe_mcp.domains.energy_tech import describe_atb, estimate_production, read_atb

ROOT = Path(__file__).resolve().parents[1]
FILTERS = {'technology': 'OffShoreWind', 'core_metric_parameter': 'Fixed O&M',
           'scenario': 'Moderate', 'core_metric_variable': '2035'}


@pytest.mark.parametrize('edition,total_rows,currency', [(2024, 585630, '2022 USD'), (2025, 1161588, '2023 USD')])
async def test_real_pinned_release_retains_qualifiers_values_and_row_evidence(ctx, edition, total_rows, currency):
    env = await read_atb(ctx, edition, FILTERS, rows=2)
    data = env.data
    assert data['total_matches'] == 56 and data['returned'] == 2 and data['table_rows'] == total_rows
    assert data['selection_status'] == 'multiple_matching_rows'
    assert env.coverage.pagination.value == 'truncated'
    assert currency in data['currency_basis']
    assert env.provenance[0].dataset_version == f'ATB {edition} {RELEASES[edition].release}'
    for row, evidence in zip(data['records'], env.evidence, strict=True):
        assert row['values']['atb_year'] == edition
        assert row['values']['scenario'] == 'Moderate'
        assert row['values']['units'] == '$/kW-yr'
        assert {'techdetail', 'techdetail2', 'resourcedetail', 'core_metric_case', 'tax_credit_case', 'crpyears'} <= row['values'].keys()
        assert row['evidence_ref'] == evidence.id and evidence.payload_hash == RELEASES[edition].sha256
        assert evidence.locator == RELEASES[edition].url
        assert f'data row {row["row_number"]}' in evidence.record_id
    if edition == 2025:
        assert data['records'][0]['values']['ref_summary']
        assert data['records'][0]['values']['ref_evidence_type']
    cached = await read_atb(ctx, edition, FILTERS, rows=2)
    assert cached.provenance[0].access_path.value == 'cache'
    assert cached.provenance[0].retrieved_at == env.provenance[0].retrieved_at
    original_value = cached.data['records'][0]['values']['value']
    cached.data['records'][0]['values']['value'] = -1.
    isolated = await read_atb(ctx, edition, FILTERS, rows=2)
    assert isolated.data['records'][0]['values']['value'] == original_value


async def test_dimension_discovery_and_exact_filters_do_not_guess_financial_case(ctx):
    env = await describe_atb(ctx, 2024, dimension='technology', rows=3)
    assert env.data['total_matches'] == 20 and len(env.data['values']) == 3
    cases = await describe_atb(ctx, 2025, dimension='core_metric_case', filters=FILTERS)
    assert 'R&D + TC' in cases.data['values']
    wrong = await read_atb(ctx, 2024, {**FILTERS, 'technology': 'offshorewind'}, rows=2)
    assert wrong.data['total_matches'] == 0 and wrong.data['records'] == []
    assert wrong.coverage.pagination.value == 'complete'
    page = await read_atb(ctx, 2024, FILTERS, rows=2, offset=2)
    head = await read_atb(ctx, 2024, FILTERS, rows=2)
    assert {r['row_number'] for r in page.data['records']}.isdisjoint(r['row_number'] for r in head.data['records'])


@pytest.mark.parametrize('edition', [2026, 'latest', True])
async def test_no_unverified_edition_or_latest_alias(ctx, edition):
    with pytest.raises(InvalidQuery, match='2024 or 2025'):
        await read_atb(ctx, edition, FILTERS)


@pytest.mark.parametrize('filters,rows,offset', [({'invented': 'x'}, 1, 0), ({'scenario': 1}, 1, 0), ({}, 0, 0), ({}, 51, 0), ({}, 1, -1)])
async def test_bad_filters_and_row_limits_fail_before_fetch(ctx, filters, rows, offset):
    class NoFetch:
        async def fetch_bytes(self, *args):
            raise AssertionError('invalid request reached transport')
    ctx.atb = AtbAdapter(NoFetch(), TTLCache())
    with pytest.raises(InvalidQuery):
        await read_atb(ctx, 2024, filters, rows, offset)


async def test_atb_hash_is_verified_before_native_parser(ctx, monkeypatch):
    import pyarrow.parquet as pq
    class ChangedAsset:
        async def fetch_bytes(self, url, params):
            return BytesResponse(b'x' * RELEASES[2024].bytes, {}, url)
    def forbidden(*args, **kwargs):
        raise AssertionError('unverified data reached native Parquet parser')
    monkeypatch.setattr(pq, 'ParquetFile', forbidden)
    ctx.atb = AtbAdapter(ChangedAsset(), TTLCache())
    with pytest.raises(SourceSchemaChanged, match='SHA256'):
        await read_atb(ctx, 2024, FILTERS)


async def test_both_atb_tools_and_pv_tool_use_source_activation_gate(ctx, monkeypatch):
    ctx.sources.get('nlr-atb').lifecycle.declared_state = DeclaredState.proposed
    for fn in [describe_atb, read_atb]:
        with pytest.raises(SourceNotActivated):
            await fn(ctx, 2024, **({'filters': FILTERS} if fn is read_atb else {}))
    def forbidden(*args, **kwargs):
        raise AssertionError('PV gate must execute before credential access')
    monkeypatch.setattr(Credentials, 'require', forbidden)
    monkeypatch.setattr(Credentials, 'load', forbidden)
    with pytest.raises(SourceNotActivated, match='nlr-pvwatts'):
        await estimate_production(ctx, {})


def test_energy_tech_server_does_not_read_a_credential_store(monkeypatch):
    from doe_mcp.servers import energy_tech
    def forbidden(*args, **kwargs):
        raise AssertionError('keyless server read the user credential store')
    class Server:
        def run(self):
            pass
    def assemble(context, profile):
        assert context.credentials.values == {} and profile == 'energy-tech:default'
        return Server()
    monkeypatch.delenv('DOE_MCP_PROFILE', raising=False)
    monkeypatch.setattr(Credentials, 'load', forbidden)
    monkeypatch.setattr(energy_tech, 'build_server', assemble)
    assert energy_tech.main() == 0


async def test_recorded_application_exports_qualified_inventory_and_no_pv_output(ctx):
    run = Run(ctx, 'energy-assumptions', 'recorded')
    files = await build(run)
    assert run.report['records'] and run.report['edition'] == 2024
    assert run.report['selection_status'] == 'multiple_matching_rows'
    assert run.report['pvwatts']['status'] == 'proposed' and run.report['pvwatts']['model_output'] is None
    assert [step['tool'] for step in run.report['steps']] == ['tech.describe_atb', 'tech.read_atb']
    assert '2022 USD' in run.report['currency_basis']
    assert json.loads(files['assumptions.json'])['asset_sha256'] == RELEASES[2024].sha256
    assert b'crpyears' in files['assumptions.csv'] and b'tax_credit_case' in files['assumptions.csv']


PV_INPUTS = {'system_capacity': 4., 'module_type': 0, 'losses': 14., 'array_type': 1,
             'tilt': 10., 'azimuth': 180., 'lat': 40., 'lon': -105.}


def validation_payload():
    # Validation-only synthetic values; never recorded, published or presented
    # as a successful PVWatts model call.
    inputs = validate_inputs(PV_INPUTS)
    return inputs, {'inputs': {**inputs, 'api_key': 'sentinel-pv-key'}, 'errors': [],
        'warnings': ['sentinel-pv-key warning'], 'version': '8.0.0',
        'ssc_info': {'module': 'pvwattsv8', 'version': 275, 'build': 'validation test'},
        'station_info': {'weather_data_source': 'validation-only', 'solar_resource_file': 'test.csv'},
        'outputs': {'ac_monthly': [1.] * 12, 'dc_monthly': [1.] * 12,
            'poa_monthly': [1.] * 12, 'solrad_monthly': [1.] * 12,
            'ac_annual': 12., 'solrad_annual': 1., 'capacity_factor': 0.1}}


def test_prepared_pv_contract_never_exposes_echoed_credential_and_preserves_metadata():
    inputs, payload = validation_payload()
    result = validate_output(payload, inputs, 'sentinel-pv-key')
    assert 'sentinel-pv-key' not in json.dumps(result)
    assert 'api_key' not in result['inputs']
    assert result['warnings'] and result['version'] == '8.0.0'
    assert result['station_info']['weather_data_source'] == 'validation-only'
    assert result['units']['ac_annual'] == 'kWhac'


@pytest.mark.parametrize('change', [{'lat': float('nan')}, {'system_capacity': 0.}, {'azimuth': 360.}, {'module_type': True}, {'api_key': 'sentinel'}, {'timeframe': 'hourly'}])
def test_prepared_pv_inputs_fail_without_echoing_inputs(change):
    with pytest.raises(InvalidQuery) as exc:
        validate_inputs({**PV_INPUTS, **change})
    assert 'sentinel' not in str(exc.value)


@pytest.mark.parametrize('defect', ['monthly_length', 'nonfinite', 'echo', 'annual_sum', 'version', 'weather'])
def test_prepared_pv_output_rejects_changed_contract(defect):
    inputs, payload = validation_payload()
    payload = copy.deepcopy(payload)
    if defect == 'monthly_length':
        payload['outputs']['ac_monthly'].pop()
    elif defect == 'nonfinite':
        payload['outputs']['dc_monthly'][0] = float('inf')
    elif defect == 'echo':
        payload['inputs']['lat'] = 39.
    elif defect == 'annual_sum':
        payload['outputs']['ac_annual'] = 200.
    elif defect == 'version':
        payload['version'] = '9.0.0'
    else:
        payload['station_info'].pop('weather_data_source')
    with pytest.raises(SourceSchemaChanged):
        validate_output(payload, inputs, 'sentinel-pv-key')


def test_recorded_asset_hashes_and_sizes_are_exact():
    manifest = json.loads((ROOT / 'tests/fixtures/nlr-atb.json').read_bytes())
    assert len(manifest['interactions']) == 2
    for spec, interaction in zip(RELEASES.values(), manifest['interactions'], strict=True):
        content = (ROOT / 'tests/fixtures' / interaction['body_ref']['file']).read_bytes()
        assert len(content) == spec.bytes < 20_000_000
        assert hashlib.sha256(content).hexdigest() == spec.sha256 == interaction['body_ref']['sha256']
