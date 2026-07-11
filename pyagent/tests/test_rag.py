from agent.rag import DocumentIndex, tokenize


def test_tokenize():
    assert tokenize("Hello, World! 123") == ["hello", "world", "123"]


def test_index_search(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("Python is a programming language. Snakes are reptiles.")
    index = DocumentIndex(chunk_size=50)
    added = index.add_file(doc)
    assert added >= 1
    results = index.search("programming language")
    assert results
    assert "programming" in results[0][1].text.lower()


def test_empty_search():
    index = DocumentIndex()
    assert index.search("anything") == []
