from agent.fileops import (
    append_file, copy_file, delete_lines, diff_files, edit_file,
    insert_at_line, read_lines, regex_replace, search_in_files, write_file,
)


def test_write_read_edit(tmp_path):
    f = tmp_path / "a.txt"
    write_file(str(f), "hello world")
    assert "1: hello world" in read_lines(str(f))
    edit_file(str(f), "world", "there")
    assert "there" in read_lines(str(f))


def test_append(tmp_path):
    f = tmp_path / "a.txt"
    write_file(str(f), "a")
    append_file(str(f), "b")
    assert f.read_text() == "ab"


def test_regex_replace(tmp_path):
    f = tmp_path / "a.txt"
    write_file(str(f), "cat cat dog")
    regex_replace(str(f), r"cat", "fish")
    assert f.read_text() == "fish fish dog"


def test_insert_and_delete_lines(tmp_path):
    f = tmp_path / "a.txt"
    write_file(str(f), "l1\nl2\nl3\n")
    insert_at_line(str(f), 2, "NEW")
    assert "NEW" in f.read_text()
    delete_lines(str(f), 1, 1)
    assert not f.read_text().startswith("l1")


def test_diff_and_copy(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    write_file(str(a), "x\n")
    copy_file(str(a), str(b))
    assert "identical" in diff_files(str(a), str(b))


def test_search(tmp_path):
    (tmp_path / "a.txt").write_text("find me here")
    out = search_in_files(str(tmp_path), "find me")
    assert "find me" in out
