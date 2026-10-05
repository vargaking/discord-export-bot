# discord-export-bot

Exports a Discord server to a folder (a bundle) that Zeta Chat can import. You run it yourself, with your own bot. Nothing is uploaded anywhere, and it only talks to Discord.

Python 3.13 and `httpx`. No Discord library.

## What you get

Server name, categories, and text, announcement, voice and forum channels. Every message of every channel the bot can read, oldest first, with attachments (downloaded, because Discord's links expire), embeds and reaction counts. Forum posts, threads and the text chat of voice channels. Authors with avatars, and custom emoji.

Left out: roles and permissions (channels are only marked `private`), private threads, stage channels, stickers, who reacted, system messages, DMs.

## 1. Create the bot

You need to own the server.

1. Open the [Discord developer portal](https://discord.com/developers/applications) and create a new application.
2. Under Bot, turn on the Message Content intent and copy the token. Treat it like a password.
3. Under OAuth2 > URL Generator, pick the `bot` scope and the permissions View Channels and Read Message History. Open the generated link and add the bot to your server.
4. Make sure the bot can see the channels you want. Channels it can't see are listed as unreadable, not an error.
5. Find the server id: turn on Developer Mode in Discord's settings, then right-click the server icon and Copy Server ID.

## 2. Install

```
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DISCORD_BOT_TOKEN=...   # the token is only read from here
```

## 3. Dry run

```
.venv/bin/python -m zet_discord export --server <server id> --out ./bundle --dry-run
```

Reads everything except the files and writes nothing. It prints, per channel, the messages, threads and attachments with their total size, then the totals, authors, private channels and unreadable channels. Attachments over the size limit (25 MB, change with `--max-file-mb`, 0 for no limit) are listed and will not be downloaded.

## 4. Export

```
.venv/bin/python -m zet_discord export --server <server id> --out ./bundle
```

It can take a long time on a big server, because Discord limits how fast it can be read. Stop and run the same command again at any time: it continues after the last exported message of each channel and picks up anything newer. Use `--refresh` to read everything again, to catch edits and deleted messages. Use `--only <channel id> ...` to export just some channels.

## 5. Hand over the folder

Send the whole folder to whoever imports it into Zeta Chat. It holds your members' messages, so share it only with them. The bot token is never written to it.

Bundle layout and the decisions behind it: [docs/bundle.md](docs/bundle.md).

## Development

```
pip install -r requirements-dev.txt
pytest
```

Tests fake Discord with `httpx.MockTransport`; nothing touches the network. The bundle format itself is owned by the importer (ping-server `docs/bundle-format.md`); `tests/fixtures/bundle` is its sample.
