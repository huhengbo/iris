from datetime import datetime

from iris_source_115.p115 import _entry


def _file(**extra):
    return {"fid": "1", "cid": "2", "n": "a.mkv", "s": 1, "pc": "pc", "sha": "ab", **extra}


def test_created_at_prefers_tp_timestamp() -> None:
    assert _entry(_file(tp="1577934245", t="2020-01-01 00:00")).created_at == 1577934245


def test_created_at_falls_back_to_t_text_and_ignores_directories() -> None:
    expected = int(datetime.strptime("2020-01-02 03:04", "%Y-%m-%d %H:%M").astimezone().timestamp())
    assert _entry(_file(t="2020-01-02 03:04")).created_at == expected
    assert _entry(_file()).created_at is None
    assert _entry({"cid": "9", "n": "dir", "tp": "1577934245"}).created_at is None
