# ollama_client.py
# Ollama Chat API (local or cloud) with tool execution loops.

from __future__ import annotations

import json
import os
from typing import Any, Callable

import requests

# Default cloud host; override with OLLAMA_HOST (e.g. http://localhost:11434)
_DEFAULT_HOST = "https://ollama.com"


def get_chat_url() -> str:
    host = os.environ.get("OLLAMA_HOST", _DEFAULT_HOST).rstrip("/")
    return f"{host}/api/chat"


def _headers() -> dict:
    key = os.environ.get("OLLAMA_API_KEY", "").strip()
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def chat_completion(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    timeout: int = 120,
) -> dict:
    """POST /api/chat; return full JSON response."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if tools is not None:
        body["tools"] = tools
    r = requests.post(get_chat_url(), headers=_headers(), json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def simple_chat(user_prompt: str, model: str, system: str | None = None, timeout: int = 120) -> str | None:
    """Single user message; return assistant content string."""
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})
    try:
        data = chat_completion(messages, model=model, tools=None, timeout=timeout)
        return data.get("message", {}).get("content")
    except Exception:
        return None


def run_tools_once(
    messages: list[dict],
    model: str,
    tools: list[dict],
    tool_registry: dict[str, Callable[..., Any]],
    timeout: int = 120,
) -> tuple[dict, list[dict]]:
    """
    One chat round; if assistant requests tools, execute and return updated messages
    (caller appends tool results and may call again).
    Returns (full_response_json, new_messages_append_only).
    """
    data = chat_completion(messages, model=model, tools=tools, timeout=timeout)
    msg = data.get("message") or {}
    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        return data, []

    append: list[dict] = []
    append.append(
        {
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
        }
    )
    for tc in tool_calls:
        fn = tc.get("function") or {}
        name = fn.get("name", "")
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args or {}
        func = tool_registry.get(name)
        if func is None:
            out = {"error": f"unknown tool {name}"}
        else:
            try:
                result = func(**args)
                out = result if isinstance(result, (dict, list)) else {"result": result}
            except Exception as e:
                out = {"error": str(e)}
        tool_row: dict[str, Any] = {"role": "tool", "content": json.dumps(out, ensure_ascii=False)}
        tc_id = tc.get("id")
        if tc_id:
            tool_row["tool_call_id"] = str(tc_id)
        if name:
            tool_row["name"] = name
        append.append(tool_row)
    return data, append


def run_tool_loop_until_text(
    messages: list[dict],
    model: str,
    tools: list[dict],
    tool_registry: dict[str, Callable[..., Any]],
    max_rounds: int = 8,
    timeout: int = 120,
) -> str | None:
    """
    Repeat: chat -> if tool_calls execute and extend messages -> else return assistant content.
    """
    msgs = list(messages)
    for _ in range(max_rounds):
        data = chat_completion(msgs, model=model, tools=tools, timeout=timeout)
        msg = data.get("message") or {}
        tool_calls = msg.get("tool_calls")
        content = (msg.get("content") or "").strip()
        if not tool_calls:
            return content if content else None
        msgs.append(
            {
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            }
        )
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw_args or {}
            func = tool_registry.get(name)
            if func is None:
                out: Any = {"error": f"unknown tool {name}"}
            else:
                try:
                    result = func(**args)
                    out = result if isinstance(result, (dict, list)) else {"result": result}
                except Exception as e:
                    out = {"error": str(e)}
            tool_msg: dict[str, Any] = {
                "role": "tool",
                "content": json.dumps(out, ensure_ascii=False),
            }
            tc_id = tc.get("id")
            if tc_id:
                tool_msg["tool_call_id"] = str(tc_id)
            if name:
                tool_msg["name"] = name
            msgs.append(tool_msg)
    return None
