import pytest

from tests.fakediscord import GUILD, TOKEN, FakeDiscord
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
