from agent.auth import AuthManager, hash_key


def test_generate_and_authenticate():
    mgr = AuthManager()
    key = mgr.create_key(role="admin", label="ci")
    record = mgr.authenticate(key)
    assert record is not None
    assert record.role == "admin"


def test_permissions():
    mgr = AuthManager()
    key = mgr.create_key(role="readonly")
    assert mgr.has_permission(key, "read")
    assert not mgr.has_permission(key, "write")


def test_invalid_key():
    mgr = AuthManager()
    assert mgr.authenticate("bogus") is None
    assert not mgr.has_permission("bogus", "read")


def test_hash_stable():
    assert hash_key("x") == hash_key("x")
