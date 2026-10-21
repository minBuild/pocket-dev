"""code_sig — 서버 코드가 바뀌었는지 알아보는 서명 (절대 raise 하지 않음)."""
from server.config import code_sig


def test_code_sig_changes(tmp_path):
    a = tmp_path / "a.py"
    a.write_text("x = 1", encoding="utf-8")
    s1 = code_sig([a])
    assert s1 == code_sig([a])
    a.write_text("x = 2", encoding="utf-8")
    assert code_sig([a]) != s1


def test_code_sig_missing_file_ok(tmp_path):
    assert isinstance(code_sig([tmp_path / ".env"]), str)


def test_code_sig_never_raises(tmp_path):
    d = tmp_path / "x.py"
    d.mkdir()
    assert code_sig([d]) is None


def test_code_sig_default():
    assert isinstance(code_sig(), str)
