import json

import httpx
import pytest

from tests.fakediscord import CDN, GUILD, TOKEN, message
from zet_discord.__main__ import main


class Run:
    def __init__(self, discord, tmp_path, capsys, monkeypatch):
        self.discord, self.out, self.capsys = discord, tmp_path / "bundle", capsys
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    def __call__(self, *args, token=TOKEN, transport=None):
        environ = {"DISCORD_BOT_TOKEN": token} if token is not None else {}
        code = main(["export", "--server", GUILD, "--out", str(self.out), *args], environ=environ,
                    transport=transport or self.discord.transport, sleep=lambda s: None)
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err


@pytest.fixture
def cli(discord, tmp_path, capsys, monkeypatch):
    return Run(discord, tmp_path, capsys, monkeypatch)


def test_missing_token_is_a_clear_error(cli):
    code, out, err = cli(token=None)
    assert code == 2 and "DISCORD_BOT_TOKEN is not set" in err
    assert not cli.out.exists()


def test_export_prints_a_summary_and_writes_the_bundle(cli, discord):
    discord.add_channel(10, "general")
    discord.add_messages("10", message(1), message(2))
    code, out, err = cli()
    assert code == 0
    assert "#general (text): 2 messages, 0 threads (0 messages), 0 attachments (0 B)" in out
    assert "Total: 2 messages" in out
    assert json.loads((cli.out / "server.json").read_text())["format"] == 1


def test_dry_run_prints_the_plan_and_writes_nothing(cli, discord):
    discord.add_channel(10, "general")
    discord.add_channel(11, "locked")
    discord.forbidden_messages.add("11")
    url = f"{CDN}/attachments/1/9/a.bin"
    discord.add_messages("10", message(1, attachments=[
        {"id": "9", "filename": "a.bin", "size": 5 * 1024 * 1024, "content_type": None, "url": url}]))
    code, out, err = cli("--dry-run")
    assert code == 0 and not cli.out.exists()
    assert "Dry run" in out
    assert "1 attachments (5.0 MB)" in out
    assert "Unreadable channels: #locked" in out


def test_max_file_mb_is_passed_through(cli, discord):
    discord.add_channel(10, "general")
    url = f"{CDN}/attachments/1/9/a.bin"
    discord.message_urls(url)
    discord.add_messages("10", message(1, attachments=[
        {"id": "9", "filename": "a.bin", "size": 2 * 1024 * 1024, "content_type": None, "url": url}]))
    code, out, err = cli("--max-file-mb", "1")
    assert "Over the size limit, not downloaded: a.bin (2.0 MB) in #general" in out
    assert not (cli.out / "files").exists()


def test_unknown_only_channel_exits_2(cli, discord):
    discord.add_channel(10, "general")
    code, out, err = cli("--only", "99")
    assert code == 2 and "99" in err


def test_bad_token_exits_1_without_echoing_it(cli):
    code, out, err = cli(token="wrong-secret-token")
    assert code == 1
    assert "Check the bot token" in err
    assert "wrong-secret-token" not in out + err


def test_bot_not_in_server_gives_a_hint(cli):
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"code": 50001, "message": "Missing Access"}))
    code, out, err = cli(transport=transport)
    assert code == 1 and "invited" in err


def test_token_never_appears_in_output_or_bundle(cli, discord):
    discord.add_channel(10, "general")
    discord.add_channel(11, "locked")
    discord.forbidden_messages.add("11")
    discord.emojis = [{"id": "70", "name": "party", "animated": False}]
    discord.files[f"{CDN}/emojis/70.png"] = b"e"
    discord.add_messages("10", message(1, content="hi"))
    code, out, err = cli()
    assert code == 0
    assert TOKEN not in out + err
    for path in cli.out.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path
            assert TOKEN not in path.name
    # every API request carried the token only in the header
    for request in discord.requests:
        assert TOKEN not in str(request.url)
