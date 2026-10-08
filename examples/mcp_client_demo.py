#!/usr/bin/env python3
"""Run an SDK stdio round trip; no publisher or API key is needed."""
import asyncio
import json

from doe_mcp.client_demo import round_trip

if __name__ == "__main__":
    print(json.dumps(asyncio.run(round_trip()), indent=2))
