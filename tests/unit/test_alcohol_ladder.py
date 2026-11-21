"""The alcohol vocabulary distinguishes explicit alcohol from generic venues."""
import re
from pathlib import Path

import yaml


def test_alcohol_words_and_word_boundaries():
    config = yaml.safe_load((Path(__file__).resolve().parents[2] / "config/config.yaml").read_text())
    ladder = config["categories"]["alcohol"]["kind"]["schemes"]["ru_text"]["ladder"]

    def classify(text):
        return next((label for label, pattern in ladder if re.search(pattern, text.lower())), None)

    assert classify("Почтовая контора 1786 г.") is None
    assert classify("почта") == "no"
    assert classify("ресторан") is None
    assert classify("банкетный зал") is None
    assert classify("безалкогольный") is None
    assert classify("алкоголь") == "yes"
    assert classify("алкомаркет") == "yes"
    assert classify("аптека") == "no"
    assert classify("аптекарь вино") == "no"
