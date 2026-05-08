"""
Responses API to Chat Completions API Proxy
=============================================
Translates OpenAI Responses API (used by Codex CLI) into OpenAI Chat Completions API (used by MiMo).
Preserves full tool call lifecycle, streaming, reasoning content, and conversation history.
"""

import hmac
import json
import os
import time
import uuid
import logging
from typing import Any, AsyncGenerator

import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# ── orjson acceleration (fallback to stdlib json) ─────────────────────────────
try:
    import orjson as _orjson
    def _dumps(obj: Any) -> str:
        return _orjson.dumps(obj).decode()
    def _loads(s: str) -> Any:
        return _orjson.loads(s)
except ImportError:
    _orjson = None
    def _dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False)
    def _loads(s: str) -> Any:
        return json.loads(s)

from config import PROVIDERS, PROXY_HOST, PROXY_PORT, PROXY_AUTH_TOKEN, ProviderConfig, reload_providers
import config_store

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("proxy")

# ── FastAPI app ──────────────────────────────────────────────────────────────
_http_client: httpx.AsyncClient | None = None

@asynccontextmanager
async def lifespan(app):
    global _http_client
    _http_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=30, read=600, write=60, pool=10))
    providers_summary = ", ".join(sorted(set(p.name for p in PROVIDERS.values())))
    log.info("Proxy started: %s → providers: %s (%d models)", PROXY_HOST + ":" + str(PROXY_PORT), providers_summary, len(PROVIDERS))
    # Startup connectivity pre-check
    checked = set()
    for p in PROVIDERS.values():
        if p.base_url in checked:
            continue
        checked.add(p.base_url)
        try:
            r = await _http_client.get(
                f"{p.base_url}/models",
                headers={"Authorization": f"Bearer {p.api_key}"},
                timeout=5,
            )
            log.info("  ✓ %s (%s) → %d", p.name, p.base_url, r.status_code)
        except Exception as e:
            log.warning("  ✗ %s (%s) → %s", p.name, p.base_url, e)
    yield
    if _http_client:
        await _http_client.aclose()
        _http_client = None
    log.info("Proxy shut down.")

app = FastAPI(title="Responses→ChatCompletions Proxy", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB

@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_SIZE:
                return JSONResponse(status_code=413, content={"error": {"type": "invalid_request", "message": "Request body too large"}})
        except ValueError:
            return JSONResponse(status_code=400, content={"error": {"type": "invalid_request", "message": "Invalid Content-Length header"}})
    return await call_next(request)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS — Responses API ↔ Chat Completions API
# ══════════════════════════════════════════════════════════════════════════════

def _convert_responses_tools_to_chat(tools: list[dict]) -> list[dict]:
    """
    Responses API tools → Chat Completions tools.
    
    Responses API format:
      {"type": "function", "name": "...", "description": "...", "parameters": {...}}
    Chat Completions format:
      {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    """
    converted = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool.get("function", {})
            # If the tool already has function nested (Chat Completions format), pass through
            if func and func.get("name"):
                converted.append({"type": "function", "function": func})
            else:
                # Responses API flat format → wrap in function key
                converted.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    }
                })
    return converted


def _convert_responses_tool_choice_to_chat(tool_choice) -> dict | None:
    """Responses API tool_choice → Chat Completions tool_choice."""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict):
        # Responses API: {"type": "auto"} or {"type": "required"} or {"type": "function", "name": "...", "mode": "auto"}
        tc_type = tool_choice.get("type", "auto")
        if tc_type in ("auto", "none", "required"):
            return tc_type
        if tc_type == "function":
            name = tool_choice.get("name") or tool_choice.get("function", {}).get("name", "")
            mode = tool_choice.get("mode", "auto")
            if mode == "required":
                return {"type": "function", "function": {"name": name}}
            return {"type": "function", "function": {"name": name}}
    return "auto"


def _convert_content_parts(content_list: list) -> str | list:
    """Convert Responses API content array to Chat Completions content format.
    Returns a plain string if only text parts, otherwise returns a multimodal array."""
    parts = []
    for c in content_list:
        if not isinstance(c, dict):
            continue
        ptype = c.get("type", "")
        if ptype in ("input_text", "text"):
            parts.append({"type": "text", "text": c.get("text", "")})
        elif ptype == "input_image":
            parts.append({"type": "image_url", "image_url": {"url": c.get("image_url", "")}})
        else:
            # Fallback: try to extract text or stringify
            parts.append({"type": "text", "text": c.get("text", str(c))})
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0]["text"]
    return parts or ""


def convert_input_to_messages(data: dict) -> list[dict]:
    """
    Convert Responses API input format to Chat Completions messages.
    
    Handles:
    - string input
    - list of strings
    - list of message objects with role/content
    - function_call items (assistant with tool calls)
    - function_call_output items (tool results)
    - developer/system message
    """
    messages = []
    
    # Top-level instructions → system message
    if instructions := data.get("instructions"):
        messages.append({"role": "system", "content": instructions})
    
    input_data = data.get("input", "")
    
    if isinstance(input_data, str):
        if input_data.strip():
            messages.append({"role": "user", "content": input_data})
    elif isinstance(input_data, list):
        i = 0
        while i < len(input_data):
            item = input_data[i]
            
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
                i += 1
                
            elif isinstance(item, dict):
                item_type = item.get("type", "")
                role = item.get("role", "")
                
                # ── Responses API message types ──
                if item_type == "message" or role in ("user", "assistant", "system", "developer"):
                    content = item.get("content", "")
                    actual_role = role if role else "user"
                    
                    if actual_role in ("system", "developer"):
                        actual_role = "system"
                    
                    if isinstance(content, list):
                        content = _convert_content_parts(content)

                    msg = {"role": actual_role, "content": content}
                    if actual_role == "assistant" and item.get("reasoning_content"):
                        msg["reasoning_content"] = item["reasoning_content"]
                    messages.append(msg)
                
                # ── Function call item (assistant decided to call a tool) ──
                elif item_type == "function_call":
                    call_id = item.get("call_id", "")
                    func_name = item.get("name", "")
                    arguments = item.get("arguments", "{}")
                    tc = {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": func_name,
                            "arguments": arguments,
                        }
                    }
                    # Merge consecutive function_calls into one assistant message
                    if (messages and messages[-1].get("role") == "assistant"
                            and "tool_calls" in messages[-1]):
                        messages[-1]["tool_calls"].append(tc)
                    else:
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [tc],
                        })
                
                # ── Function call output (tool result) ──
                elif item_type == "function_call_output":
                    call_id = item.get("call_id", "")
                    output = item.get("output", "")
                    if isinstance(output, list):
                        # Some agents send array output
                        output = _dumps(output)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": str(output),
                    })
                
                else:
                    # Unknown type — try best-effort extraction
                    if role:
                        content = item.get("content", "")
                        if isinstance(content, list):
                            content = _convert_content_parts(content)
                        msg = {"role": role, "content": content if content else ""}
                        if role == "assistant" and item.get("reasoning_content"):
                            msg["reasoning_content"] = item["reasoning_content"]
                        messages.append(msg)
            
            i += 1
    
    return messages


def to_chat_completions(data: dict) -> dict:
    """Convert a Responses API request to a Chat Completions request."""
    messages = convert_input_to_messages(data)
    
    result = {
        "model": data.get("model", ""),
        "messages": messages,
    }
    
    # Pass through temperature (handle 0.0 correctly — walrus would skip it)
    if "temperature" in data:
        result["temperature"] = data["temperature"]
    
    # max_output_tokens → max_tokens
    if "max_output_tokens" in data:
        result["max_tokens"] = data["max_output_tokens"]
    
    if "top_p" in data:
        result["top_p"] = data["top_p"]
    
    # ── Tool call support ──
    if tools := data.get("tools"):
        result["tools"] = _convert_responses_tools_to_chat(tools)
    
    if tool_choice := data.get("tool_choice"):
        tc = _convert_responses_tool_choice_to_chat(tool_choice)
        if tc is not None:
            result["tool_choice"] = tc
    
    # Pass through optional parameters
    if "frequency_penalty" in data:
        result["frequency_penalty"] = data["frequency_penalty"]
    if "presence_penalty" in data:
        result["presence_penalty"] = data["presence_penalty"]
    if "stop" in data:
        result["stop"] = data["stop"]
    if "seed" in data:
        result["seed"] = data["seed"]
    if "response_format" in data:
        result["response_format"] = data["response_format"]
    
    return result


def _make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


# ══════════════════════════════════════════════════════════════════════════════
#  STREAMING — Responses API SSE format
# ══════════════════════════════════════════════════════════════════════════════

async def stream_response(chat_request: dict, provider: ProviderConfig, model: str) -> AsyncGenerator[str, None]:
    """
    Stream a Chat Completions response from upstream, convert to Responses API SSE events.
    Full tool call support: captures tool_calls delta chunks and emits them correctly.
    """
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    resp_id = _make_id("resp")
    msg_id = _make_id("msg")
    reasoning_id = _make_id("rs")
    
    # ── Initial events ──
    yield f"data: {_dumps({'type': 'response.created', 'response': {'id': resp_id, 'object': 'response', 'model': model, 'status': 'in_progress'}})}\n\n"
    yield f"data: {_dumps({'type': 'response.in_progress', 'response': {'id': resp_id, 'status': 'in_progress'}})}\n\n"
    
    full_reasoning = ""
    full_content = ""
    reasoning_started = False
    reasoning_closed = False
    content_started = False
    message_emitted = False  # tracks whether the message output item was emitted
    
    # Tool call accumulation state
    tool_calls_started = False
    tool_calls_output_index = None
    tool_call_accumulators: dict[int, dict] = {}  # index → {id, name, arguments}
    tool_call_done_emitted: dict[int, bool] = {}
    current_output_index = 0  # tracks the next output_index to use
    usage_data = None  # captured from the final streaming chunk
    
    try:
        async with _http_client.stream(
            "POST",
            f"{provider.base_url}/chat/completions",
            json=chat_request,
            headers=headers,
        ) as resp:
            if resp.status_code != 200:
                error_body = await resp.aread()
                error_msg = error_body.decode(errors="replace")[:500]
                log.error("Upstream stream %d (model=%s, provider=%s): %s", resp.status_code, model, provider.name, error_msg)
                yield f"data: {_dumps({'type': 'error', 'error': {'type': 'upstream_error', 'message': f'Upstream returned {resp.status_code}'}})}\n\n"
                return
            
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                
                try:
                    chunk = _loads(payload)
                except Exception:
                    log.warning("Malformed chunk: %s", payload[:200])
                    continue

                # Capture usage from the final chunk (stream_options.include_usage)
                if chunk.get("usage"):
                    usage_data = chunk["usage"]

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                
                delta = choices[0].get("delta", {})
                finish_reason = choices[0].get("finish_reason")
                
                # ── Reasoning content ──
                reasoning = delta.get("reasoning_content", "")
                if reasoning:
                    if not reasoning_started:
                        reasoning_started = True
                        yield f"data: {_dumps({'type': 'response.output_item.added', 'output_index': current_output_index, 'item': {'type': 'reasoning', 'id': reasoning_id, 'summary': []}})}\n\n"
                    full_reasoning += reasoning
                
                # ── Text content ──
                content = delta.get("content", "")
                if content:
                    if not content_started:
                        content_started = True
                        # Close reasoning if it was started
                        if reasoning_started and not reasoning_closed:
                            reasoning_closed = True
                            yield f"data: {_dumps({'type': 'response.output_item.done', 'output_index': current_output_index, 'item': {'type': 'reasoning', 'id': reasoning_id, 'summary': [{'type': 'summary_text', 'text': full_reasoning[:500]}]}})}\n\n"
                            current_output_index += 1

                        message_emitted = True
                        yield f"data: {_dumps({'type': 'response.output_item.added', 'output_index': current_output_index, 'item': {'type': 'message', 'id': msg_id, 'role': 'assistant', 'status': 'in_progress', 'content': []}})}\n\n"
                        yield f"data: {_dumps({'type': 'response.content_part.added', 'output_index': current_output_index, 'content_index': 0, 'part': {'type': 'output_text', 'text': ''}})}\n\n"
                    
                    full_content += content
                    yield f"data: {_dumps({'type': 'response.output_text.delta', 'output_index': current_output_index, 'content_index': 0, 'delta': content})}\n\n"
                
                # ── Tool calls (streamed in delta chunks) ──
                tool_calls = delta.get("tool_calls", [])
                if tool_calls:
                    # Close reasoning if it was started and not already closed
                    if reasoning_started and not reasoning_closed:
                        reasoning_closed = True
                        yield f"data: {_dumps({'type': 'response.output_item.done', 'output_index': current_output_index, 'item': {'type': 'reasoning', 'id': reasoning_id, 'summary': [{'type': 'summary_text', 'text': full_reasoning[:500]}]}})}\n\n"
                        current_output_index += 1

                    # Close content/message if it was started
                    if content_started:
                        yield f"data: {_dumps({'type': 'response.content_part.done', 'output_index': current_output_index, 'content_index': 0, 'part': {'type': 'output_text', 'text': full_content}})}\n\n"
                        yield f"data: {_dumps({'type': 'response.output_item.done', 'output_index': current_output_index, 'item': {'type': 'message', 'id': msg_id, 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': full_content}]}})}\n\n"
                        current_output_index += 1
                        content_started = False
                    elif not message_emitted:
                        # Tool calls arrived before any content — emit empty message
                        message_emitted = True
                        yield f"data: {_dumps({'type': 'response.output_item.added', 'output_index': current_output_index, 'item': {'type': 'message', 'id': msg_id, 'role': 'assistant', 'status': 'in_progress', 'content': []}})}\n\n"
                        yield f"data: {_dumps({'type': 'response.content_part.added', 'output_index': current_output_index, 'content_index': 0, 'part': {'type': 'output_text', 'text': ''}})}\n\n"
                        yield f"data: {_dumps({'type': 'response.content_part.done', 'output_index': current_output_index, 'content_index': 0, 'part': {'type': 'output_text', 'text': ''}})}\n\n"
                        yield f"data: {_dumps({'type': 'response.output_item.done', 'output_index': current_output_index, 'item': {'type': 'message', 'id': msg_id, 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': ''}]}})}\n\n"
                        current_output_index += 1
                    
                    for tc in tool_calls:
                        tc_index = tc.get("index", 0)
                        tc_id = tc.get("id")
                        tc_func = tc.get("function", {})
                        tc_name = tc_func.get("name", "")
                        tc_args = tc_func.get("arguments", "")
                        
                        if tc_index not in tool_call_accumulators:
                            tool_call_accumulators[tc_index] = {"id": "", "name": "", "arguments": ""}
                            tool_call_done_emitted[tc_index] = False
                        
                        acc = tool_call_accumulators[tc_index]
                        if tc_id:
                            acc["id"] = tc_id
                        if tc_name:
                            acc["name"] = tc_name
                        if tc_args:
                            acc["arguments"] += tc_args
                        
                        # Emit the tool call as a function_call output item
                        # First chunk: emit output_item.added
                        if not tool_calls_started:
                            tool_calls_started = True
                            tool_calls_output_index = current_output_index
                        
                        # Emit function_call_arguments.delta for each chunk
                        call_id = acc["id"] or _make_id("call")
                        yield f"data: {_dumps({'type': 'response.function_call_arguments.delta', 'item_id': call_id, 'output_index': tool_calls_output_index + tc_index, 'content_index': 0, 'arguments_delta': tc_args})}\n\n"
                
                # ── Finish reason handling ──
                if finish_reason == "tool_calls":
                    # Don't close yet — the agent loop continues
                    pass
                elif finish_reason in ("stop", "length", None):
                    pass  # Will be handled after stream ends
    
    except httpx.TimeoutException:
        log.error("Upstream timeout for model=%s provider=%s", model, provider.name)
        yield f"data: {_dumps({'type': 'error', 'error': {'type': 'timeout', 'message': 'Upstream request timed out'}})}\n\n"
        return
    except Exception as e:
        log.error("Stream error (model=%s, provider=%s): %s", model, provider.name, e, exc_info=True)
        yield f"data: {_dumps({'type': 'error', 'error': {'type': 'upstream_error', 'message': f'Stream error: {e}'}})}\n\n"
        return
    
    # ── Emit final events ──

    # If we had reasoning but no content or tool calls, still need to emit a message
    if not content_started and not message_emitted:
        if reasoning_started and not reasoning_closed:
            yield f"data: {_dumps({'type': 'response.output_item.done', 'output_index': current_output_index, 'item': {'type': 'reasoning', 'id': reasoning_id, 'summary': [{'type': 'summary_text', 'text': full_reasoning[:500]}]}})}\n\n"
            current_output_index += 1

        message_emitted = True
        yield f"data: {_dumps({'type': 'response.output_item.added', 'output_index': current_output_index, 'item': {'type': 'message', 'id': msg_id, 'role': 'assistant', 'status': 'in_progress', 'content': []}})}\n\n"
        yield f"data: {_dumps({'type': 'response.content_part.added', 'output_index': current_output_index, 'content_index': 0, 'part': {'type': 'output_text', 'text': ''}})}\n\n"
        full_content = ""

    # Close content part if it was opened
    if content_started:
        yield f"data: {_dumps({'type': 'response.content_part.done', 'output_index': current_output_index, 'content_index': 0, 'part': {'type': 'output_text', 'text': full_content}})}\n\n"
        yield f"data: {_dumps({'type': 'response.output_item.done', 'output_index': current_output_index, 'item': {'type': 'message', 'id': msg_id, 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': full_content}]}})}\n\n"
    
    # Close tool call items
    for tc_index in sorted(tool_call_accumulators.keys()):
        if not tool_call_done_emitted.get(tc_index, False):
            acc = tool_call_accumulators[tc_index]
            call_id = acc["id"] or _make_id("call")
            idx = (tool_calls_output_index or current_output_index) + tc_index
            yield f"data: {_dumps({'type': 'response.output_item.done', 'output_index': idx, 'item': {'type': 'function_call', 'id': call_id, 'call_id': call_id, 'name': acc['name'], 'arguments': acc['arguments']}})}\n\n"
            tool_call_done_emitted[tc_index] = True
    
    # ── Final response.completed event ──
    output = []
    
    # Add content message if we have text
    if message_emitted and full_content:
        output.append({
            "type": "message",
            "id": msg_id,
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": full_content}],
        })
    
    # Add function call items
    for tc_index in sorted(tool_call_accumulators.keys()):
        acc = tool_call_accumulators[tc_index]
        call_id = acc["id"] or _make_id("call")
        output.append({
            "type": "function_call",
            "id": call_id,
            "call_id": call_id,
            "name": acc["name"],
            "arguments": acc["arguments"],
        })
    
    resp_usage = {
        "input_tokens": usage_data.get("prompt_tokens", 0) if usage_data else 0,
        "output_tokens": usage_data.get("completion_tokens", 0) if usage_data else 0,
        "total_tokens": usage_data.get("total_tokens", 0) if usage_data else 0,
    }
    yield f"data: {_dumps({'type': 'response.completed', 'response': {'id': resp_id, 'object': 'response', 'model': model, 'status': 'completed', 'output': output, 'usage': resp_usage}})}\n\n"
    yield "data: [DONE]\n\n"


# ══════════════════════════════════════════════════════════════════════════════
#  NON-STREAMING — Responses API format
# ══════════════════════════════════════════════════════════════════════════════

def to_responses_format(chat_response: dict, model: str) -> dict:
    """Convert a Chat Completions response to Responses API format."""
    resp_id = _make_id("resp")
    output = []
    
    if choices := chat_response.get("choices"):
        msg = choices[0].get("message", {})
        
        # Reasoning content
        reasoning_text = msg.get("reasoning_content", "")
        if reasoning_text:
            output.append({
                "type": "reasoning",
                "id": _make_id("rs"),
                "summary": [{"type": "summary_text", "text": reasoning_text[:500]}],
            })
        
        # Tool calls
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                output.append({
                    "type": "function_call",
                    "id": tc.get("id", _make_id("call")),
                    "call_id": tc.get("id", _make_id("call")),
                    "name": func.get("name", ""),
                    "arguments": func.get("arguments", "{}"),
                })
        
        # Text content (only if no tool calls — with tool calls, content is usually None)
        content_text = msg.get("content", "")
        if content_text or not tool_calls:
            output.append({
                "type": "message",
                "id": _make_id("msg"),
                "role": "assistant",
                "content": [{"type": "output_text", "text": content_text or ""}],
                "status": "completed",
            })
    
    usage = chat_response.get("usage", {})
    return {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "output": output,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "status": "completed",
    }


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG MANAGEMENT API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def index():
    """返回 Web UI 管理页面。"""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    return HTMLResponse("<h1>Responses→ChatCompletions Proxy</h1><p>static/index.html 不存在</p>")


@app.get("/api/config")
async def get_config():
    """返回当前配置（providers + 全局设置）。"""
    raw = config_store.read_raw_config()
    # 掩码处理 API Key：只显示前 4 位和后 4 位
    providers = raw.get("providers", {})
    masked = {}
    for name, cfg in providers.items():
        key = cfg.get("api_key", "")
        if len(key) > 8:
            masked_key = key[:4] + "•" * (len(key) - 8) + key[-4:]
        elif key:
            masked_key = key[:2] + "•" * (len(key) - 2)
        else:
            masked_key = ""
        masked[name] = {
            "base_url": cfg.get("base_url", ""),
            "api_key_masked": masked_key,
            "api_key_length": len(key),
            "models": cfg.get("models", []),
        }
    # 读取 global 配置（providers.json 中的值优先，否则用环境变量）
    g = raw.get("global", {})
    return {
        "global": {
            "host": g.get("host", PROXY_HOST),
            "port": g.get("port", PROXY_PORT),
        },
        "active_model": config_store.read_codex_model(),
        "codex_config_paths": raw.get("codex_config_paths", ["~/.codex/config.toml"]),
        "providers": masked,
        "model_count": len(PROVIDERS),
        "config_path": config_store.get_config_path(),
    }


@app.post("/api/config")
async def save_config(request: Request):
    """保存配置到 providers.json 并热重载。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    providers = body.get("providers")
    if not isinstance(providers, dict):
        return JSONResponse(status_code=400, content={"error": "Missing or invalid 'providers' field"})

    # 读取当前配置，用于保留未修改的 api_key
    current_raw = config_store.read_raw_config()
    current_providers = current_raw.get("providers", {})

    # 校验每个 provider 并保留未修改的 api_key
    for name, cfg in providers.items():
        if not isinstance(cfg, dict):
            return JSONResponse(status_code=400, content={"error": f"Provider '{name}' must be an object"})
        if not cfg.get("base_url"):
            return JSONResponse(status_code=400, content={"error": f"Provider '{name}' missing base_url"})
        if not cfg.get("models") or not isinstance(cfg["models"], list):
            return JSONResponse(status_code=400, content={"error": f"Provider '{name}' missing models list"})
        # 未传 api_key 或为空时，保留原有值
        if not cfg.get("api_key"):
            if name in current_providers and current_providers[name].get("api_key"):
                cfg["api_key"] = current_providers[name]["api_key"]

    # 处理 global 配置（host/port），检测是否有变化
    global_cfg = body.get("global")
    need_restart = False
    if global_cfg:
        old_host = current_raw.get("global", {}).get("host", PROXY_HOST)
        old_port = current_raw.get("global", {}).get("port", PROXY_PORT)
        new_host = global_cfg.get("host", old_host)
        new_port = global_cfg.get("port", old_port)
        if str(old_host) != str(new_host) or str(old_port) != str(new_port):
            need_restart = True
        # 每次保存都同步更新 config.toml 的 base_url
        config_store.write_codex_base_url(new_host, new_port)

    try:
        config_store.save_providers(providers, global_cfg,
                                    body.get("codex_config_paths"))
        model_count = reload_providers()
        return {"status": "ok", "model_count": model_count, "need_restart": need_restart}
    except Exception as e:
        log.error("保存配置失败: %s", e)
        return JSONResponse(status_code=500, content={"error": f"Failed to save: {e}"})


@app.post("/api/config/test")
async def test_provider(request: Request):
    """测试单个 provider 的连通性。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    base_url = body.get("base_url", "").rstrip("/")
    api_key = body.get("api_key", "")
    provider_name = body.get("provider_name", "")

    # 如果没有提供 api_key，尝试从已保存的配置中获取
    if not api_key and provider_name:
        saved = config_store.read_raw_config().get("providers", {}).get(provider_name, {})
        api_key = saved.get("api_key", "")

    if not base_url:
        return JSONResponse(status_code=400, content={"error": "Missing base_url"})

    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = await _http_client.get(f"{base_url}/models", headers=headers, timeout=10)
        if r.status_code == 200:
            return {"status": "ok", "status_code": 200, "message": "连接成功"}
        elif r.status_code in (401, 403):
            return {"status": "error", "status_code": r.status_code, "message": "认证失败，请检查 API Key"}
        elif r.status_code == 404:
            # 服务器可达但不支持 /models 端点，不算失败
            return {"status": "ok", "status_code": 404, "message": "连接成功（服务器不支持 /models 查询）"}
        else:
            return {"status": "error", "status_code": r.status_code, "message": f"HTTP {r.status_code}"}
    except httpx.TimeoutException:
        return {"status": "error", "message": "连接超时"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/reload")
async def reload_config():
    """热重载配置（不重启进程）。"""
    try:
        model_count = reload_providers()
        return {"status": "ok", "model_count": model_count}
    except Exception as e:
        log.error("重载配置失败: %s", e)
        return JSONResponse(status_code=500, content={"error": f"Reload failed: {e}"})


@app.post("/api/restart")
async def restart_server():
    """重启代理服务器（用于 host/port 变更后）。"""
    import sys
    import subprocess
    import threading

    def do_restart():
        import time
        time.sleep(1.0)  # 等待响应发送完成
        log.info("正在重启代理...")
        
        # 启动新进程
        proc = subprocess.Popen(
            [sys.executable] + sys.argv,
            cwd=os.getcwd(),
        )
        # 等待新进程启动
        time.sleep(2.0)
        # 退出当前进程
        os._exit(0)

    threading.Thread(target=do_restart, daemon=True).start()
    return {"status": "restarting", "message": "代理将在 1 秒后重启"}


@app.get("/api/active-model")
async def get_active_model():
    """返回当前 Codex CLI 激活的模型。"""
    return {"model": config_store.read_codex_model()}


@app.post("/api/active-model")
async def set_active_model(request: Request):
    """设置 Codex CLI 激活的模型（自动更新 config.toml）。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    model = body.get("model", "").strip()
    if not model:
        return JSONResponse(status_code=400, content={"error": "Missing model"})

    # 校验模型是否在已配置的列表中
    if model not in PROVIDERS:
        available = ", ".join(sorted(PROVIDERS.keys()))
        return JSONResponse(status_code=400, content={
            "error": f"Unknown model '{model}'. Available: {available}"
        })

    try:
        config_store.write_codex_model(model)
        return {"status": "ok", "model": model}
    except Exception as e:
        log.error("更新 config.toml 失败: %s", e)
        return JSONResponse(status_code=500, content={"error": f"Failed to update config.toml: {e}"})


# ══════════════════════════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/v1/responses")
async def proxy_responses(request: Request):
    """Main proxy endpoint: Responses API → Chat Completions → upstream → Responses API."""
    # Optional auth check
    if PROXY_AUTH_TOKEN:
        auth_header = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth_header, f"Bearer {PROXY_AUTH_TOKEN}"):
            return JSONResponse(status_code=401, content={"error": {"type": "auth_error", "message": "Invalid proxy token"}})

    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"type": "invalid_request", "message": "Invalid JSON body"}})

    model = data.get("model", "")
    is_stream = data.get("stream", False)

    # ── 路由：按 model 查找 provider ──
    provider = PROVIDERS.get(model)
    if not provider:
        available = ", ".join(sorted(PROVIDERS.keys()))
        return JSONResponse(status_code=400, content={
            "error": {
                "type": "invalid_request",
                "message": f"Unknown model '{model}'. Available models: {available}",
            }
        })

    request_id = getattr(request.state, "request_id", "?")
    input_data = data.get("input", "")
    input_len = len(input_data) if isinstance(input_data, (str, list)) else 0
    tool_count = len(data.get("tools", []))
    log.info("[%s] Request: model=%s, provider=%s, stream=%s, input_len=%d, tools=%d", request_id, model, provider.name, is_stream, input_len, tool_count)

    # Convert to Chat Completions format
    chat_request = to_chat_completions(data)
    chat_request["stream"] = is_stream

    # Request usage stats in streaming
    if is_stream:
        chat_request["stream_options"] = {"include_usage": True}

    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }

    try:
        if is_stream:
            return StreamingResponse(
                stream_response(chat_request, provider, model),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # Non-streaming
        resp = await _http_client.post(
            f"{provider.base_url}/chat/completions",
            json=chat_request,
            headers=headers,
        )

        if "application/json" not in resp.headers.get("content-type", ""):
            error_body = (await resp.aread()).decode(errors="replace")[:500]
            log.error("Upstream non-JSON (status %d, content-type: %s): %s", resp.status_code, resp.headers.get("content-type"), error_body)
            return JSONResponse(status_code=502, content={"error": {"type": "upstream_error", "message": f"Upstream returned non-JSON: {error_body[:200]}"}})
        chat_response = resp.json()
        if resp.status_code != 200:
            error_msg = chat_response.get("error", {}).get("message", str(chat_response))
            log.error("Upstream error %d (model=%s, provider=%s): %s", resp.status_code, model, provider.name, error_msg[:500])
            return JSONResponse(
                status_code=resp.status_code,
                content={
                    "error": {
                        "type": "api_error",
                        "message": error_msg,
                        "code": str(resp.status_code),
                    }
                }
            )

        result = to_responses_format(chat_response, model)
        return result

    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={"error": {"type": "timeout", "message": "Upstream request timed out"}})
    except Exception as e:
        log.error("Proxy error: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": {"type": "server_error", "message": "Internal proxy error"}})


@app.get("/v1/models")
async def list_models():
    """Return aggregated model list from all configured providers."""
    models = []
    seen = set()
    for model_name, provider in PROVIDERS.items():
        if model_name not in seen:
            seen.add(model_name)
            models.append({
                "id": model_name,
                "object": "model",
                "owned_by": provider.name,
            })
    return {"object": "list", "data": models}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    import webbrowser
    import threading

    url = f"http://{PROXY_HOST}:{PROXY_PORT}"
    print(f"Starting proxy server on {url}")
    print(f"Models: {', '.join(sorted(PROVIDERS.keys()))}")

    def _open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT)
