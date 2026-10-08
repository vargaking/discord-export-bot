# Bundle details

The format is defined by the importer (ping-server, `docs/bundle-format.md`, format version 1). This page only covers what the exporter decides.

## Files beyond the format

- `channels/<id>/progress.json`: where the last run stopped for that channel. `last_id` is the last message id read, `chunks` the number of message chunk files, `authors` message counts per author, and `threads` the same per thread. The importer ignores it.
- `profiles.json`: author names and avatar hashes seen so far, so a resumed run doesn't forget older authors.

## Zip

- After a run that isn't a dry run, `<out>.zip` is written next to the folder (`--no-zip` skips it). The folder stays; it is what the next run continues from.
- `server.json` is at the zip's root and paths use forward slashes. Left out: `profiles.json`, every `channels/<id>/progress.json`, and leftover `.tmp` and `.part` files (under `files/` only `.part` is left out; an attachment called `notes.tmp` or `progress.json` is kept).
- Entries are sorted by path. `.json` entries are deflated, everything else is stored, since media doesn't compress.
- It is written to `<out>.zip.tmp` and renamed when complete, so a failed or interrupted run never leaves a half-written zip. The next run builds it again.

## Messages

- Chunks hold 1000 messages and are numbered `1.json`, `2.json`, ... A resumed run tops up the last chunk. A message is never in two chunks.
- Messages are read 100 at a time, walking forward with `after`, which is what makes resuming work.
- Kept: normal messages and replies. A thread's starter-message pointer becomes the starter's own content. Everything else Discord marks as a system message is skipped. Forwarded messages keep their text but not the forward link.
- Timestamps are UTC to the second, like the sample.
- `<@!id>` becomes `<@id>`. `<#id>` becomes `#name`, `<@&id>` becomes `@name` and `<:name:id>` becomes `:name:`. A deleted channel or role reads `#deleted-channel` or `@deleted-role`.
- Reactions are `{emoji, count}`; custom emoji are written as `:name:`.
- Embeds without a url are dropped. `image_url` is the original address, never Discord's proxy.

## Threads

- A forum post, or a thread in a text channel, is `channels/<channel id>/threads/<thread id>.json`. Active and archived public threads are read; private threads are not.
- `pinned` is the thread flag with value 2, `locked` comes from the thread metadata, `created_at` from its create time or else from the id.
- If the opening message was deleted, `messages` starts with the first one left. A thread with no messages left is not written.

## Private and unreadable

- A channel is `private` when it has an `@everyone` permission overwrite denying View Channel. If it has none of its own, its category's overwrite is used. Role and member overwrites are ignored.
- A 403 when listing messages puts the channel in `unreadable`; it stays in `channels` with no files. A 403 when listing archived threads is noted in the run summary and the channel carries on.

## Files

- Attachments go to `files/<attachment id>/<filename>` with unsafe characters in the name replaced. Larger than `--max-file-mb`, or failed downloads: `path` is `null` and the file is listed in the summary.
- Avatars go to `avatars/<author id>.png`, emoji to `emoji/<id>.png` or `.gif`. A file that is already there with the right size is not downloaded again.

## Resuming and `server.json`

- `server.json` is written atomically (temp file and rename) before the channels, after each channel and at the end, so an interrupted run leaves a bundle the importer can read. The last write adds the avatars.
- If a chunk file is ahead of its `progress.json` (a crash between the two writes), that channel is read again from the start.
- `--refresh` deletes a channel's messages, threads and progress and reads it again. Downloaded files stay and are reused; files of deleted messages are left behind.
- `--only` limits what is read. Channels exported by earlier runs stay in `server.json`.
- `--dry-run` ignores any earlier bundle and writes nothing.
