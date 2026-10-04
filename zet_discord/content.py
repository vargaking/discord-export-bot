"""Turning Discord messages into bundle messages."""
import re
from datetime import datetime, timezone

DISCORD_EPOCH_MS = 1420070400000

_USER_MENTION = re.compile(r"<@!(\d+)>")
_CHANNEL_MENTION = re.compile(r"<#(\d+)>")
_ROLE_MENTION = re.compile(r"<@&(\d+)>")
_CUSTOM_EMOJI = re.compile(r"<a?:(\w+):\d+>")


def flatten(content: str, channel_names: dict[str, str], role_names: dict[str, str]) -> str:
    """Discord markdown without the Discord-only parts.

    User mentions stay <@id> for the importer to map; the rest becomes text.
    """
    content = _USER_MENTION.sub(r"<@\1>", content)
    content = _CHANNEL_MENTION.sub(
        lambda m: "#" + channel_names.get(m.group(1), "deleted-channel"), content)
    content = _ROLE_MENTION.sub(
        lambda m: "@" + role_names.get(m.group(1), "deleted-role"), content)
    return _CUSTOM_EMOJI.sub(r":\1:", content)


def snowflake_time(snowflake: str) -> datetime:
    millis = (int(snowflake) >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def iso(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reaction_label(emoji: dict) -> str:
    return f":{emoji['name']}:" if emoji.get("id") else emoji["name"]


def embeds_of(raw_embeds: list[dict]) -> list[dict]:
    """Embeds that have a url, with the original image address."""
    embeds = []
    for embed in raw_embeds:
        if not embed.get("url"):
            continue
        image = embed.get("image") or embed.get("thumbnail") or {}
        embeds.append({
            "url": embed["url"],
            "title": embed.get("title"),
            "description": embed.get("description"),
            "site_name": (embed.get("provider") or {}).get("name"),
            "image_url": image.get("url"),
        })
    return embeds
