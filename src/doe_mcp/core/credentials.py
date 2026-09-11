"""Per-user credentials (decision 0012-B).

The rule and the reason: keys live in one file the user owns, never in an
MCP client's config. Client configs get copied into issues, pasted into
chats, and synced to repositories; an API key in one is a key you have to
rotate. Environment variables take precedence over the file so a CI run or a
container can inject a key without writing one to disk.

`doctor` reports presence, never values. There is no code path in this
package that prints a credential.

The api.data.gov budget is worth naming here because it is the one that
surprises people: a single key is 1,000 requests an hour across *everything*
that key touches, which is every NLR API at once. That shared budget is why
GSA's own EIA catalog entry is marked single-user, and why there is no
hosted tier to exhaust it (decision 0005).
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import CredentialMissing

# api.data.gov's public trial key. Real answers, someone else's budget —
# every source using it raises `rate_limited_demo`.
DEMO_KEY = "DEMO_KEY"

CREDENTIAL_SPECS: dict[str, dict[str, str]] = {
    "EIA_API_KEY": {
        "label": "EIA API v2 key",
        "register_url": "https://www.eia.gov/opendata/register.php",
        "note": "Free, instant, emailed. EIA runs its own key system; this "
                "is not an api.data.gov key.",
    },
    "API_DATA_GOV_KEY": {
        "label": "api.data.gov key (NLR developer network, URDB, AFDC)",
        "register_url": "https://api.data.gov/signup/",
        "note": "One key covers every NLR API. The 1,000 requests/hour "
                "budget is shared across all of them, so a busy PVWatts "
                "session and an AFDC session spend the same allowance. "
                "DEMO_KEY works for trial at a much lower limit.",
    },
    "EDX_API_KEY": {
        "label": "NETL Energy Data eXchange key",
        "register_url": "https://edx.netl.doe.gov/",
        "note": "Free account, key issued from the user profile page.",
    },
    "ARM_LIVE_TOKEN": {
        "label": "ARM Live data-services token",
        "register_url": "https://adc.arm.gov/armlive/",
        "note": "Free ARM account. ARM requires citation as a condition of "
                "use; the citation string rides in the envelope.",
    },
    "MP_API_KEY": {
        "label": "Materials Project API key",
        "register_url": "https://next-gen.materialsproject.org/api",
        "note": "Free account. Used by the mp-api library DOE-MCP depends "
                "on and wraps (decision 0011).",
    },
}


def default_path() -> Path:
    """`$DOE_MCP_CREDENTIALS`, else XDG config, else ~/.config."""
    override = os.environ.get("DOE_MCP_CREDENTIALS")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "doe-mcp" / "credentials.env"


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


@dataclass
class Credentials:
    values: dict[str, str]
    path: Path
    file_exists: bool
    insecure_mode: bool = False
    """True when the credentials file is readable by group or other. Reported
    by doctor; not fatal, because refusing to start over a file mode would
    strand a user mid-task with no way to read the advice."""

    @classmethod
    def load(cls, path: Path | None = None) -> "Credentials":
        p = path or default_path()
        values: dict[str, str] = {}
        exists = p.exists()
        insecure = False
        if exists:
            values.update(_parse(p.read_text()))
            mode = p.stat().st_mode
            insecure = bool(mode & (stat.S_IRWXG | stat.S_IRWXO))
        # Environment wins: a container or CI run injects without writing.
        for name in CREDENTIAL_SPECS:
            env = os.environ.get(name)
            if env:
                values[name] = env
        return cls(values=values, path=p, file_exists=exists,
                   insecure_mode=insecure)

    def scoped_to(self, names: "tuple[str, ...] | list[str]") -> "Credentials":
        """The same credentials with everything this server does not need
        removed.

        Least privilege between servers, not only between a server and the
        outside. `doe-research`, `doe-earth` and `doe-materials` declare no
        credentials at all, and until this existed each of them loaded the
        whole file — the EIA key, and every key the later phases add — into
        a process that has no source to spend it on. A prompt injection
        reaching a tool in one of those servers had the other servers' keys
        in reach; now it has an empty mapping.

        The keeper is the lineup's own `needs_credentials`, so a server
        gains access to a key by declaring it in the one table that already
        decides what the server is.
        """
        wanted = set(names)
        return Credentials(
            values={k: v for k, v in self.values.items() if k in wanted},
            path=self.path, file_exists=self.file_exists,
            insecure_mode=self.insecure_mode)

    def has(self, name: str) -> bool:
        return bool(self.values.get(name))

    def is_demo(self, name: str) -> bool:
        return self.values.get(name, "").strip().upper() == DEMO_KEY

    def require(self, name: str) -> str:
        value = self.values.get(name)
        if not value:
            spec = CREDENTIAL_SPECS.get(name, {})
            register = spec.get("register_url", "the publisher's site")
            raise CredentialMissing(
                f"{spec.get('label', name)} is not configured. This source "
                f"cannot be queried without it. Get a free key at {register}, "
                f"then run `doe-mcp configure credentials --set {name}`. Do "
                "not put the key in your MCP client's config file.")
        return value

    def present(self) -> list[str]:
        """Names only. Never values — this is what doctor prints."""
        return sorted(n for n in CREDENTIAL_SPECS if self.has(n))

    def missing(self) -> list[str]:
        return sorted(n for n in CREDENTIAL_SPECS if not self.has(n))
