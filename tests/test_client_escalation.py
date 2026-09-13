"""Unit tests for ChatClient empty-content escalation ladder.

Run: python tests/test_client_escalation.py
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wl_benchmark.client import ChatClient

captured = []


class FakeResponse:
    def __init__(self, payload):
        self._d = json.dumps(payload).encode()
        choice = (payload.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        delta = {"content": msg.get("content"),
                 "reasoning_content": None, "tool_calls": None}
        sse_choice = {"delta": delta,
                      "finish_reason": choice.get("finish_reason")}
        self._sse = ("data: " + json.dumps(
            {"choices": [sse_choice], "usage": payload.get("usage", {})},
            ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode()

    def read(self):
        return self._d

    def __iter__(self):
        return iter(self._sse.splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


GOOD = {"choices": [{"finish_reason": "stop",
                     "message": {"content": "答案"}}], "usage": {}}
EMPTY = {"choices": [{"finish_reason": "length",
                      "message": {"content": None,
                                  "reasoning_content": "思" * 100}}],
         "usage": {"completion_tokens": 8192}}


def make(steps):
    cl = ChatClient("http://x", "k", max_retries=6)
    state = {"i": 0}

    def opener_open(req, timeout=None):
        body = json.loads(req.data.decode())
        captured.append({"max_tokens": body.get("max_tokens"),
                         "reasoning_effort": body.get("reasoning_effort")})
        kind, payload = steps[state["i"]]
        state["i"] += 1
        if kind == "400":
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {},
                                         FakeResponse({"error": "no"}))
        return FakeResponse(payload)

    cl._opener.open = opener_open
    return cl


results = []


def run(name, steps, check):
    captured.clear()
    cl = make(steps)
    r = cl.chat("glm", [{"role": "user", "content": "x"}])
    ok, why = check(r, captured)
    print(("PASS" if ok else "FAIL") + f": {name}"
          + (f" — {why}" if not ok else ""))
    results.append(ok)


# 1. 默认: 显式大额 max_tokens（顶开服务端默认上限）
run("server output cap lifted by default", [("resp", GOOD)],
    lambda r, c: (c[0]["max_tokens"] == 8192
                  and c[0]["reasoning_effort"] is None, ""))

# 2. 空内容 → 阶梯: low(65536) → low(server default) → 成功
run("ladder: low kept, budget dropped, content obtained",
    [("resp", EMPTY), ("400", None), ("resp", GOOD)],
    lambda r, c: (
        r.content == "答案"
        and c[0] == {"max_tokens": 8192, "reasoning_effort": None}
        and c[1] == {"max_tokens": 8192, "reasoning_effort": "low"}
        and c[2] == {"max_tokens": None, "reasoning_effort": "low"}, ""))

# 3. 空内容无 400: low 重试即成功（预算保留）
run("ladder without 400",
    [("resp", EMPTY), ("resp", GOOD)],
    lambda r, c: (
        r.content == "答案"
        and c[0]["reasoning_effort"] is None
        and c[1] == {"max_tokens": 8192, "reasoning_effort": "low"}, ""))

# 4. 全部失败 → 诊断带尝试序列
run("all-fail diag with attempt history",
    [("resp", EMPTY), ("resp", EMPTY), ("resp", EMPTY)],
    lambda r, c: (
        r.error and "empty content" in r.error
        and "tried: reasoning_effort=low; server-default budget" in r.error, ""))

# 5. 正常回复: 不注入任何参数
run("normal reply: no extra params", [("resp", GOOD)],
    lambda r, c: (c[0]["reasoning_effort"] is None
                  and c[0]["max_tokens"] == 8192, ""))

n_ok = sum(results)
print("-" * 40)
print(f"{n_ok}/{len(results)} passed")
sys.exit(0 if n_ok == len(results) else 1)
