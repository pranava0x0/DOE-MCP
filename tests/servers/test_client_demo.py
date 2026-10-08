from doe_mcp.client_demo import round_trip


async def test_sdk_stdio_round_trip():
    result = await round_trip()
    assert result["server"] == "doe-research"
    assert result["envelope"]["data"]["resolved"]["id"] == "nlr"
    assert result["typed_error"] == "InvalidQuery"
    assert result["tool_count"] > 0


def test_cli_serve_scopes_credentials(monkeypatch, tmp_path):
    from argparse import Namespace
    from doe_mcp.cli.__main__ import cmd_serve
    from doe_mcp.core.credentials import Credentials
    import doe_mcp.servers.build as build
    credentials = Credentials(values={"EIA_API_KEY": "sentinel-key"},
                              path=tmp_path / "none", file_exists=False)
    monkeypatch.setattr(Credentials, "load", lambda: credentials)
    seen = []
    class Server:
        def run(self):
            pass
    def capture(ctx, profile):
        seen.append(ctx)
        return Server()
    monkeypatch.setattr(build, "build_server", capture)
    assert cmd_serve(Namespace(profile="research:default")) == 0
    assert not seen[-1].credentials.has("EIA_API_KEY")
    assert seen[-1].server_name == "doe-research"
    assert cmd_serve(Namespace(profile="energy:default")) == 0
    assert seen[-1].credentials.has("EIA_API_KEY")
