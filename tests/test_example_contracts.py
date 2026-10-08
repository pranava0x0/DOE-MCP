"""Published command examples must match registered argument names."""
import inspect
import json
import re
from pathlib import Path

from doe_mcp.workflows import tool_spec


def test_published_cli_example_signatures():
    root = Path(__file__).resolve().parents[1]
    seen = 0
    for path in [root / 'README.md', root / 'examples' / 'README.md']:
        for name, raw in re.findall(r"doe-mcp tools call ([\w.]+)\s*\\?\s*--args '([^']+)'", path.read_text()):
            inspect.signature(tool_spec(name).fn).bind(None, **json.loads(raw))
            seen += 1
    assert seen >= 10
