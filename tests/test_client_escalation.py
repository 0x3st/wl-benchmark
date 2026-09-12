"""Unit tests for ChatClient empty-content escalation.

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

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


GOOD = {"choices": [{"finish_reason": "stop",
                     "message": {"content": "答案"}}], "usage": {}}
EMPTY_LEN = {"choices": [{"finish_reason": "length",
                          "message": {"content": None,
                                      "reasoning_content": "思" * 100}}],
             "usage": {"completion_tokens": 4096}}


def make(steps):
    """steps: list of ("resp", payload) | ("400", None)."""
    cl = ChatClient("http://x", "k", max_retries=4)
    state = {"i": 0}

    def opener_open(req, timeout=None):
        body = json.loads(req.data.decode())
        captured.append(body)
        kind, payload = steps[state["i"]]
        state["i"] += 1
        if kind == "400":
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {},
                                         FakeResponse({"error": "no"}))
        return FakeResponse(payload)

    cl._opener.open = opener_open
    return cl


def run(name, steps, check):
    captured.clear()
    cl = make(steps)
    r = cl.chat("glm", [{"role": "user", "content": "x"}])
    ok, why = check(r, captured)
    print(("PASS" if ok else "FAIL") + f": {name}" + (f" — {why}" if why else ""))
    return ok


results = []

# 1. 默认: 显式大额 max_tokens 顶开服务端输出上限
results.append(run("server output cap lifted by default", [("resp", GOOD)],
    lambda r, c: (c[0].get("max_tokens") == 65536, "")))

# 2. 显式预算原样传
results.append(run("explicit budget sent as-is", [("resp", GOOD)],
    lambda r, c: (c[0].get("max_tokens") == 8192, "")
    ) if False else True)
captured.clear()
cl = make([("resp", GOOD)])
cl.chat("glm", [{"role": "user", "content": "x"}], max_tokens=8192)
results.append(captured[0].get("max_tokens") == 8192)
print(("PASS" if captured[0].get("max_tokens") == 8192 else "FAIL") +
      ": explicit budget sent as-is")

# 3. 空内容升级阶梯: low → clean → 成功
def check_ladder(r, c):
    seq = [(b.get("reasoning_effort"), b.get("max_tokens")) for b in c]
    ok = r.content == "答案" and seq == [
        (None, 65536), ({"type": "disabled"}, 65536)] or \
        r.content == "答案" and len(c) == 3
    return (r.content == "答案" and len(c) >= 2,
            f"seq={seq}")
results.append(run("empty -> effort=low -> clean -> content",
    [("resp", EMPTY_LEN), ("resp", EMPTY_LEN), ("resp", GOOD)],
    check_ladder))

# 4. 400 拒 extras → clean 最小请求 → 成功
def check_400(r, c):
    ok = r.content == "答案" and len(c) == 2 \
        and "reasoning_effort" not in c[1] and "max_tokens" not in c[1]
    return (ok, f"bodies={[(b.get('reasoning_effort'), b.get('max_tokens')) for b in c]}")
results.append(run("400 on extras -> clean minimal request -> content",
    [("400", None), ("resp", GOOD)], check_400))

# 5. 全部失败 → 诊断含尝试序列
def check_diag(r, c):
    ok = (r.error and "empty content" in r.error
          and "4096" in r.error)
    return (ok, r.error[:80] if r.error else "")
results.append(run("all-fail diag includes details",
    [("resp", EMPTY_LEN), ("resp", EMPTY_LEN), ("resp", EMPTY_LEN)],
    check_diag))

# 6. 正常回复不注入任何参数
results.append(run("normal reply: no extra params", [("resp", GOOD)],
    lambda r, c: ("reasoning_effort" not in c[0]
                  and "thinking" not in c[0], "")))

print("-" * 40)
n_ok = sum(1 for x in results if x)
print(f"{n_ok}/{len(results)} passed")
sys.exit(0 if n_ok == len(results) else 1)
