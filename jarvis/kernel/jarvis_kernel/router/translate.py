"""Pure dict translation between the OpenAI chat format and Anthropic Messages.

Standard library only; no network, no SDK.
"""
from __future__ import annotations

import json
import time
from typing import Any

DEFAULT_MAX_TOKENS = 4096

STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}

_TEXT_PART_TYPES = ("text", "input_text", "output_text")


class Unsupported(ValueError):
    """The request uses something that cannot be translated."""


class UpstreamError(RuntimeError):
    """The Anthropic stream reported an error event."""


def _image_block(part: dict) -> dict:
    img = part.get("image_url")
    url = img.get("url") if isinstance(img, dict) else img
    if not isinstance(url, str) or not url:
        raise Unsupported("image_url part without url")
    if url.startswith("data:"):
        head, sep, data = url.partition(",")
        meta = head[5:].split(";")
        if not sep or "base64" not in meta[1:] or not meta[0]:
            raise Unsupported("only base64 data: image URLs are supported")
        return {"type": "image",
                "source": {"type": "base64", "media_type": meta[0], "data": data}}
    if url.startswith("https://"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    raise Unsupported("unsupported image URL scheme")


def _parts_to_blocks(content: Any) -> list[dict]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        raise Unsupported("unsupported content type")
    blocks = []
    for part in content:
        if isinstance(part, str):
            blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            raise Unsupported("unsupported content part")
        ptype = part.get("type")
        if ptype in _TEXT_PART_TYPES:
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif ptype in ("image_url", "input_image"):
            blocks.append(_image_block(part))
        else:
            raise Unsupported(f"unsupported content part type: {ptype!r}")
    return blocks


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    texts = []
    for b in _parts_to_blocks(content):
        if b["type"] != "text":
            raise Unsupported("non-text content not allowed here")
        texts.append(b["text"])
    return "".join(texts)


def _convert_tool_choice(tc: Any) -> dict:
    if tc == "auto":
        return {"type": "auto"}
    if tc == "none":
        return {"type": "none"}
    if tc == "required":
        return {"type": "any"}
    if isinstance(tc, dict) and tc.get("type") == "function":
        name = (tc.get("function") or {}).get("name")
        if name:
            return {"type": "tool", "name": name}
    raise Unsupported("unsupported tool_choice")


def _refuse_unsupported_features(body: dict) -> None:
    """Features Anthropic's Messages API cannot honour are refused, never silently dropped (a caller asking for JSON mode must not get prose)."""
    rf = body.get("response_format")
    if rf and (not isinstance(rf, dict) or rf.get("type") not in (None, "text")):
        raise Unsupported("response_format is not supported")
    for k in ("logit_bias", "functions", "function_call", "audio", "modalities", "logprobs"):
        if body.get(k):
            raise Unsupported(f"{k} is not supported")
    if int(body.get("n") or 1) != 1:
        raise Unsupported("n > 1 is not supported")


def openai_to_anthropic(body: dict, drop_sampling: bool = False) -> dict:
    _refuse_unsupported_features(body)
    system: list[str] = []
    turns: list[dict] = []

    def add(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    for m in body.get("messages") or []:
        role = m.get("role")
        if role in ("system", "developer"):
            text = _text_of(m.get("content"))
            if text:
                system.append(text)
        elif role == "user":
            add("user", _parts_to_blocks(m.get("content")))
        elif role == "assistant":
            blocks = _parts_to_blocks(m.get("content"))
            for call in m.get("tool_calls") or []:
                if call.get("type", "function") != "function":
                    raise Unsupported("unsupported tool call type")
                fn = call.get("function") or {}
                raw = fn.get("arguments")
                try:
                    args = json.loads(raw) if raw else {}
                except (TypeError, ValueError):
                    raise Unsupported("invalid JSON in tool call arguments")
                if not isinstance(args, dict):
                    raise Unsupported("tool call arguments must be a JSON object")
                blocks.append({"type": "tool_use", "id": call.get("id"),
                               "name": fn.get("name"), "input": args})
            add("assistant", blocks)
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                     "content": _text_of(m.get("content"))}
            # tool_result blocks must lead the user turn, in order.
            if turns and turns[-1]["role"] == "user":
                content = turns[-1]["content"]
                i = 0
                while i < len(content) and content[i]["type"] == "tool_result":
                    i += 1
                content.insert(i, block)
            else:
                add("user", [block])
        else:
            raise Unsupported(f"unsupported role: {role!r}")

    if turns and turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": [{"type": "text", "text": "."}]})

    out: dict = {"model": body.get("model"), "messages": turns}
    if system:
        out["system"] = "\n\n".join(system)
    mt = body.get("max_completion_tokens")
    if mt is None:
        mt = body.get("max_tokens")
    out["max_tokens"] = mt if mt is not None else DEFAULT_MAX_TOKENS
    stop = body.get("stop")
    if stop is not None:
        out["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    if not drop_sampling:
        for k in ("temperature", "top_p"):
            if body.get(k) is not None:
                out[k] = body[k]
    if body.get("stream"):
        out["stream"] = True
    tools = body.get("tools")
    if tools:
        conv = []
        for t in tools:
            if t.get("type") != "function":
                raise Unsupported("only function tools are supported")
            fn = t.get("function") or {}
            conv.append({"name": fn.get("name"),
                         "description": fn.get("description", ""),
                         "input_schema": fn.get("parameters")
                         or {"type": "object", "properties": {}}})
        out["tools"] = conv
        if body.get("tool_choice") is not None:
            out["tool_choice"] = _convert_tool_choice(body["tool_choice"])
    return out


def _usage(u: dict | None) -> dict:
    u = u or {}
    i, o = u.get("input_tokens") or 0, u.get("output_tokens") or 0
    return {"prompt_tokens": i, "completion_tokens": o, "total_tokens": i + o}


def anthropic_to_openai(msg: dict, model: str) -> dict:
    texts, calls = [], []
    for b in msg.get("content") or []:
        t = b.get("type")
        if t == "text":
            texts.append(b.get("text", ""))
        elif t == "tool_use":
            calls.append({"id": b.get("id"), "type": "function",
                          "function": {"name": b.get("name"),
                                       "arguments": json.dumps(b.get("input") or {})}})
    message: dict = {"role": "assistant", "content": "".join(texts) if texts else None}
    if calls:
        message["tool_calls"] = calls
    elif message["content"] is None:
        message["content"] = ""
    return {
        "id": msg.get("id", ""),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": STOP_REASONS.get(msg.get("stop_reason"), "stop")}],
        "usage": _usage(msg.get("usage")),
    }


class StreamTranslator:
    def __init__(self, model: str = "", created: int | None = None) -> None:
        self.model = model
        self.created = int(time.time()) if created is None else created
        self.id = ""
        self.pings = 0
        self.finished = False
        self._tools: dict[int, int] = {}      # anthropic block index -> tool index
        self._ignored: set[int] = set()
        self._input_tokens = 0
        self._output_tokens = 0
        self._stop_reason: str | None = None

    def _chunk(self, delta: dict, finish: str | None = None, usage: dict | None = None) -> str:
        obj = {"id": self.id, "object": "chat.completion.chunk",
               "created": self.created, "model": self.model,
               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            obj["usage"] = usage
        return "data: " + json.dumps(obj) + "\n\n"

    def feed(self, event_name: str, data_json_str: str) -> list[str]:
        if event_name == "ping":
            self.pings += 1
            return []
        if event_name not in ("message_start", "content_block_start",
                              "content_block_delta", "content_block_stop",
                              "message_delta", "message_stop", "error"):
            return []
        try:
            data = json.loads(data_json_str) if data_json_str else {}
        except ValueError:
            raise UpstreamError("malformed event data")
        if event_name == "error":
            err = data.get("error") if isinstance(data, dict) else None
            msg = err.get("message") if isinstance(err, dict) else None
            raise UpstreamError(msg or "upstream error")
        if self.finished:
            return []
        idx = data.get("index")
        if event_name == "message_start":
            m = data.get("message") or {}
            self.id = m.get("id", "")
            self.model = self.model or m.get("model", "")
            self._input_tokens = (m.get("usage") or {}).get("input_tokens") or 0
            self._output_tokens = (m.get("usage") or {}).get("output_tokens") or 0
            return [self._chunk({"role": "assistant", "content": ""})]
        if event_name == "content_block_start":
            b = data.get("content_block") or {}
            if b.get("type") == "tool_use":
                n = len(self._tools)
                self._tools[idx] = n
                return [self._chunk({"tool_calls": [{
                    "index": n, "id": b.get("id"), "type": "function",
                    "function": {"name": b.get("name"), "arguments": ""}}]})]
            if b.get("type") == "text":
                text = b.get("text")
                return [self._chunk({"content": text})] if text else []
            self._ignored.add(idx)
            return []
        if event_name == "content_block_delta":
            d = data.get("delta") or {}
            if d.get("type") == "text_delta" and idx not in self._ignored:
                return [self._chunk({"content": d.get("text", "")})]
            if d.get("type") == "input_json_delta" and idx in self._tools:
                return [self._chunk({"tool_calls": [{
                    "index": self._tools[idx],
                    "function": {"arguments": d.get("partial_json", "")}}]})]
            return []
        if event_name == "message_delta":
            d = data.get("delta") or {}
            if d.get("stop_reason"):
                self._stop_reason = d["stop_reason"]
            u = data.get("usage") or {}
            if u.get("input_tokens") is not None:
                self._input_tokens = u["input_tokens"]
            if u.get("output_tokens") is not None:
                self._output_tokens = u["output_tokens"]
            return []
        if event_name == "message_stop":
            self.finished = True
            usage = _usage({"input_tokens": self._input_tokens,
                            "output_tokens": self._output_tokens})
            finish = STOP_REASONS.get(self._stop_reason, "stop")
            return [self._chunk({}, finish, usage), "data: [DONE]\n\n"]
        return []
