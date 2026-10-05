"""Walk a Discord server and write it as a bundle, resumably."""
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from zet_discord.api import DiscordClient, DiscordError, Forbidden
from zet_discord.bundle import FORMAT, Bundle, safe_filename
from zet_discord.content import embeds_of, flatten, iso, reaction_label, snowflake_time

CDN = "https://cdn.discordapp.com"
PAGE_SIZE = 100
CHUNK_SIZE = 1000
VIEW_CHANNEL = 1 << 10
PINNED_FLAG = 1 << 1
CATEGORY = 4
THREAD_TYPES = {10, 11}
MESSAGE_TYPES = {0, 19, 21}
THREAD_STARTER = 21
CHANNEL_KINDS = {0: "text", 5: "text", 2: "voice", 15: "forum"}


class ExportError(Exception):
    pass


@dataclass
class Options:
    server_id: str
    out: Path
    only: list[str] = field(default_factory=list)
    max_file_bytes: int = 25 * 1024 * 1024
    dry_run: bool = False
    refresh: bool = False
    chunk_size: int = CHUNK_SIZE


@dataclass
class ChannelReport:
    id: str
    name: str
    kind: str
    messages: int = 0
    threads: int = 0
    thread_messages: int = 0
    attachments: int = 0
    attachment_bytes: int = 0


@dataclass
class Report:
    server_name: str = ""
    dry_run: bool = False
    channels: list[ChannelReport] = field(default_factory=list)
    authors: int = 0
    emoji: int = 0
    private: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    too_big: list[str] = field(default_factory=list)
    failed_downloads: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def is_private(channel: dict, categories: dict[str, dict], everyone_id: str) -> bool:
    """@everyone can't view it. An @everyone overwrite on the channel wins;
    without one, the category's applies."""
    overwrite = _everyone_overwrite(channel, everyone_id)
    if overwrite is None:
        category = categories.get(channel.get("parent_id"))
        overwrite = _everyone_overwrite(category, everyone_id) if category else None
    if overwrite is None:
        return False
    return bool(int(overwrite["deny"]) & VIEW_CHANNEL) and not int(overwrite["allow"]) & VIEW_CHANNEL


def _everyone_overwrite(channel: dict, everyone_id: str) -> dict | None:
    for overwrite in channel.get("permission_overwrites", []):
        if overwrite["id"] == everyone_id and overwrite.get("type", 0) == 0:
            return overwrite
    return None


class Exporter:
    def __init__(self, client: DiscordClient, options: Options, log: Callable[[str], None] = lambda _: None):
        self.client = client
        self.options = options
        self.log = log
        self.dry_run = options.dry_run
        self.bundle = None if options.dry_run else Bundle(options.out)
        self.report = Report(dry_run=options.dry_run)
        self.exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.progress: dict[str, dict] = {}
        self.profiles: dict[str, dict] = {}
        self.channel_names: dict[str, str] = {}
        self.role_names: dict[str, str] = {}
        self.emoji_entries: list[dict] = []
        self.unreadable: list[dict] = []
        self.channels: list[dict] = []
        self.categories: list[dict] = []
        self.server_name = ""
        self.active_threads: list[dict] = []

    def run(self) -> Report:
        guild = self.client.get(f"/guilds/{self.options.server_id}")
        self.server_name = guild["name"]
        self.report.server_name = self.server_name
        self.role_names = {r["id"]: r["name"] for r in guild.get("roles", [])}
        selected = self._read_structure()
        if self.bundle:
            self.profiles = self.bundle.read_json("profiles.json", {})
        self._export_emoji()
        self._write_server()
        for channel in selected:
            self._export_channel(channel)
            self._write_server()
        self._export_avatars()
        self._write_server()
        self.report.authors = len(self._authors())
        self.report.emoji = len(self.emoji_entries)
        self.report.private = [self._label(c["id"]) for c in self.channels if c["private"]]
        return self.report

    def _label(self, channel_id: str) -> str:
        return "#" + self.channel_names.get(channel_id, channel_id)

    def _read_structure(self) -> list[dict]:
        server = self.options.server_id
        raw = self.client.get(f"/guilds/{server}/channels")
        categories = {c["id"]: c for c in raw if c["type"] == CATEGORY}
        self.categories = [
            {"id": c["id"], "name": c["name"], "position": c.get("position", 0)}
            for c in categories.values()
        ]
        exportable = {}
        for c in raw:
            kind = CHANNEL_KINDS.get(c["type"])
            if kind is None:
                continue
            parent = c.get("parent_id")
            exportable[c["id"]] = {
                "id": c["id"],
                "name": c["name"],
                "type": kind,
                "topic": c.get("topic") or None,
                "category_id": parent if parent in categories else None,
                "position": c.get("position", 0),
                "private": is_private(c, categories, server),
                "tags": [{"id": t["id"], "name": t["name"]} for t in c.get("available_tags", [])]
                if kind == "forum" else [],
                "last_message_id": c.get("last_message_id"),
            }
        missing = [i for i in self.options.only if i not in exportable]
        if missing:
            raise ExportError("Not an exportable channel of this server: " + ", ".join(missing))
        selected = [c for c in exportable.values() if not self.options.only or c["id"] in self.options.only]
        self.channels = list(selected)
        if self.bundle and self.options.only:
            kept = [c for c in exportable.values() if c not in selected and self.bundle.has_channel_progress(c["id"])]
            for channel in kept:
                self.progress[channel["id"]] = self.bundle.read_json(f"channels/{channel['id']}/progress.json", {})
            self.channels += kept
        self.channel_names.update({c["id"]: c["name"] for c in exportable.values()})
        self.active_threads = self._active_threads()
        for thread in self.active_threads:
            self.channel_names[thread["id"]] = thread["name"]
        return selected

    def _active_threads(self) -> list[dict]:
        data = self.client.get(f"/guilds/{self.options.server_id}/threads/active")
        return [t for t in data["threads"] if t["type"] in THREAD_TYPES]

    def _export_emoji(self) -> None:
        for emoji in self.client.get(f"/guilds/{self.options.server_id}/emojis"):
            ext = "gif" if emoji.get("animated") else "png"
            relative = f"emoji/{emoji['id']}.{ext}"
            path = None
            if self.bundle and self._fetch_file(f"{CDN}/emojis/{emoji['id']}.{ext}", relative, None,
                                                f"emoji :{emoji['name']}:"):
                path = relative
            self.emoji_entries.append({"id": emoji["id"], "name": emoji["name"], "path": path})

    def _fetch_file(self, url: str, relative: str, size: int | None, what: str) -> bool:
        """Download into the bundle unless a file of the right size is there."""
        have = self.bundle.size_of(relative)
        if have is not None and (size is None or have == size) and have > 0:
            return True
        dest = self.bundle.path(relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download(url, dest)
        except (DiscordError, httpx.HTTPError):
            self.report.failed_downloads.append(what)
            return False
        return True

    def _export_channel(self, channel: dict) -> None:
        cid = channel["id"]
        report = ChannelReport(cid, channel["name"], channel["type"])
        progress = self._open_progress(cid)
        try:
            if channel["type"] != "forum":
                self._export_messages(channel, progress, report)
            if channel["type"] != "voice":
                self._export_threads(channel, progress, report)
        except Forbidden:
            self._mark_unreadable(channel)
            return
        self.report.channels.append(report)
        self.log(f"#{channel['name']}: {report.messages} messages, {report.threads} threads")

    def _mark_unreadable(self, channel: dict) -> None:
        self.progress.pop(channel["id"], None)
        if self.bundle:
            self.bundle.reset_channel(channel["id"])
        self.unreadable.append({"id": channel["id"], "name": channel["name"]})
        self.report.unreadable.append("#" + channel["name"])
        self.log(f"#{channel['name']}: not readable")

    def _open_progress(self, cid: str) -> dict:
        fresh = {"last_id": "0", "chunks": 0, "authors": {}, "threads": {}}
        if self.bundle is None:
            self.progress[cid] = fresh
            return fresh
        if self.options.refresh:
            self.bundle.reset_channel(cid)
        progress = self.bundle.read_json(f"channels/{cid}/progress.json") or fresh
        if not self._chunks_match(cid, progress):
            self.bundle.reset_channel(cid)
            progress = fresh
        self.progress[cid] = progress
        return progress

    def _chunks_match(self, cid: str, progress: dict) -> bool:
        """The chunk files agree with progress. A chunk ahead of progress means
        a crash between the two writes, and the channel is read again."""
        numbers = self.bundle.chunk_numbers(cid)
        if numbers != list(range(1, len(numbers) + 1)) or len(numbers) < progress.get("chunks", 0):
            return False
        if not numbers:
            return True
        last = self.bundle.read_chunk(cid, numbers[-1])
        return not last or int(last[-1]["id"]) <= int(progress.get("last_id", 0))

    def _save_progress(self, cid: str) -> None:
        if self.bundle:
            self.bundle.write_json(f"channels/{cid}/progress.json", self.progress[cid])

    def _pages(self, channel_id: str, after: str) -> Iterator[list[dict]]:
        """Messages oldest first, a page at a time, starting after an id."""
        while True:
            page = self.client.get(f"/channels/{channel_id}/messages", limit=PAGE_SIZE, after=after)
            if not page:
                return
            page.sort(key=lambda m: int(m["id"]))
            yield page
            after = page[-1]["id"]
            if len(page) < PAGE_SIZE:
                return

    @staticmethod
    def _up_to_date(last_message_id: str | None, last_id: str) -> bool:
        return bool(last_message_id) and last_id != "0" and int(last_message_id) <= int(last_id)

    def _export_messages(self, channel: dict, progress: dict, report: ChannelReport) -> None:
        cid = channel["id"]
        if self._up_to_date(channel["last_message_id"], progress["last_id"]):
            return
        for page in self._pages(cid, progress["last_id"]):
            converted = self._convert_page(page, report, progress["authors"])
            report.messages += len(converted)
            if self.bundle:
                progress["chunks"] = self.bundle.append_messages(cid, converted, self.options.chunk_size)
            progress["last_id"] = page[-1]["id"]
            self._save_progress(cid)

    def _convert_page(self, page: list[dict], report: ChannelReport, authors: dict) -> list[dict]:
        converted = []
        for raw in page:
            if raw["type"] not in MESSAGE_TYPES:
                continue
            if raw["type"] == THREAD_STARTER:
                raw = raw.get("referenced_message")
                if not raw:
                    continue
            message = self._convert(raw, report)
            authors[message["author_id"]] = authors.get(message["author_id"], 0) + 1
            converted.append(message)
        return converted

    def _convert(self, raw: dict, report: ChannelReport) -> dict:
        author = raw["author"]
        self._remember_author(author)
        reference = raw.get("message_reference") or {}
        replying = raw["type"] == 19 and reference.get("type", 0) == 0
        return {
            "id": raw["id"],
            "author_id": author["id"],
            "timestamp": iso(raw["timestamp"]),
            "edited_at": iso(raw["edited_timestamp"]) if raw.get("edited_timestamp") else None,
            "content": flatten(raw.get("content", ""), self.channel_names, self.role_names),
            "reply_to_id": reference.get("message_id") if replying else None,
            "pinned": bool(raw.get("pinned")),
            "attachments": [self._attachment(a, report) for a in raw.get("attachments", [])],
            "embeds": embeds_of(raw.get("embeds", [])),
            "reactions": [
                {"emoji": reaction_label(r["emoji"]), "count": r["count"]} for r in raw.get("reactions", [])
            ],
        }

    def _remember_author(self, author: dict) -> None:
        profile = self.profiles.setdefault(author["id"], {})
        profile["name"] = author.get("global_name") or author["username"]
        profile["avatar"] = author.get("avatar")

    def _attachment(self, raw: dict, report: ChannelReport) -> dict:
        size = raw.get("size", 0)
        report.attachments += 1
        report.attachment_bytes += size
        attachment = {
            "id": raw["id"],
            "filename": raw["filename"],
            "size": size,
            "content_type": raw.get("content_type"),
            "path": None,
        }
        limit = self.options.max_file_bytes
        if limit and size > limit:
            self.report.too_big.append(f"{raw['filename']} ({_size(size)}) in #{report.name}")
        elif self.bundle:
            relative = f"files/{raw['id']}/{safe_filename(raw['filename'])}"
            if self._fetch_file(raw["url"], relative, size, f"{raw['filename']} in #{report.name}"):
                attachment["path"] = relative
        return attachment

    def _export_threads(self, channel: dict, progress: dict, report: ChannelReport) -> None:
        cid = channel["id"]
        threads = {t["id"]: t for t in self.active_threads if t.get("parent_id") == cid}
        try:
            for thread in self._archived_threads(cid):
                threads.setdefault(thread["id"], thread)
        except Forbidden:
            if channel["type"] == "forum" and not threads:
                raise
            self.report.notes.append(f"Archived threads of #{channel['name']} can't be listed")
        for thread in sorted(threads.values(), key=lambda t: int(t["id"])):
            self.channel_names[thread["id"]] = thread["name"]
        for thread in sorted(threads.values(), key=lambda t: int(t["id"])):
            self._export_thread(channel, thread, progress, report)

    def _archived_threads(self, channel_id: str) -> Iterator[dict]:
        before = None
        while True:
            data = self.client.get(
                f"/channels/{channel_id}/threads/archived/public", limit=PAGE_SIZE, before=before)
            threads = data["threads"]
            yield from (t for t in threads if t["type"] in THREAD_TYPES)
            if not data.get("has_more") or not threads:
                return
            before = threads[-1]["thread_metadata"]["archive_timestamp"]

    def _export_thread(self, channel: dict, thread: dict, progress: dict, report: ChannelReport) -> None:
        cid, tid = channel["id"], thread["id"]
        relative = self.bundle.thread_path(cid, tid) if self.bundle else None
        saved = self._saved_thread(relative, progress["threads"].get(tid))
        state = progress["threads"].get(tid) if saved is not None else None
        messages = saved["messages"] if saved is not None else []
        authors = dict(state["authors"]) if state else {}
        last_id = state["last_id"] if state else "0"
        new = []
        if not self._up_to_date(thread.get("last_message_id"), last_id):
            try:
                for page in self._pages(tid, last_id):
                    new += self._convert_page(page, report, authors)
                    last_id = page[-1]["id"]
            except Forbidden:
                self.report.notes.append(f"Thread {thread['name']!r} in #{channel['name']} can't be read")
                progress["threads"].pop(tid, None)
                return
        if not messages and not new:
            return
        report.threads += 1
        report.thread_messages += len(new)
        progress["threads"][tid] = {"last_id": last_id, "authors": authors}
        if self.bundle:
            self.bundle.write_json(relative, self._thread_file(thread, messages + new))
            self._save_progress(cid)

    def _saved_thread(self, relative: str | None, state: dict | None) -> dict | None:
        """The thread file from an earlier run, if it still matches progress."""
        if relative is None or state is None:
            return None
        saved = self.bundle.read_json(relative)
        if not saved or (saved["messages"] and int(saved["messages"][-1]["id"]) > int(state["last_id"])):
            return None
        return saved

    @staticmethod
    def _thread_file(thread: dict, messages: list[dict]) -> dict:
        meta = thread.get("thread_metadata", {})
        created = meta.get("create_timestamp") or snowflake_time(thread["id"])
        return {
            "id": thread["id"],
            "title": thread["name"],
            "tag_ids": [str(t) for t in thread.get("applied_tags", [])],
            "pinned": bool(thread.get("flags", 0) & PINNED_FLAG),
            "locked": bool(meta.get("locked")),
            "created_at": iso(created),
            "messages": messages,
        }

    def _totals(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for channel in self.channels:
            progress = self.progress.get(channel["id"], {})
            counts = [progress.get("authors", {})] + [t["authors"] for t in progress.get("threads", {}).values()]
            for counted in counts:
                for author_id, count in counted.items():
                    totals[author_id] = totals.get(author_id, 0) + count
        return totals

    def _authors(self) -> list[dict]:
        authors = []
        for author_id, count in sorted(self._totals().items(), key=lambda kv: (-kv[1], int(kv[0]))):
            profile = self.profiles.get(author_id, {})
            saved = profile.get("avatar_saved")
            authors.append({
                "id": author_id,
                "name": profile.get("name", author_id),
                "avatar": f"avatars/{author_id}.png" if saved and saved == profile.get("avatar") else None,
                "messages": count,
            })
        return authors

    def _export_avatars(self) -> None:
        if not self.bundle:
            return
        for author_id in self._totals():
            profile = self.profiles.get(author_id, {})
            avatar = profile.get("avatar")
            if not avatar:
                continue
            relative = f"avatars/{author_id}.png"
            fresh = profile.get("avatar_saved") == avatar and self.bundle.size_of(relative)
            if fresh or self._fetch_avatar(author_id, avatar, relative, profile):
                profile["avatar_saved"] = avatar
        self.bundle.write_json("profiles.json", self.profiles)

    def _fetch_avatar(self, author_id: str, avatar: str, relative: str, profile: dict) -> bool:
        dest = self.bundle.path(relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download(f"{CDN}/avatars/{author_id}/{avatar}.png", dest)
        except (DiscordError, httpx.HTTPError):
            self.report.failed_downloads.append(f"avatar of {profile.get('name', author_id)}")
            return False
        return True

    def _write_server(self) -> None:
        if not self.bundle:
            return
        self.bundle.write_json("server.json", {
            "format": FORMAT,
            "source": {
                "platform": "discord",
                "server_id": self.options.server_id,
                "server_name": self.server_name,
                "exported_at": self.exported_at,
            },
            "categories": self.categories,
            "channels": [{k: v for k, v in c.items() if k != "last_message_id"} for c in self.channels],
            "authors": self._authors(),
            "emoji": self.emoji_entries,
            "unreadable": self.unreadable,
        })


def _size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_report(report: Report) -> str:
    lines = [f"Server: {report.server_name}"]
    if report.dry_run:
        lines.append("Dry run: nothing was written and no files were downloaded.")
    for c in report.channels:
        lines.append(
            f"  #{c.name} ({c.kind}): {c.messages} messages, "
            f"{c.threads} threads ({c.thread_messages} messages), "
            f"{c.attachments} attachments ({_size(c.attachment_bytes)})")

    def total(attr: str) -> int:
        return sum(getattr(c, attr) for c in report.channels)

    lines.append(
        f"Total: {total('messages')} messages, {total('threads')} threads "
        f"({total('thread_messages')} messages), {total('attachments')} attachments "
        f"({_size(total('attachment_bytes'))}), {report.authors} authors, {report.emoji} custom emoji")
    for title, items in (
        ("Private channels", report.private),
        ("Unreadable channels", report.unreadable),
        ("Over the size limit, not downloaded", report.too_big),
        ("Downloads that failed", report.failed_downloads),
        ("Notes", report.notes),
    ):
        if items:
            lines.append(f"{title}: " + "; ".join(items))
    return "\n".join(lines)
