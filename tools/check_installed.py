#!/usr/bin/env python3
"""Run with a clean wheel environment's Python, outside the checkout."""
from __future__ import annotations

import asyncio
import os
import tempfile


def main():
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        from doe_mcp.cli.workflows import asset_dir
        from doe_mcp.client_demo import round_trip
        from doe_mcp.replay import load_replay_context
        from doe_mcp.workflows import CASES, run_workflow
        assert asset_dir('skills').name == '_skills', 'using checkout skills'
        assert asset_dir('fixtures').name == '_fixtures', 'using checkout fixtures'
        skills=list(asset_dir('skills').glob('*/SKILL.md'))
        assert skills and all((p.parent/'bench.yaml').is_file() for p in skills)
        async def check():
            for case in CASES:
                ctx=load_replay_context(asset_dir('fixtures'),keyed=case=='eia')
                report=await run_workflow(ctx,case)
                assert report['steps']
            result=await round_trip()
            assert result['envelope']['data']['resolved']['id']=='nlr'
        asyncio.run(check())
        print('Installed skills, all recorded workflows and SDK round trip passed')


if __name__ == '__main__':
    main()
