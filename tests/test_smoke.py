import zet_discord
from zet_discord.__main__ import main


def test_main_prints_version(capsys):
    assert main() == 0
    assert zet_discord.__version__ in capsys.readouterr().out
