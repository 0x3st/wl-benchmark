"""OpenAI-compatible chat client (stdlib only, no external deps).

Supports: tool calling, multimodal content (text / image_url / file),
retry with backoff, latency & usage recording.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChatResult:
    content: Optional[str] = None
    reasoning_chars: int = 0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    latency: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class ChatClient:
    """Minimal OpenAI-compatible /v1/chat/completions client.

    proxy: "direct" (default; bypass system/env proxies — typical endpoints
           here are reachable directly)
         | "system" (follow system/env proxies) | "http://host:port"
    """

    RETRYABLE = (429, 500, 502, 503, 504)

    DEFAULT_MAX_TOKENS = 65536   # lift the server's output cap

    def __init__(self, base_url: str, api_key: str, timeout: int = 180,
                 max_retries: int = 3, proxy: str = "direct"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxy = proxy
        if proxy == "direct":
            # bypass macOS system proxy / env-var proxies (urllib reads them by default)
            self._opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}))
        elif proxy == "system":
            self._opener = urllib.request.build_opener()
        else:
            self._opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))

    # -------------------------------------------------------------- helpers
    @staticmethod
    def image_part(path_or_dataurl: str) -> Dict[str, Any]:
        """Build an image_url content part from a local file or data URL."""
        if path_or_dataurl.startswith(("http://", "https://", "data:")):
            url = path_or_dataurl
        else:
            with open(path_or_dataurl, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            mime = "image/png" if path_or_dataurl.endswith(".png") else "image/jpeg"
            url = f"data:{mime};base64,{b64}"
        return {"type": "image_url", "image_url": {"url": url}}

    @staticmethod
    def file_part(path: str) -> Dict[str, Any]:
        """Build a file content part (pdf etc.) as base64 data URL."""
        import mimetypes
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return {"type": "file", "file": {
            "filename": path.split("/")[-1],
            "file_data": f"data:{mime};base64,{b64}",
        }}

    # ------------------------------------------------------------------ api
    def chat(self, model: str, messages: List[Dict[str, Any]],
             tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: str = "auto",
             max_tokens: Optional[int] = None,
             temperature: Optional[float] = None,
             response_format: Optional[Dict[str, Any]] = None) -> ChatResult:
        body: Dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        # No budget configured: send a LARGE max_tokens on purpose.
        # Servers apply their own default output cap when the parameter
        # is omitted (observed: 8192), and reasoning models burn that
        # cap on thinking before writing any content. A big value lifts
        # the cap; if the server rejects it we retry without (below).
        if max_tokens:
            body["max_tokens"] = max_tokens
        else:
            body["max_tokens"] = self.DEFAULT_MAX_TOKENS
        if temperature is not None:
            body["temperature"] = temperature
        if response_format:
            body["response_format"] = response_format

        payload = json.dumps(body).encode()
        last_err: Optional[str] = None
        last_res: Optional[ChatResult] = None

        for attempt in range(self.max_retries):
            t0 = time.time()
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions", data=payload,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"})
            try:
                with self._opener.open(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode())
                res = self._parse(data, time.time() - t0)
                if (res.content is None or not res.content.strip()) \
                        and not res.tool_calls:
                    # Reasoning models can burn the whole budget thinking
                    # and return an empty content. Escalate instead of
                    # scoring an empty answer: retry with thinking
                    # disabled (GLM-style param) and a doubled budget.
                    usage = res.usage or {}
                    res.error = (
                        f"empty content (finish_reason={res.finish_reason}, "
                        f"completion_tokens={usage.get('completion_tokens')}, "
                        f"reasoning≈{res.reasoning_chars} chars)")
                    last_err = res.error
                    last_res = res
                    if res.finish_reason == "length":
                        # thinking stays ON — disabling it would score
                        # the model without its core capability. The fix
                        # belongs to the deployment: raise the output cap.
                        server_cap = usage.get("completion_tokens")
                        asked = body.get("max_tokens")
                        clamped = (isinstance(server_cap, int)
                                   and asked and server_cap < asked)
                        res.error = (
                            "empty content: the model spent its whole "
                            f"output budget on reasoning ({res.reasoning_chars} "
                            "chars) and hit the output limit"
                            + (f" — the server clamped generation at "
                               f"{server_cap} tokens though we requested "
                               f"{asked}" if clamped else
                               f" (finish_reason=length, "
                               f"completion_tokens={server_cap})")
                            + ". The deployment's max output must be "
                              "raised (GLM-5.3 supports far more than "
                              "this) or a deployment without the cap "
                              "used — reasoning must stay on, it is the "
                              "capability being measured.")
                        last_err = res.error
                        last_res = res
                        break
                    continue    # finish_reason=stop: transient, plain retry
                else:
                    return res
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode()[:300]
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {detail}"
                if e.code == 400 and "thinking" in body:
                    # endpoint rejects the thinking param — drop it and
                    # retry with the (already boosted) budget
                    body.pop("thinking", None)
                    payload = json.dumps(body).encode()
                    continue
                if e.code == 400 and "max_tokens" in body \
                        and body["max_tokens"] == self.DEFAULT_MAX_TOKENS:
                    # server caps output below our lift — retry with its
                    # own default instead
                    body.pop("max_tokens", None)
                    payload = json.dumps(body).encode()
                    continue
                if e.code not in self.RETRYABLE:
                    break
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))

        if last_res is not None:
            return last_res        # parsed-but-empty: keep diagnostics
        return ChatResult(error=last_err, latency=time.time() - t0)

    @staticmethod
    def _parse(data: Dict[str, Any], latency: float) -> ChatResult:
        if "choices" not in data:
            return ChatResult(error=json.dumps(data, ensure_ascii=False)[:500],
                              latency=latency, raw=data)
        choice = data["choices"][0]
        msg = choice.get("message", {})
        return ChatResult(
            content=msg.get("content"),
            tool_calls=msg.get("tool_calls") or [],
            finish_reason=choice.get("finish_reason"),
            usage=data.get("usage", {}),
            latency=latency,
            raw=data,
            reasoning_chars=len(msg.get("reasoning_content") or ""),
        )


def parse_judge_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from a judge reply (tolerates code fences)."""
    if not text:
        return None
    text = text.strip()
    if "```" in text:
        for seg in text.split("```"):
            seg = seg.strip()
            if seg.startswith("json"):
                seg = seg[4:].strip()
            if seg.startswith("{"):
                text = seg
                break
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
