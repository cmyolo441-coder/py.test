import asyncio


from agent.async_engine import (
    AsyncTaskRunner, async_retry, gather_with_timeout, run_concurrent,
)


def test_run_concurrent():
    async def make(n):
        await asyncio.sleep(0.01)
        return n * 2

    async def main():
        return await run_concurrent([make(i) for i in range(5)], limit=2)

    results = asyncio.run(main())
    assert sorted(results) == [0, 2, 4, 6, 8]


def test_gather_with_timeout():
    async def slow():
        await asyncio.sleep(1)
        return "done"

    async def fast():
        return "quick"

    async def main():
        return await gather_with_timeout([slow(), fast()], timeout=0.05)

    results = asyncio.run(main())
    assert results[0] is None
    assert results[1] == "quick"


def test_async_retry_succeeds():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ValueError("fail")
        return "ok"

    async def main():
        return await async_retry(flaky, attempts=3, base_delay=0.01)

    assert asyncio.run(main()) == "ok"
    assert calls["n"] == 2


def test_task_runner():
    async def main():
        runner = AsyncTaskRunner()

        async def work(n):
            await asyncio.sleep(0.01)
            return n

        runner.spawn(work(1))
        runner.spawn(work(2))
        results = await runner.drain()
        return sorted(r for r in results if isinstance(r, int))

    assert asyncio.run(main()) == [1, 2]
