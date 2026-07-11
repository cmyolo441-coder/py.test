from agent.cache import TTLCache, cached, make_key


def test_cache_hit_and_miss():
    cache = TTLCache(maxsize=2, ttl=100)
    assert cache.get("a") is None
    cache.set("a", 1)
    assert cache.get("a") == 1
    assert cache.hits == 1
    assert cache.misses == 1


def test_cache_eviction():
    cache = TTLCache(maxsize=2, ttl=100)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("c") == 3


def test_cached_decorator():
    cache = TTLCache()
    calls = {"n": 0}

    @cached(cache)
    def slow(x):
        calls["n"] += 1
        return x * 2

    assert slow(3) == 6
    assert slow(3) == 6
    assert calls["n"] == 1


def test_make_key_stable():
    assert make_key(1, b=2) == make_key(1, b=2)
