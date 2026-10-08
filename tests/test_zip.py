import os
import zipfile

import pytest

from tests.fakediscord import ALICE, CDN, TOKEN, message, thread
from zet_discord import bundle as bundle_module


def attachment(id, filename, size=5):
    url = f"{CDN}/attachments/1/{id}/{filename}"
    return url, {"id": str(id), "filename": filename, "size": size, "content_type": None, "url": url}


@pytest.fixture
def populated(discord):
    """A guild whose export has messages, a thread, files, an avatar and an emoji."""
    discord.add_channel(10, "general")
    photo_url, photo = attachment(9, "photo.png")
    kept_url, kept = attachment(8, "progress.json")
    tmp_url, tmp = attachment(7, "notes.tmp")
    discord.files[photo_url] = b"\x89PNG-bytes"
    discord.files[kept_url] = b'{"mine": true}'
    discord.files[tmp_url] = b"scratch"
    discord.files[f"{CDN}/avatars/11/ahash.png"] = b"avatar"
    discord.emojis = [{"id": "70", "name": "party", "animated": False}]
    discord.files[f"{CDN}/emojis/70.png"] = b"emoji"
    discord.add_messages("10", message(1, attachments=[photo, kept, tmp]), message(2))
    discord.add_thread(thread(500, 10, "talk"), message(501, content="in thread"))
    return discord


def zip_names(path):
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def folder_files(root):
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_zip_holds_the_bundle_without_bookkeeping(populated, cli):
    code, out, err = cli()
    assert code == 0
    zip_path = cli.out.with_name("bundle.zip")
    names = zip_names(zip_path)
    assert names == sorted(names)
    assert "channels/10/progress.json" in folder_files(cli.out) and "profiles.json" in folder_files(cli.out)
    assert set(names) == folder_files(cli.out) - {"channels/10/progress.json", "profiles.json"}
    assert "server.json" in names
    assert not any(n.startswith(("bundle/", "/")) for n in names)
    assert "\\" not in "".join(names)


def test_attachments_named_like_bookkeeping_are_kept(populated, cli):
    cli()
    names = zip_names(cli.out.with_name("bundle.zip"))
    assert "files/8/progress.json" in names and "files/7/notes.tmp" in names


def test_leftover_temp_files_are_left_out(populated, cli):
    cli()
    (cli.out / "channels/10/messages/2.json.tmp").write_text("half")
    (cli.out / "server.json.tmp").write_text("half")
    (cli.out / "files/9/photo.png.part").write_bytes(b"half")
    cli()
    names = zip_names(cli.out.with_name("bundle.zip"))
    assert not [n for n in names if n.endswith(".part") or n.endswith("json.tmp")]
    assert "server.json" in names


def test_json_is_deflated_and_everything_else_stored(populated, cli):
    cli()
    with zipfile.ZipFile(cli.out.with_name("bundle.zip")) as archive:
        kinds = {i.filename: i.compress_type for i in archive.infolist()}
    assert kinds["server.json"] == zipfile.ZIP_DEFLATED
    assert kinds["channels/10/messages/1.json"] == zipfile.ZIP_DEFLATED
    assert kinds["channels/10/threads/500.json"] == zipfile.ZIP_DEFLATED
    assert kinds["files/9/photo.png"] == zipfile.ZIP_STORED
    assert kinds["avatars/11.png"] == zipfile.ZIP_STORED
    assert kinds["emoji/70.png"] == zipfile.ZIP_STORED


def test_unpacked_files_are_identical_to_the_folder(populated, cli, tmp_path):
    cli()
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(cli.out.with_name("bundle.zip")) as archive:
        assert archive.testzip() is None
        archive.extractall(unpacked)
    for relative in folder_files(unpacked):
        assert (unpacked / relative).read_bytes() == (cli.out / relative).read_bytes(), relative


def test_files_with_pre_1980_timestamps_are_zipped(populated, cli):
    cli()
    os.utime(cli.out / "files/9/photo.png", (0, 0))
    code, out, err = cli()
    assert code == 0
    assert "files/9/photo.png" in zip_names(cli.out.with_name("bundle.zip"))


def test_a_second_run_rebuilds_the_zip(populated, cli):
    cli()
    zip_path = cli.out.with_name("bundle.zip")
    populated.add_messages("10", message(3, content="newer"))
    code, out, err = cli()
    assert code == 0
    with zipfile.ZipFile(zip_path) as archive:
        assert b"newer" in archive.read("channels/10/messages/1.json")
    assert sorted(p.name for p in zip_path.parent.iterdir()) == ["bundle", "bundle.zip"]


def test_dry_run_writes_no_zip(populated, cli):
    code, out, err = cli("--dry-run")
    assert code == 0
    assert not list(cli.out.parent.iterdir())
    assert "Zip:" not in out and "Zipping" not in err


def test_no_zip_writes_the_folder_only(populated, cli):
    code, out, err = cli("--no-zip")
    assert code == 0
    assert (cli.out / "server.json").is_file()
    assert [p.name for p in cli.out.parent.iterdir()] == ["bundle"]
    assert "Zip:" not in out and "Zipping" not in err


def test_summary_names_the_zip_and_its_size(populated, cli):
    code, out, err = cli()
    zip_path = cli.out.with_name("bundle.zip")
    last_line = out.splitlines()[-1]
    assert last_line.startswith(f"Zip: {zip_path} (") and last_line.endswith(" KB)")
    assert f"Zipping the bundle to {zip_path}" in err


def test_a_trailing_slash_in_out_puts_the_zip_next_to_the_folder(populated, cli):
    code, out, err = cli(out=f"{cli.out}/")
    assert code == 0
    assert sorted(p.name for p in cli.out.parent.iterdir()) == ["bundle", "bundle.zip"]
    assert not list(cli.out.glob("*.zip"))


def test_a_relative_out_puts_the_zip_next_to_the_folder(populated, cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, err = cli(out="./bundle/")
    assert code == 0
    assert (tmp_path / "bundle.zip").is_file()
    assert f"Zip: {tmp_path / 'bundle.zip'}" in out


def test_the_token_is_not_in_the_zip(populated, cli):
    cli()
    with zipfile.ZipFile(cli.out.with_name("bundle.zip")) as archive:
        assert TOKEN not in "".join(archive.namelist())
        for name in archive.namelist():
            assert TOKEN.encode() not in archive.read(name), name


def break_midway(monkeypatch):
    original = zipfile.ZipFile.write
    written = []

    def write(self, filename, arcname=None, **kwargs):
        if len(written) == 2:
            raise OSError("disk full")
        written.append(arcname)
        return original(self, filename, arcname, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", write)


def test_a_failure_while_zipping_leaves_no_zip_and_no_temp_file(populated, cli, monkeypatch):
    break_midway(monkeypatch)
    code, out, err = cli()
    assert code == 1
    assert "Couldn't write the zip (disk full)" in err and "run the command again" in err
    assert "Zip:" not in out and "Total:" in out
    assert [p.name for p in cli.out.parent.iterdir()] == ["bundle"]


def test_a_failure_leaves_an_older_zip_untouched(populated, cli, monkeypatch):
    cli()
    zip_path = cli.out.with_name("bundle.zip")
    before = zip_path.read_bytes()
    populated.add_messages("10", message(3, content="newer"))
    break_midway(monkeypatch)
    code, out, err = cli()
    assert code == 1
    assert zip_path.read_bytes() == before
    assert sorted(p.name for p in zip_path.parent.iterdir()) == ["bundle", "bundle.zip"]


def test_an_interrupted_zip_leaves_no_temp_file(populated, cli, monkeypatch):
    def interrupt(self, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(bundle_module.zipfile.ZipFile, "write", interrupt)
    with pytest.raises(KeyboardInterrupt):
        cli()
    assert [p.name for p in cli.out.parent.iterdir()] == ["bundle"]


def test_exporter_alone_writes_no_zip(populated, run_export):
    run_export()
    assert [p.name for p in run_export.out.parent.iterdir()] == ["bundle"]
