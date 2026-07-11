import time

from agent.ratelimit import RateLimiter


def test_capacity_limits():
    rl = RateLimiter(rate=1, capacity=2)
    assert rl.try_acquire()
    assert rl.try_acquire()
    assert not rl.try_acquire()


def test_refill():
    rl = RateLimiter(rate=100, capacity=1)
    assert rl.try_acquire()
    assert not rl.try_acquire()
    time.sleep(0.05)
    assert rl.try_acquire()
