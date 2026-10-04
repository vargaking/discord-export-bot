import json

import pytest

from tests.fakediscord import (BOB, CDN, GUILD, channel, deny_everyone_view, message, thread)
from zet_discord.api import DiscordError
from zet_discord.export import ExportError

MB = 1024 * 1024


def read(out, relative):
    return json.loads((out / relative).read_text(encoding="utf-8"))


def ids(out, cid):
    numbers = sorted(int(p.stem) for p in (out / f"channels/{cid}/messages").glob("*.json") if p.stem.isdigit())
    return [[m["id"] for m in read(out, f"channels/{cid}/messages/{n}.json")] for n in numbers]


def test_structure_and_private_channels(discord, run_export):
    discord.channels += [
        channel(1, "Text", 4, position=0, permission_overwrites=[deny_everyone_view()]),
        channel(2, "Voice", 4, position=1),
        channel(10, "general", 0, parent_id="2", topic="Hi", position=0),
        channel(11, "staff", 0, parent_id="2", permission_overwrites=[deny_everyone_view()]),
        channel(12, "inherits", 0, parent_id="1"),
        channel(13, "opens", 0, parent_id="1", permission_overwrites=[
            {"id": GUILD, "type": 0, "allow": str(1 << 10), "deny": "0"}]),
        channel(14, "news", 5),
        channel(15, "Lounge", 2, parent_id="2"),
        channel(16, "stage", 13),
        channel(17, "ideas", 15, available_tags=[{"id": "5", "name": "idea", "emoji_name": None}]),
        channel(18, "role-only", 0, permission_overwrites=[
            {"id": "77", "type": 0, "allow": "0", "deny": str(1 << 10)}]),
    ]
    run_export()
    server = read(run_export.out, "server.json")
    assert server["format"] == 1
    assert server["source"]["platform"] == "discord"
    assert server["source"]["server_id"] == GUILD
    assert server["source"]["server_name"] == "Sample Guild"
    assert server["categories"] == [
        {"id": "1", "name": "Text", "position": 0}, {"id": "2", "name": "Voice", "position": 1}]
    by_name = {c["name"]: c for c in server["channels"]}
    assert "stage" not in by_name
    assert by_name["news"]["type"] == "text"
    assert by_name["Lounge"]["type"] == "voice"
    assert by_name["ideas"]["tags"] == [{"id": "5", "name": "idea"}]
    assert by_name["general"]["topic"] == "Hi" and by_name["general"]["category_id"] == "2"
    private = {name for name, c in by_name.items() if c["private"]}
    assert private == {"staff", "inherits"}
    assert "permission_overwrites" not in json.dumps(server)


def test_messages_paginate_oldest_first_across_chunks(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_messages("10", *[message(1000 + i, content=f"m{i}") for i in range(250)])
    run_export(chunk_size=100)
    assert [len(c) for c in ids(run_export.out, "10")] == [100, 100, 50]
    flat = [i for c in ids(run_export.out, "10") for i in c]
    assert flat == [str(1000 + i) for i in range(250)]
    message_calls = [r for r in discord.requests if r.url.path.endswith("/channels/10/messages")]
    assert [r.url.params["after"] for r in message_calls] == ["0", "1099", "1199"]
    assert all(r.url.params["limit"] == "100" for r in message_calls)


def test_message_fields_and_system_messages(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_channel(11, "random")
    discord.roles.append({"id": "50", "name": "mods"})
    discord.add_messages(
        "10",
        message(1, content="<@!12> see <#11> cc <@&50> <:party:99> done", pinned=True,
                edited_timestamp="2024-03-01T11:00:00.000000+00:00",
                reactions=[{"emoji": {"id": None, "name": "\N{THUMBS UP SIGN}"}, "count": 3},
                           {"emoji": {"id": "99", "name": "party"}, "count": 1}],
                embeds=[{"url": "https://e.test", "title": "T", "provider": {"name": "E"},
                         "image": {"url": "https://e.test/i.png", "proxy_url": "https://media.discordapp.net/x"}},
                        {"title": "no url"}]),
        message(2, BOB, "joined", type=7),
        message(3, BOB, "re", type=19, message_reference={"message_id": "1"}),
        message(4, BOB, "forwarded", message_reference={"message_id": "1", "type": 1}),
        message(5, BOB, "pin notice", type=6),
    )
    run_export()
    first, reply, forwarded = read(run_export.out, "channels/10/messages/1.json")
    assert first == {
        "id": "1", "author_id": "11", "timestamp": "2024-03-01T10:00:00Z", "edited_at": "2024-03-01T11:00:00Z",
        "content": "<@12> see #random cc @mods :party: done", "reply_to_id": None, "pinned": True,
        "attachments": [],
        "embeds": [{"url": "https://e.test", "title": "T", "description": None, "site_name": "E",
                    "image_url": "https://e.test/i.png"}],
        "reactions": [{"emoji": "\N{THUMBS UP SIGN}", "count": 3}, {"emoji": ":party:", "count": 1}],
    }
    assert reply["reply_to_id"] == "1"
    assert forwarded["reply_to_id"] is None
    assert ids(run_export.out, "10") == [["1", "3", "4"]]


def test_attachments_are_downloaded_and_big_ones_listed(discord, run_export):
    discord.add_channel(10, "general")
    small = f"{CDN}/attachments/1/9001/cat.png?ex=abc"
    big = f"{CDN}/attachments/1/9002/big.zip?ex=abc"
    discord.message_urls(small.split("?")[0], big.split("?")[0])
    discord.add_messages("10", message(1, attachments=[
        {"id": "9001", "filename": "cat.png", "size": 10, "content_type": "image/png", "url": small},
        {"id": "9002", "filename": "big.zip", "size": 30 * MB, "content_type": "application/zip", "url": big},
    ]))
    report = run_export(max_file_bytes=25 * MB)
    small_entry, big_entry = read(run_export.out, "channels/10/messages/1.json")[0]["attachments"]
    assert small_entry["path"] == "files/9001/cat.png"
    assert (run_export.out / small_entry["path"]).read_bytes() == b"data:" + small.split("?")[0].encode()
    assert big_entry["path"] is None and big_entry["size"] == 30 * MB
    assert not (run_export.out / "files/9002").exists()
    assert report.too_big == ["big.zip (30.0 MB) in #general"]


def test_zero_means_no_size_limit(discord, run_export):
    discord.add_channel(10, "general")
    url = f"{CDN}/attachments/1/9002/big.zip"
    discord.message_urls(url)
    discord.add_messages("10", message(1, attachments=[
        {"id": "9002", "filename": "big.zip", "size": 300 * MB, "content_type": None, "url": url}]))
    run_export(max_file_bytes=0)
    assert read(run_export.out, "channels/10/messages/1.json")[0]["attachments"][0]["path"] == "files/9002/big.zip"


def test_failed_download_is_listed_not_fatal(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_messages("10", message(1, attachments=[
        {"id": "9", "filename": "gone.png", "size": 5, "content_type": None, "url": f"{CDN}/attachments/gone.png"}]))
    report = run_export()
    assert read(run_export.out, "channels/10/messages/1.json")[0]["attachments"][0]["path"] is None
    assert "gone.png in #general" in report.failed_downloads


def test_unsafe_attachment_filename_stays_inside_the_bundle(discord, run_export):
    discord.add_channel(10, "general")
    url = f"{CDN}/attachments/1/9/evil"
    discord.message_urls(url)
    discord.add_messages("10", message(1, attachments=[
        {"id": "9", "filename": "../../x.txt", "size": 1, "content_type": None, "url": url}]))
    run_export()
    entry = read(run_export.out, "channels/10/messages/1.json")[0]["attachments"][0]
    assert entry["path"] == "files/9/_.._x.txt"
    assert (run_export.out / entry["path"]).resolve().is_relative_to(run_export.out.resolve())


def test_unreadable_channel_is_listed_and_not_an_error(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_channel(11, "archive")
    discord.add_messages("10", message(1))
    discord.forbidden_messages.add("11")
    report = run_export()
    server = read(run_export.out, "server.json")
    assert server["unreadable"] == [{"id": "11", "name": "archive"}]
    assert {c["id"] for c in server["channels"]} == {"10", "11"}
    assert not (run_export.out / "channels/11").exists()
    assert report.unreadable == ["#archive"]


def test_authors_have_counts_names_and_avatars(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_messages("10", message(1), message(2), message(3, BOB))
    discord.files[f"{CDN}/avatars/11/ahash.png"] = b"png"
    run_export()
    assert read(run_export.out, "server.json")["authors"] == [
        {"id": "11", "name": "Alice A", "avatar": "avatars/11.png", "messages": 2},
        {"id": "12", "name": "bob", "avatar": None, "messages": 1},
    ]
    assert (run_export.out / "avatars/11.png").read_bytes() == b"png"


def test_custom_emoji_are_saved(discord, run_export):
    discord.emojis = [{"id": "70", "name": "party", "animated": False}, {"id": "71", "name": "wave", "animated": True}]
    discord.files[f"{CDN}/emojis/70.png"] = b"p"
    discord.files[f"{CDN}/emojis/71.gif"] = b"g"
    run_export()
    assert read(run_export.out, "server.json")["emoji"] == [
        {"id": "70", "name": "party", "path": "emoji/70.png"},
        {"id": "71", "name": "wave", "path": "emoji/71.gif"},
    ]
    assert (run_export.out / "emoji/71.gif").read_bytes() == b"g"


def test_rate_limit_retries_without_real_waiting(discord, run_export, sleeps):
    discord.add_channel(10, "general")
    discord.add_messages("10", message(1))
    discord.rate_limit_first = 2
    run_export()
    assert sleeps == [0.5, 0.5]
    assert ids(run_export.out, "10") == [["1"]]


def test_voice_channel_text_chat_is_exported(discord, run_export):
    discord.add_channel(15, "Lounge", 2)
    discord.add_messages("15", message(1, content="in voice"))
    run_export()
    assert read(run_export.out, "channels/15/messages/1.json")[0]["content"] == "in voice"


def test_forum_posts_active_and_archived_with_pagination(discord, run_export):
    discord.add_channel(20, "ideas", 15, available_tags=[{"id": "5", "name": "idea"}])
    discord.add_thread(thread(300, 20, "Active post", applied_tags=["5"], flags=2),
                       message(300, content="starter"), message(301, BOB, "reply"))
    for n in (310, 311, 312):
        discord.add_thread(thread(n, 20, f"Old {n}", archived=True), message(n, content=f"old {n}"))
    discord.threads[-1]["thread_metadata"]["locked"] = True
    discord.archived_page_size = 2
    report = run_export()
    out = run_export.out
    assert sorted(p.stem for p in (out / "channels/20/threads").glob("*.json")) == ["300", "310", "311", "312"]
    post = read(out, "channels/20/threads/300.json")
    assert (post["title"], post["tag_ids"], post["pinned"], post["locked"]) == ("Active post", ["5"], True, False)
    assert post["created_at"] == "2024-03-05T09:00:00Z"
    assert [m["id"] for m in post["messages"]] == ["300", "301"]
    assert read(out, "channels/20/threads/312.json")["locked"] is True
    assert not (out / "channels/20/messages").exists()
    assert report.channels[0].threads == 4


def test_threads_inside_text_channels_and_private_threads_left_out(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_thread(thread(400, 10, "Release"), message(400, content="plan"))
    discord.add_thread(thread(401, 10, "Secret", type=12), message(401, content="private"))
    discord.add_thread(thread(402, 10, "Archived", archived=True), message(402, content="done"))
    run_export()
    assert sorted(p.stem for p in (run_export.out / "channels/10/threads").glob("*.json")) == ["400", "402"]


def test_deleted_starter_message_leaves_first_remaining_message(discord, run_export):
    discord.add_channel(20, "ideas", 15)
    discord.add_thread(thread(500, 20, "Starter deleted"), message(501, BOB, "first remaining"), message(502))
    run_export()
    assert [m["id"] for m in read(run_export.out, "channels/20/threads/500.json")["messages"]] == ["501", "502"]


def test_thread_starter_message_maps_to_the_starter_content(discord, run_export):
    discord.add_channel(10, "general")
    starter = message(600, content="original post")
    discord.add_thread(thread(600, 10, "From a message"),
                       message(601, type=21, content="", message_reference={"message_id": "600"}, referenced_message=starter),
                       message(602, BOB, "reply"),
                       message(603, type=21, content="", referenced_message=None))
    run_export()
    messages = read(run_export.out, "channels/10/threads/600.json")["messages"]
    assert [(m["id"], m["content"]) for m in messages] == [("600", "original post"), ("602", "reply")]


def test_archived_listing_forbidden_is_noted_and_export_continues(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_messages("10", message(1))
    discord.add_thread(thread(400, 10, "Live"), message(400))
    discord.forbidden_archived.add("10")
    report = run_export()
    assert (run_export.out / "channels/10/threads/400.json").exists()
    assert ids(run_export.out, "10") == [["1"]]
    assert report.notes == ["Archived threads of #general can't be listed"]
    assert report.unreadable == []


def test_unreadable_thread_is_skipped_with_a_note(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_thread(thread(400, 10, "Hidden"), message(400))
    discord.forbidden_messages.add("400")
    report = run_export()
    assert not (run_export.out / "channels/10/threads").exists()
    assert report.notes == ["Thread 'Hidden' in #general can't be read"]


def test_rerun_continues_after_the_last_message(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_messages("10", *[message(100 + i) for i in range(5)])
    run_export(chunk_size=3)
    discord.add_messages("10", *[message(200 + i) for i in range(2)])
    discord.requests.clear()
    run_export(chunk_size=3)
    assert ids(run_export.out, "10") == [["100", "101", "102"], ["103", "104", "200"], ["201"]]
    assert [r.url.params["after"] for r in discord.requests if r.url.path.endswith("/messages")] == ["104"]
    assert read(run_export.out, "server.json")["authors"][0]["messages"] == 7


def test_rerun_with_nothing_new_changes_nothing(discord, run_export):
    discord.add_channel(10, "general", last_message_id="104")
    discord.add_messages("10", *[message(100 + i) for i in range(5)])
    run_export()
    before = read(run_export.out, "channels/10/messages/1.json")
    discord.requests.clear()
    run_export()
    assert read(run_export.out, "channels/10/messages/1.json") == before
    assert discord.paths("/channels/10/messages") == []


def test_rerun_continues_threads_and_does_not_duplicate(discord, run_export):
    discord.add_channel(20, "ideas", 15)
    discord.add_thread(thread(300, 20, "Post"), message(300), message(301))
    run_export()
    discord.messages["300"].append(message(302, BOB, "late"))
    discord.threads[0]["name"] = "Renamed"
    discord.requests.clear()
    run_export()
    saved = read(run_export.out, "channels/20/threads/300.json")
    assert [m["id"] for m in saved["messages"]] == ["300", "301", "302"]
    assert saved["title"] == "Renamed"
    assert [r.url.params["after"] for r in discord.requests if r.url.path.endswith("/300/messages")] == ["301"]
    assert {a["id"]: a["messages"] for a in read(run_export.out, "server.json")["authors"]} == {"11": 2, "12": 1}


def test_rerun_after_a_crash_between_chunk_and_progress_starts_the_channel_over(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_messages("10", *[message(100 + i) for i in range(3)])
    run_export()
    progress_path = run_export.out / "channels/10/progress.json"
    progress = json.loads(progress_path.read_text())
    progress["last_id"] = "100"
    progress_path.write_text(json.dumps(progress))
    run_export()
    assert ids(run_export.out, "10") == [["100", "101", "102"]]
    assert read(run_export.out, "server.json")["authors"][0]["messages"] == 3


def test_refresh_reads_everything_again(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_messages("10", message(1, content="old"), message(2, content="gone"), message(3))
    run_export()
    discord.messages["10"] = [message(1, content="edited"), message(3)]
    discord.requests.clear()
    run_export(refresh=True)
    assert [m["content"] for m in read(run_export.out, "channels/10/messages/1.json")] == ["edited", "hello"]
    assert [r.url.params["after"] for r in discord.requests if r.url.path.endswith("/messages")] == ["0"]
    assert read(run_export.out, "server.json")["authors"][0]["messages"] == 2


def test_refresh_drops_threads_that_no_longer_exist(discord, run_export):
    discord.add_channel(20, "ideas", 15)
    discord.add_thread(thread(300, 20, "Post"), message(300))
    run_export()
    discord.threads.clear()
    run_export(refresh=True)
    assert not (run_export.out / "channels/20/threads").exists()


def test_only_limits_the_channels(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_channel(11, "random")
    discord.add_messages("10", message(1))
    discord.add_messages("11", message(2))
    run_export(only=["11"])
    assert [c["id"] for c in read(run_export.out, "server.json")["channels"]] == ["11"]
    assert not (run_export.out / "channels/10").exists()
    run_export(only=["10"])
    assert sorted(c["id"] for c in read(run_export.out, "server.json")["channels"]) == ["10", "11"]


def test_only_with_an_unknown_channel_is_an_error(discord, run_export):
    discord.add_channel(10, "general")
    with pytest.raises(ExportError, match="99"):
        run_export(only=["99"])
    assert not run_export.out.exists()


def test_dry_run_writes_nothing_and_downloads_nothing(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_channel(11, "staff", permission_overwrites=[deny_everyone_view()])
    discord.add_channel(12, "locked")
    discord.emojis = [{"id": "70", "name": "party", "animated": False}]
    discord.forbidden_messages.add("12")
    url = f"{CDN}/attachments/1/9/a.bin"
    discord.message_urls(url)
    discord.add_messages("10", message(1, attachments=[{"id": "9", "filename": "a.bin", "size": 2048, "content_type": None, "url": url}]),
                         message(2, BOB))
    discord.add_thread(thread(400, 10, "T"), message(400))
    report = run_export(dry_run=True)
    assert not run_export.out.exists()
    assert not [r for r in discord.requests if r.url.host == "cdn.discordapp.com"]
    general = report.channels[0]
    assert (general.messages, general.threads, general.attachments, general.attachment_bytes) == (2, 1, 1, 2048)
    assert report.authors == 2
    assert report.private == ["#staff"]
    assert report.unreadable == ["#locked"]


def test_a_crash_leaves_a_readable_bundle_and_the_rerun_finishes_it(discord, run_export):
    discord.add_channel(10, "general")
    discord.add_channel(11, "random")
    discord.add_messages("10", message(1))
    discord.add_messages("11", message(2))
    discord.broken_messages.add("11")
    with pytest.raises(DiscordError):
        run_export()
    server = read(run_export.out, "server.json")
    assert {c["id"] for c in server["channels"]} == {"10", "11"}
    assert server["authors"][0]["messages"] == 1
    assert not list(run_export.out.rglob("*.tmp"))
    discord.broken_messages.clear()
    run_export()
    assert ids(run_export.out, "11") == [["2"]]
    assert read(run_export.out, "server.json")["authors"][0]["messages"] == 2
