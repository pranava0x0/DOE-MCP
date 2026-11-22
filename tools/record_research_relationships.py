#!/usr/bin/env python3
"""Capture bounded anonymous one-hop DOI requests; never synthesize responses.

Stops after a transport/publisher failure and keeps any successful recordings
in a new output file. Existing recordings are never overwritten.
"""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from doe_mcp.adapters.base import HttpFetcher, RequestBudget, request_budget, egress_policy_for, close_shared_client  # noqa: E402
from doe_mcp.adapters.replay import RecordingFetcher  # noqa: E402
from doe_mcp.core.credentials import Credentials  # noqa: E402
from doe_mcp.core.errors import DoeMcpError  # noqa: E402
from doe_mcp.runtime import load_context  # noqa: E402


async def record(output):
    if output.exists():
        raise ValueError('choose a new output file; recordings are never overwritten')
    ctx = load_context(credentials=Credentials(values={}, path=Path('/nonexistent'), file_exists=False))
    recorder = RecordingFetcher(inner=None)
    # Exactly the requests made by the application and resolver, with their
    # actual request parameters retained in the fixture.
    plan = [('osti-data-explorer', {'q': 'EAGLE-I', 'rows': 2, 'page': 1})]
    target = '10.13139/ornlnccs/1975203'
    plan += [(source, {'all_fields' if source == 'osti-doe-code' else 'doi': target, 'rows': 5, 'page': 1})
             for source in ('osti-gov-records', 'osti-doe-pages', 'osti-data-explorer', 'osti-doe-code')]
    budget = RequestBudget(5)
    token = request_budget.set(budget)
    try:
        for source, params in plan:
            manifest = ctx.sources.require_active(source)
            adapter = ctx.osti.params_for(manifest)
            recorder.inner = HttpFetcher(policy=egress_policy_for(manifest, adapter.base_url))
            try:
                await recorder.fetch_json(adapter.base_url.rstrip('/') + adapter.record_path, params)
                print(f'{source}: captured anonymous publisher response', flush=True)
            except DoeMcpError as err:
                print(f'{source}: stopped: {err.code}: {err}', flush=True)
                break
    finally:
        request_budget.reset(token)
        await close_shared_client()
    if recorder.interactions:
        recorder.write(output, 'Recorded live from anonymous OSTI research APIs by tools/record_research_relationships.py; original query provenance retained, no response trimming. Partial files demonstrate only their captured interactions.')
        print(f'{len(recorder.interactions)} interactions written to {output}; {budget.used} HTTP attempts')
    else:
        print('No publisher responses captured; live relationship coverage remains unverified.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(record(parser.parse_args().output))
