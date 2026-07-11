from agent.database import Database


def test_conversation_roundtrip(tmp_path):
    db = Database(tmp_path / "test.db")
    cid = db.create_conversation("chat1")
    db.add_message(cid, "user", "hi")
    db.add_message(cid, "assistant", "hello")
    msgs = db.get_messages(cid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    db.close()


def test_list_conversations(tmp_path):
    db = Database(tmp_path / "test.db")
    db.create_conversation("a")
    db.create_conversation("b")
    assert len(db.list_conversations()) == 2
    db.close()


def test_audit(tmp_path):
    db = Database(tmp_path / "test.db")
    db.audit("login", {"user": "x"})
    db.close()
