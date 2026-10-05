import pytest

from tests.fakediscord import GUILD, TOKEN, FakeDiscord
from zet_discord.__main__ import main
from zet_discord.api import DiscordClient
from zet_discord.export import Exporter, Options


@pytest.fixture
def discord():
    return FakeDiscord()


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def run_export(discord, sleeps, tmp_path):
    """Run an export against the fake guild; returns the report."""
    out = tmp_path / "bundle"

    def run(**options):
        client = DiscordClient(TOKEN, transport=discord.transport, sleep=sleeps.append)
        options.setdefault("server_id", GUILD)
        options.setdefault("out", out)
        report = Exporter(client, Options(**options)).run()
        client.close()
        return report

    run.out = out
    return run


class Run:
    def __init__(self, discord, tmp_path, capsys, monkeypatch):
        self.discord, self.out, self.capsys = discord, tmp_path / "bundle", capsys
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    def __call__(self, *args, token=TOKEN, transport=None, out=None):
        environ = {"DISCORD_BOT_TOKEN": token} if token is not None else {}
        code = main(["export", "--server", GUILD, "--out", str(out or self.out), *args], environ=environ,
                    transport=transport or self.discord.transport, sleep=lambda s: None)
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err


@pytest.fixture
def cli(discord, tmp_path, capsys, monkeypatch):
    return Run(discord, tmp_path, capsys, monkeypatch)
