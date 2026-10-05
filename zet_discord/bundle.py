"""Reading and writing the bundle folder. Every write is atomic."""
import json
import os
import re
import shutil
from pathlib import Path

FORMAT = 1


class Bundle:
    def __init__(self, root: Path):
        self.root = root

    def path(self, relative: str) -> Path:
        return self.root / relative

    def write_json(self, relative: str, data) -> None:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def read_json(self, relative: str, default=None):
        try:
            return json.loads(self.path(relative).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return default

    def exists(self, relative: str) -> bool:
        return self.path(relative).is_file()

    def size_of(self, relative: str) -> int | None:
        try:
            return self.path(relative).stat().st_size
        except FileNotFoundError:
            return None

    def reset_channel(self, channel_id: str) -> None:
        shutil.rmtree(self.path(f"channels/{channel_id}"), ignore_errors=True)

    def has_channel_progress(self, channel_id: str) -> bool:
        return self.exists(f"channels/{channel_id}/progress.json")

    def chunk_numbers(self, channel_id: str) -> list[int]:
        directory = self.path(f"channels/{channel_id}/messages")
        if not directory.is_dir():
            return []
        numbers = [int(p.stem) for p in directory.iterdir() if re.fullmatch(r"\d+\.json", p.name)]
        return sorted(numbers)

    def read_chunk(self, channel_id: str, number: int) -> list[dict]:
        return self.read_json(f"channels/{channel_id}/messages/{number}.json", [])

    def append_messages(self, channel_id: str, messages: list[dict], chunk_size: int) -> int:
        """Add messages after the existing ones, topping up the last chunk
        before starting new ones. Returns the number of chunks."""
        numbers = self.chunk_numbers(channel_id)
        number = numbers[-1] if numbers else 1
        current = self.read_chunk(channel_id, number) if numbers else []
        pending = list(messages)
        while pending:
            if len(current) >= chunk_size:
                number += 1
                current = []
            room = chunk_size - len(current)
            current = current + pending[:room]
            pending = pending[room:]
            self.write_json(f"channels/{channel_id}/messages/{number}.json", current)
        return number if numbers or messages else 0

    def thread_path(self, channel_id: str, thread_id: str) -> str:
        return f"channels/{channel_id}/threads/{thread_id}.json"


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", name).strip().lstrip(".")
    if len(cleaned) > 150:
        stem, dot, ext = cleaned.rpartition(".")
        cleaned = (stem[: 150 - len(ext) - 1] + dot + ext) if dot and len(ext) < 20 else cleaned[:150]
    return cleaned or "file"
