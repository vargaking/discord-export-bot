"""A configurable fake Discord guild served through httpx.MockTransport."""
import httpx

TOKEN = "fake-token-0123456789"
GUILD = "900"
VIEW_CHANNEL = 1 << 10
CDN = "https://cdn.discordapp.com"

ALICE = {"id": "11", "username": "alice", "global_name": "Alice A", "avatar": "ahash"}
BOB = {"id": "12", "username": "bob", "global_name": None, "avatar": None}


def message(id, author=ALICE, content="hello", **extra):
    base = {
        "id": str(id), "type": 0, "author": author, "content": content,
        "timestamp": "2024-03-01T10:00:00.000000+00:00", "edited_timestamp": None,
        "pinned": False, "attachments": [], "embeds": [], "reactions": [],
    }
    return base | extra


def channel(id, name, type=0, **extra):
    base = {"id": str(id), "name": name, "type": type, "position": 0, "parent_id": None,
            "topic": None, "permission_overwrites": [], "last_message_id": None}
    return base | extra


def thread(id, parent, name, type=11, archived=False, **extra):
    meta = {"archived": archived, "locked": False, "archive_timestamp": f"2024-04-{int(id) % 28 + 1:02d}T00:00:00+00:00",
            "create_timestamp": "2024-03-05T09:00:00+00:00"}
    base = {"id": str(id), "parent_id": str(parent), "name": name, "type": type,
            "thread_metadata": meta, "flags": 0, "applied_tags": []}
    return base | extra


def deny_everyone_view():
    return {"id": GUILD, "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}


class FakeDiscord:
    def __init__(self, name="Sample Guild"):
        self.name = name
        self.roles = [{"id": GUILD, "name": "@everyone"}]
        self.channels = []
        self.messages: dict[str, list[dict]] = {}
        self.threads: list[dict] = []
        self.emojis: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.forbidden_messages: set[str] = set()
        self.forbidden_archived: set[str] = set()
        self.broken_messages: set[str] = set()
        self.archived_page_size = 100
        self.rate_limit_first = 0
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self.handle)

    def add_channel(self, id, name, type=0, **extra):
        self.channels.append(channel(id, name, type, **extra))
        self.messages.setdefault(str(id), [])
        return str(id)

    def add_messages(self, channel_id, *messages):
        self.messages.setdefault(str(channel_id), []).extend(messages)

    def add_thread(self, thread_object, *messages):
        self.threads.append(thread_object)
        self.messages[thread_object["id"]] = list(messages)

    def message_urls(self, *urls):
        for url in urls:
            self.files[url] = b"data:" + url.encode()

    def paths(self, prefix=""):
        return [r.url.path.removeprefix("/api/v10") for r in self.requests
                if r.url.host == "discord.com" and r.url.path.removeprefix("/api/v10").startswith(prefix)]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "cdn.discordapp.com":
            content = self.files.get(str(request.url).split("?")[0])
            return httpx.Response(200, content=content) if content is not None else httpx.Response(404)
        if request.headers.get("Authorization") != f"Bot {TOKEN}":
            return httpx.Response(401, json={"message": "401: Unauthorized"})
        path = request.url.path.removeprefix("/api/v10")
        params = request.url.params
        if path == f"/guilds/{GUILD}":
            return httpx.Response(200, json={"id": GUILD, "name": self.name, "roles": self.roles})
        if path == f"/guilds/{GUILD}/channels":
            return httpx.Response(200, json=self.channels)
        if path == f"/guilds/{GUILD}/emojis":
            return httpx.Response(200, json=self.emojis)
        if path == f"/guilds/{GUILD}/threads/active":
            active = [t for t in self.threads if not t["thread_metadata"]["archived"]]
            return httpx.Response(200, json={"threads": active, "members": []})
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "channels" and parts[2] == "messages":
            return self.list_messages(parts[1], params)
        if len(parts) == 5 and parts[2:] == ["threads", "archived", "public"]:
            return self.list_archived(parts[1], params)
        return httpx.Response(404, json={"message": "Unknown"})

    def list_messages(self, channel_id, params):
        if self.rate_limit_first > 0:
            self.rate_limit_first -= 1
            return httpx.Response(429, json={"message": "You are being rate limited.", "retry_after": 0.5})
        if channel_id in self.broken_messages:
            return httpx.Response(400, json={"message": "Bad request"})
        if channel_id in self.forbidden_messages:
            return httpx.Response(403, json={"code": 50001, "message": "Missing Access"})
        after = int(params.get("after", 0))
        limit = int(params.get("limit", 50))
        page = sorted((m for m in self.messages.get(channel_id, []) if int(m["id"]) > after), key=lambda m: int(m["id"]))
        return httpx.Response(200, json=list(reversed(page[:limit])))

    def list_archived(self, channel_id, params):
        if channel_id in self.forbidden_archived:
            return httpx.Response(403, json={"code": 50001, "message": "Missing Access"})
        before = params.get("before")
        archived = [t for t in self.threads if t["parent_id"] == channel_id and t["thread_metadata"]["archived"]
                    and (before is None or t["thread_metadata"]["archive_timestamp"] < before)]
        archived.sort(key=lambda t: t["thread_metadata"]["archive_timestamp"], reverse=True)
        limit = min(int(params.get("limit", 50)), self.archived_page_size)
        return httpx.Response(200, json={"threads": archived[:limit], "members": [], "has_more": len(archived) > limit})
