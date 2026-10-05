"""The exported bundle has the shape of the sample copied from ping-server."""
import json
import re
from datetime import datetime
from pathlib import Path

from tests.fakediscord import BOB, CDN, channel, deny_everyone_view, message, thread

SAMPLE = Path(__file__).parent / "fixtures" / "bundle"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def shape(value):
    """Keys all the way down; lists are the merge of their items; null matches anything."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        merged = None
        for item in value:
            merged = merge(merged, shape(item))
        return [merged]
    return None if value is None else type(value).__name__


def merge(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: merge(a.get(k), b.get(k)) for k in a.keys() | b.keys()}
    if isinstance(a, list) and isinstance(b, list):
        return [merge(a[0], b[0])]
    return a


def merged_shape(paths):
    merged = None
    for path in paths:
        merged = merge(merged, shape(load(path)))
    return merged


def assert_same_shape(mine, sample, where):
    if mine is None or sample is None:
        return
    assert type(mine) is type(sample), f"{where}: {mine!r} vs {sample!r}"
    if isinstance(sample, dict):
        assert mine.keys() == sample.keys(), f"{where}: {sorted(mine)} vs {sorted(sample)}"
        for key in sample:
            assert_same_shape(mine[key], sample[key], f"{where}.{key}")
    elif isinstance(sample, list):
        assert_same_shape(mine[0], sample[0], f"{where}[]")
    else:
        assert mine == sample, f"{where}: {mine} vs {sample}"


def build_guild(discord):
    discord.channels += [
        channel(1, "Text channels", 4),
        channel(2, "Voice channels", 4, position=1),
        channel(101, "general", 0, parent_id="1", topic="Welcome"),
        channel(103, "staff", 0, parent_id="1", permission_overwrites=[deny_everyone_view()]),
        channel(105, "ideas", 15, parent_id="1", available_tags=[{"id": "t1", "name": "idea"}]),
        channel(104, "Lounge", 2, parent_id="2"),
        channel(107, "archive", 0, parent_id="1"),
    ]
    discord.messages.update({"101": [], "103": [], "104": [], "107": []})
    discord.forbidden_messages.add("107")
    discord.emojis = [{"id": "e1", "name": "party", "animated": False}]
    discord.files[f"{CDN}/emojis/e1.png"] = b"emoji"
    discord.files[f"{CDN}/avatars/11/ahash.png"] = b"avatar"
    discord.files[f"{CDN}/attachments/9001/cat.png"] = b"cat"
    discord.add_messages("101", message(
        1001, content="Thanks <@!12>", pinned=True, message_reference={"message_id": "1000"}, type=19,
        edited_timestamp="2024-03-01T10:12:00.000000+00:00",
        attachments=[{"id": "9001", "filename": "cat.png", "size": 3, "content_type": "image/png",
                      "url": f"{CDN}/attachments/9001/cat.png?ex=1"}],
        embeds=[{"url": "https://e.test", "title": "T", "description": "D", "provider": {"name": "E"},
                 "image": {"url": "https://e.test/i.png"}}],
        reactions=[{"emoji": {"id": None, "name": "\N{THUMBS UP SIGN}"}, "count": 3}],
    ), message(1002, BOB))
    discord.add_messages("103", message(1100))
    discord.add_thread(thread(7001, 101, "Release planning"), message(7001))
    discord.add_thread(thread(8001, 105, "Dark mode?", applied_tags=["t1"], flags=2), message(8001))


def test_exported_bundle_has_the_shape_of_the_sample(discord, run_export):
    build_guild(discord)
    run_export()
    out = run_export.out

    assert_same_shape(shape(load(out / "server.json")), shape(load(SAMPLE / "server.json")), "server.json")
    mine = merged_shape(out.glob("channels/*/messages/*.json"))
    sample = merged_shape(SAMPLE.glob("channels/*/messages/*.json"))
    assert_same_shape(mine, sample, "messages")
    mine = merged_shape(out.glob("channels/*/threads/*.json"))
    sample = merged_shape(SAMPLE.glob("channels/*/threads/*.json"))
    assert_same_shape(mine, sample, "threads")


def test_exported_bundle_passes_the_importers_checks(discord, run_export):
    """A small re-implementation of what the importer's parser requires."""
    build_guild(discord)
    run_export()
    out = run_export.out
    server = load(out / "server.json")

    assert server["format"] == 1
    assert server["source"]["platform"] and server["source"]["server_id"]
    for entry in server["categories"] + server["channels"] + server["authors"] + server["emoji"] + server["unreadable"]:
        assert isinstance(entry["id"], str)
    assert {c["type"] for c in server["channels"]} <= {"text", "voice", "forum"}
    assert {u["id"] for u in server["unreadable"]} <= {c["id"] for c in server["channels"]}

    def check_message(message):
        for key in ("id", "author_id"):
            assert isinstance(message[key], str)
        datetime.fromisoformat(message["timestamp"])
        if message["edited_at"]:
            datetime.fromisoformat(message["edited_at"])
        for attachment in message["attachments"]:
            assert isinstance(attachment["id"], str)
            if attachment["path"]:
                assert re.fullmatch(rf"files/{attachment['id']}/[^/]+", attachment["path"])
                assert (out / attachment["path"]).is_file()
        for embed in message["embeds"]:
            assert embed["url"]
        for reaction in message["reactions"]:
            assert isinstance(reaction["emoji"], str) and isinstance(reaction["count"], int)

    author_ids = {a["id"] for a in server["authors"]}
    for channel_dir in (out / "channels").iterdir():
        chunks = sorted((channel_dir / "messages").glob("*.json"), key=lambda p: int(p.stem)) if (channel_dir / "messages").is_dir() else []
        assert [int(p.stem) for p in chunks] == list(range(1, len(chunks) + 1))
        for chunk in chunks:
            for message in load(chunk):
                check_message(message)
                assert message["author_id"] in author_ids
        for thread_file in (channel_dir / "threads").glob("*.json") if (channel_dir / "threads").is_dir() else []:
            thread = load(thread_file)
            datetime.fromisoformat(thread["created_at"])
            assert thread["title"] and all(isinstance(t, str) for t in thread["tag_ids"])
            for message in thread["messages"]:
                check_message(message)
    for author in server["authors"]:
        assert author["avatar"] is None or (out / author["avatar"]).is_file()
    for emoji in server["emoji"]:
        assert (out / emoji["path"]).is_file()
