import httpx
import pytest

from zet_discord.api import DiscordClient, DiscordError, Forbidden


def client_for(handler, sleeps):
    return DiscordClient("secret-token", transport=httpx.MockTransport(handler), sleep=sleeps.append)


def test_sends_bot_authorization_and_drops_none_params():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = client_for(handler, [])
    assert client.get("/x", limit=100, after=None) == {"ok": True}
    assert seen[0].headers["Authorization"] == "Bot secret-token"
    assert dict(seen[0].url.params) == {"limit": "100"}


def test_429_sleeps_retry_after_from_body_and_retries():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"retry_after": 1.5})
        return httpx.Response(200, json=[])

    sleeps = []
    assert client_for(handler, sleeps).get("/x") == []
    assert sleeps == [1.5]


def test_429_falls_back_to_retry_after_header():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, text="slow down")
        return httpx.Response(200, json=[])

    sleeps = []
    client_for(handler, sleeps).get("/x")
    assert sleeps == [3.0]


def test_waits_reset_after_when_bucket_is_empty():
    def handler(request):
        return httpx.Response(
            200, json=[], headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "0.75"})

    sleeps = []
    client = client_for(handler, sleeps)
    client.get("/a")
    assert sleeps == []
    client.get("/b")
    assert sleeps == [0.75]
    client.get("/c")
    assert sleeps == [0.75, 0.75]


def test_gives_up_after_repeated_429():
    sleeps = []
    client = client_for(lambda r: httpx.Response(429, json={"retry_after": 0.1}), sleeps)
    with pytest.raises(DiscordError) as error:
        client.get("/x")
    assert error.value.status == 429
    assert len(sleeps) == 10


def test_403_is_forbidden_with_code():
    client = client_for(lambda r: httpx.Response(403, json={"code": 50001, "message": "Missing Access"}), [])
    with pytest.raises(Forbidden) as error:
        client.get("/channels/1/messages")
    assert error.value.code == 50001


def test_retries_server_errors_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(502) if len(calls) < 3 else httpx.Response(200, json={"a": 1})

    sleeps = []
    assert client_for(handler, sleeps).get("/x") == {"a": 1}
    assert len(sleeps) == 2


def test_errors_never_contain_the_token():
    client = client_for(lambda r: httpx.Response(401, json={"message": "401: Unauthorized"}), [])
    with pytest.raises(DiscordError) as error:
        client.get("/guilds/1")
    assert "secret-token" not in str(error.value)


def test_download_writes_file_without_authorization(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"bytes")

    client = client_for(handler, [])
    dest = tmp_path / "f.bin"
    client.download("https://cdn.discordapp.com/attachments/1/2/f.bin", dest)
    assert dest.read_bytes() == b"bytes"
    assert not (tmp_path / "f.bin.part").exists()
    assert "Authorization" not in seen[0].headers


def test_download_failure_leaves_nothing(tmp_path):
    client = client_for(lambda r: httpx.Response(404), [])
    with pytest.raises(DiscordError):
        client.download("https://cdn.discordapp.com/x", tmp_path / "f.bin")
    assert list(tmp_path.iterdir()) == []
