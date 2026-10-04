import json

from zet_discord.bundle import Bundle, safe_filename


def msg(n):
    return {"id": str(n)}


def chunks(bundle, cid):
    return [[m["id"] for m in bundle.read_chunk(cid, n)] for n in bundle.chunk_numbers(cid)]


def test_write_json_is_atomic_and_leaves_no_temp(tmp_path):
    bundle = Bundle(tmp_path)
    bundle.write_json("a/b.json", {"x": "é"})
    assert json.loads((tmp_path / "a/b.json").read_text(encoding="utf-8")) == {"x": "é"}
    assert [p.name for p in (tmp_path / "a").iterdir()] == ["b.json"]


def test_messages_are_split_into_numbered_chunks(tmp_path):
    bundle = Bundle(tmp_path)
    bundle.append_messages("1", [msg(i) for i in range(1, 6)], chunk_size=2)
    assert chunks(bundle, "1") == [["1", "2"], ["3", "4"], ["5"]]


def test_appending_tops_up_the_last_chunk_without_duplicates(tmp_path):
    bundle = Bundle(tmp_path)
    bundle.append_messages("1", [msg(i) for i in range(1, 4)], chunk_size=2)
    bundle.append_messages("1", [msg(i) for i in range(4, 7)], chunk_size=2)
    assert chunks(bundle, "1") == [["1", "2"], ["3", "4"], ["5", "6"]]


def test_chunks_sort_numerically(tmp_path):
    bundle = Bundle(tmp_path)
    bundle.append_messages("1", [msg(i) for i in range(1, 12)], chunk_size=1)
    assert bundle.chunk_numbers("1") == list(range(1, 12))


def test_safe_filename():
    assert safe_filename("a/b\\c:d.png") == "a_b_c_d.png"
    assert safe_filename("..hidden") == "hidden"
    assert safe_filename("") == "file"
    assert safe_filename("x" * 300 + ".txt").endswith(".txt")
    assert len(safe_filename("x" * 300 + ".txt")) == 150
