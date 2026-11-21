#!/usr/bin/env python3
"""Deterministic protocol demonstration. No model, provider or network calls."""
import json
import sys

request = json.load(sys.stdin)
steps = [message for message in request["messages"] if message["role"] == "tool"]
if not steps:
    result = {"tool_calls": [{"name": "registry.resolve_org", "arguments": {"query": "NREL"}}]}
else:
    evidence = steps[-1]["content"]["envelope"]["evidence"][0]["id"]
    result = {"answer": {
        "text": "Simulation only: the local registry resolves the former laboratory name. No model produced this answer.",
        "citations": [{"step": 1, "evidence_id": evidence}]}}
result.update(model_version="simulation-v1", settings={"deterministic": True, "provider_calls": 0},
              usage={"input_tokens": 10, "cached_input_tokens": 0,
                     "output_tokens": 5, "reasoning_output_tokens": 0})
print(json.dumps(result))
