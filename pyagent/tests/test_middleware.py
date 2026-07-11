from agent.middleware import Pipeline, redaction_middleware


def test_pipeline_order():
    trace = []

    def handler(ctx):
        trace.append("handler")
        return ctx

    def mw_a(ctx, nxt):
        trace.append("a-before")
        r = nxt(ctx)
        trace.append("a-after")
        return r

    pipe = Pipeline(handler).use(mw_a)
    pipe.run({})
    assert trace == ["a-before", "handler", "a-after"]


def test_redaction():
    def handler(ctx):
        return ctx

    pipe = Pipeline(handler).use(redaction_middleware)
    result = pipe.run({"input": "my key is sk-abcdefgh12345678"})
    assert "[REDACTED]" in result["input"]
