import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import httpx

from zet_discord.api import DiscordClient, DiscordError
from zet_discord.export import ExportError, Exporter, Options, format_report

TOKEN_VARIABLE = "DISCORD_BOT_TOKEN"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m zet_discord")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="export a Discord server to a folder")
    export.add_argument("--server", required=True, help="the Discord server id")
    export.add_argument("--out", required=True, type=Path, help="the folder to write the bundle to")
    export.add_argument("--only", nargs="+", default=[], metavar="CHANNEL", help="export only these channel ids")
    export.add_argument("--max-file-mb", type=float, default=25,
                        help="don't download attachments above this size; 0 means no limit (default 25)")
    export.add_argument("--dry-run", action="store_true",
                        help="read everything except the files, write nothing, print what would be exported")
    export.add_argument("--refresh", action="store_true",
                        help="read every channel again instead of continuing, to pick up edits and deletions")
    return parser


def main(argv=None, *, environ: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None,
         sleep=None, stdout=None, stderr=None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    environ = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)

    token = environ.get(TOKEN_VARIABLE, "").strip()
    if not token:
        print(f"{TOKEN_VARIABLE} is not set. Put the bot token in that environment variable.", file=stderr)
        return 2
    if args.max_file_mb < 0:
        print("--max-file-mb can't be negative", file=stderr)
        return 2

    options = Options(
        server_id=args.server,
        out=args.out,
        only=args.only,
        max_file_bytes=int(args.max_file_mb * 1024 * 1024),
        dry_run=args.dry_run,
        refresh=args.refresh,
    )
    client_options = {"transport": transport, **({"sleep": sleep} if sleep else {})}
    client = DiscordClient(token, **client_options)
    try:
        report = Exporter(client, options, log=lambda line: print(line, file=stderr)).run()
    except ExportError as error:
        print(error, file=stderr)
        return 2
    except DiscordError as error:
        print(f"{error}. {_hint(error.status)}", file=stderr)
        return 1
    except httpx.HTTPError as error:
        print(f"Network problem ({type(error).__name__}). Run the command again to continue.", file=stderr)
        return 1
    finally:
        client.close()
    print(format_report(report), file=stdout)
    return 0


def _hint(status: int) -> str:
    if status == 401:
        return "Check the bot token."
    if status in (403, 404):
        return "Check the server id and that the bot has been invited to the server."
    return "Run the command again to continue."


if __name__ == "__main__":
    sys.exit(main())
