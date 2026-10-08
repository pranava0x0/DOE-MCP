#!/usr/bin/env python3
"""Measure replay startup, cold/warm execution and output size; no model calls."""
import argparse
import asyncio
import json
import time
from pathlib import Path

from doe_mcp.cli.workflows import asset_dir
from doe_mcp.replay import fixture_manifest, load_replay_context
from doe_mcp.workflows import CASES, run_workflow


async def measure(repeats):
    rows=[]
    fixtures=asset_dir('fixtures')
    for case in CASES:
        for repeat in range(repeats):
            started=time.perf_counter()
            ctx=load_replay_context(fixtures,keyed=case=='eia')
            startup=time.perf_counter()-started
            for cache in ['cold','warm']:
                started=time.perf_counter()
                report=await run_workflow(ctx,case)
                elapsed=time.perf_counter()-started
                rows.append({'case':case,'repeat':repeat+1,'cache':cache,
                             'context_ms':round(startup*1000,3),
                             'workflow_ms':round(elapsed*1000,3),
                             'output_bytes':len(json.dumps(report).encode()),
                             'tool_calls':len(report['steps'])})
    return {'mode':'recorded','model_calls':0,'tokens':None,'samples':rows,
            'fixtures':fixture_manifest(fixtures),
            'note':'Replay-only baseline. No publisher or model latency measured.'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args()
    if args.repeats<1:
        parser.error('repeats must be positive')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(asyncio.run(measure(args.repeats)),indent=2)+'\n')
    print(args.output)
