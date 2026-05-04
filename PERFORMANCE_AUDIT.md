# 全方位性能检查报告和修改建议

> 本文档对 responses-proxy 项目进行全面审查，涵盖架构分析、性能问题、正确性问题、安全问题、缺失功能和改进建议。
> 审查基于 proxy.py (635行)、config.py (17行)、requirements.txt。

---

## 目录

1. [架构分析](#1-架构分析)
2. [性能问题](#2-性能问题)
3. [正确性问题](#3-正确性问题)
4. [安全问题](#4-安全问题)
5. [缺失功能 vs 官方规范](#5-缺失功能-vs-官方规范)
6. [改进优先级](#6-改进优先级)
7. [建议代码修改](#7-建议代码修改)

---

## 1. 架构分析

### 1.1 当前设计

```
┌──────────────┐     HTTP/SSE      ┌──────────────┐     HTTP/SSE     ┌──────────────┐
│  Codex CLI   │ ───────────────→  │   代理服务器   │ ─────────────→  │  MiMo Token  │
│  (客户端)     │ ←─────────────── │  (FastAPI)    │ ←───────────── │  Plan API    │
└──────────────┘   Responses API   └──────────────┘  Chat Completions └──────────────┘
                                    127.0.0.1:8000                    token-plan-cn.xiaomimimo.com
```

### 1.2 数据流

**请求路径：**
1. 客户端发送 `POST /v1/responses`（Responses API 格式）
2. 中间件检查请求体大小（`limit_request_body`，L44-49）
3. 主端点 `proxy_responses`（L536-608）：
   - 可选的 auth 检查（L540-543）
   - 解析 JSON body（L546-548）
   - `to_chat_completions()` 转换请求格式（L556）
   - 设置 headers 和可选的 stream_options（L559-566）
   - 非流式：`_http_client.post()` 发送请求，`to_responses_format()` 转换响应
   - 流式：返回 `StreamingResponse`，内部调用 `stream_response()` 生成器

**流式路径（`stream_response`，L259-468）：**
1. 生成响应 ID 和消息 ID
2. 发送 `response.created` 和 `response.in_progress` 事件
3. 使用 `_http_client.stream()` 建立上游 SSE 连接
4. 逐行读取上游 SSE 事件，解析为 Chat Completions chunk
5. 根据 chunk 内容生成对应的 Responses API 事件
6. 流结束后发送 `response.completed` 和 `[DONE]`

### 1.3 优势

- ✅ **共享 httpx.AsyncClient**：使用 `lifespan` 管理（L29-38），支持连接池复用
- ✅ **异步架构**：基于 FastAPI + httpx，非阻塞 I/O
- ✅ **完善的错误处理**：覆盖超时、JSON 解析错误、上游错误等场景
- ✅ **工具调用双向转换**：扁平格式 ↔ 嵌套格式，处理完整
- ✅ **推理内容支持**：reasoning_content 字段的流式和非流式处理
- ✅ **请求体大小限制**：10MB 上限（L42），防止滥用
- ✅ **可选的代理认证**：PROXY_AUTH_TOKEN 支持（L540-543）
- ✅ **Content-Type 校验**：检查上游返回格式（L583-585）

---

## 2. 性能问题

### 2.1 JSON 序列化开销（严重）

**位置：** `stream_response()` 函数，L269-468，几乎每一行 yield 都包含 `json.dumps()`

**问题：** 流式传输中每个 SSE 事件都调用 `json.dumps()` 进行序列化。对于长文本生成，每个 token 会产生一个 `response.output_text.delta` 事件，导致大量 JSON 序列化调用。

**量化分析：**
- 假设生成 1000 token，每个 token 对应一个 delta 事件
- 每个 delta 事件需要 `json.dumps({"type": "response.output_text.delta", ...})`
- 每个 `json.dumps` 调用耗时约 1-5μs（小字典）
- 总开销：约 1-5ms，对于 1000 token 的响应约 0.1%-0.5%

**具体位置：**
- L269: `response.created` 事件
- L270: `response.in_progress` 事件
- L325: `response.output_item.added`（reasoning）
- L336: `response.output_item.done`（reasoning）
- L340: `response.output_item.added`（message）
- L341: `response.content_part.added`
- L344: `response.output_text.delta`（**最高频**）
- L352: `response.output_item.done`（reasoning，关闭时）
- L357-358: `response.content_part.done` + `response.output_item.done`（message）
- L397: `response.function_call_arguments.delta`
- L439: `response.output_item.done`（function_call）
- L467: `response.completed`

**建议：** 可以预构建事件字典模板，仅更新 delta 字段，或使用 `orjson` 替代标准 `json`（快 2-10 倍）。

### 2.2 httpx 超时配置

**位置：** L32

```python
_http_client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30))
```

**问题：**
- `timeout=300` 是总超时 300 秒（5 分钟），对于长文本生成是合理的
- `connect=30` 是连接超时 30 秒，合理
- **但是：** 没有单独设置 `read` 超时。对于流式传输，如果上游在某个 token 之间暂停超过 300 秒，连接会被中断
- 没有 `write` 超时设置（对于 POST 请求不太关键）

**建议：** 为流式传输设置更细粒度的超时：

```python
timeout = httpx.Timeout(
    connect=30.0,
    read=300.0,    # 单次读取超时
    write=60.0,    # 发送请求体超时
    pool=10.0,     # 连接池获取连接超时
)
```

### 2.3 请求体中间件开销

**位置：** L44-49

```python
@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_SIZE:
        return JSONResponse(...)
    return await call_next(request)
```

**问题：**
- 仅检查 `Content-Length` header，如果客户端不发送此 header（如 chunked transfer encoding），限制失效
- `int(content_length)` 没有异常处理，如果 Content-Length 不是合法数字会抛出 `ValueError`
- 每个请求都会执行此中间件，虽然开销很小

**影响：** 低风险。大多数 HTTP 客户端会发送 Content-Length。

### 2.4 SSE 事件构造开销

**位置：** L259-468 中的 f-string 模板

**问题：** 每个 SSE 事件使用 f-string 构造，包含 JSON 序列化 + 字符串拼接。在 Python 中，f-string 本身开销很小，但与 `json.dumps()` 结合时有一定开销。

**量化：** 每个事件约 1-3μs 的字符串构造开销。1000 token 约 1-3ms。

### 2.5 内存：full_reasoning / full_content 累积

**位置：** L272-273

```python
full_reasoning = ""
full_content = ""
```

**问题：** 
- `full_reasoning` 和 `full_content` 在流式传输过程中不断累积所有内容
- 对于超长响应（如生成数万 token），这些字符串会占用大量内存
- 在 L326: `full_reasoning += reasoning`（字符串拼接，O(n) 每次）
- 在 L343: `full_content += content`（字符串拼接，O(n) 每次）
- **更严重：** 在 `response.output_item.done` 事件中，完整的 `full_content` 会被嵌入事件（L358, L431, L467），导致单个 SSE 事件可能非常大

**内存估算：** 生成 50K token 的回复，假设平均 token 4 字符 → 约 200KB 累积字符串。对于极端情况（100K+ token），可能达到 MB 级别。

**建议：** 
1. 使用 `io.StringIO` 或列表收集代替字符串拼接
2. 在 `response.output_item.done` 中仅发送最终状态标记，不嵌入完整内容
3. 但注意：官方规范要求 `done` 事件包含完整 item

### 2.6 连接复用（优点）

**位置：** L27, L32, L36

```python
_http_client: httpx.AsyncClient | None = None
# 在 lifespan 中创建
_http_client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30))
# 在 lifespan 中关闭
await _http_client.aclose()
```

**评价：** 这是正确的做法。httpx.AsyncClient 内部维护连接池，多个请求可以复用 TCP 连接，避免了每次请求都建立新连接的开销。

---

## 3. 正确性问题

### 3.1 响应对象缺少字段

**位置：** `to_responses_format()` 函数，L517-529

当前返回的响应对象：

```python
return {
    "id": resp_id,
    "object": "response",       # ✅ 正确
    "created_at": int(time.time()),  # ✅ 正确
    "model": model,
    "output": output,
    "usage": {...},
    "status": "completed",      # ✅ 正确
}
```

**缺少的官方规范字段：**

| 字段 | 类型 | 说明 | 影响 |
|------|------|------|------|
| `incomplete_details` | object/null | 当响应被截断时的详情 | 中 — 当 status 为 incomplete 时需要 |
| `max_output_tokens` | integer/null | 请求中的 max_output_tokens 值 | 低 — 回传请求参数 |
| `previous_response_id` | string/null | 上一个响应的 ID | 低 — 代理不支持此功能 |
| `metadata` | object/null | 请求中的 metadata | 低 — 代理不透传 |
| `service_tier` | string/null | 服务层级 | 低 — 上游未提供 |
| `temperature` | number/null | 实际使用的温度 | 低 — 回传请求参数 |
| `top_p` | number/null | 实际使用的 top_p | 低 — 回传请求参数 |
| `tools` | array/null | 实际使用的工具定义 | 低 — 回传请求参数 |
| `tool_choice` | any/null | 实际使用的 tool_choice | 低 — 回传请求参数 |
| `reasoning` | object/null | 推理配置摘要 | 低 |
| `output_text` | object/null | 输出文本配置 | 低 |

**最严重缺失：** `incomplete_details`。当上游返回 `finish_reason: "length"` 时，代理应标记 `status: "incomplete"` 并提供 `incomplete_details`。

### 3.2 流式：response.in_progress body 不完整

**位置：** L270

```python
yield f"data: {json.dumps({'type': 'response.in_progress', 'response': {'id': resp_id, 'status': 'in_progress'}})}\n\n"
```

**问题：** 根据官方规范，`response.in_progress` 的 `response` 字段应包含完整的响应对象（与 `response.created` 相同的结构），而不仅仅是 `id` 和 `status`。当前实现缺少 `object`、`model`、`created_at`、`output` 等字段。

**影响：** 低 — 大多数客户端能处理简化版本，但不符合规范。

### 3.3 工具调用 delta 事件结构

**位置：** L397

```python
yield f"data: {json.dumps({'type': 'response.function_call_arguments.delta', 'output_index': tool_calls_output_index + tc_index, 'item': {'type': 'function_call', 'id': call_id, 'call_id': call_id, 'name': acc['name'], 'arguments': tc_args}})}\n\n"
```

**问题分析：**
1. 根据官方规范，`response.function_call_arguments.delta` 事件的结构应为：
   ```json
   {
     "type": "response.function_call_arguments.delta",
     "item_id": "call_xxx",
     "output_index": 0,
     "content_index": 0,
     "arguments_delta": "{\"city\":"
   }
   ```
   而当前实现使用了 `item` 对象包含完整信息。

2. **`item_id` vs `item`：** 官方规范使用顶层 `item_id` 字段，而非嵌套的 `item` 对象。

3. **`arguments_delta` vs `item.arguments`：** 官方规范使用 `arguments_delta` 字段名。

4. **`content_index`：** 官方规范中 function_call 的 delta 事件包含 `content_index` 字段，当前实现缺少。

**影响：** 高 — Codex CLI 可能无法正确解析这些事件，导致工具调用失败。

### 3.4 缺少 GET /v1/responses/{response_id}

**位置：** 无（整个代理中不存在）

**问题：** 官方规范提供了 `GET /v1/responses/{response_id}` 端点，用于获取已完成或进行中的响应。代理不支持此端点。

**影响：** 中 — 某些客户端可能尝试轮询响应状态。

### 3.5 缺少 DELETE /v1/responses/{response_id}

**位置：** 无

**问题：** 官方规范提供了 `DELETE /v1/responses/{response_id}` 端点，用于取消进行中的响应。

**影响：** 低 — 大多数客户端不会主动取消。

### 3.6 max_output_tokens 处理

**位置：** L221-222

```python
if max_tokens := data.get("max_output_tokens"):
    result["max_tokens"] = max_tokens
```

**问题：** 使用了 walrus 运算符 `:=`，当 `max_output_tokens` 的值为 `0` 时，条件为假，不会传递。虽然 `max_output_tokens=0` 不常见，但这是一个潜在的 bug。更重要的是，`max_output_tokens` 作为请求参数应始终回传到响应对象中。

**建议：** 使用 `"max_output_tokens" in data` 替代 walrus。

### 3.7 reasoning_content 流式关闭时机

**位置：** L334-337

```python
if reasoning_started and not reasoning_closed:
    reasoning_closed = True
    yield f"data: {json.dumps({...reasoning.output_item.done...})}\n\n"
    current_output_index += 1
```

**问题：** 当第一个 `content` delta 到达时关闭 reasoning。但如果某些上游 API 在 reasoning 和 content 之间有混合输出（先一些 reasoning，然后一些 content，然后更多 reasoning），当前实现会过早关闭 reasoning。

**影响：** 低 — MiMo 的 reasoning_content 和 content 不会混合。

---

## 4. 安全问题

### 4.1 API Key 硬编码在 config.py

**位置：** config.py L5

```python
API_KEY = os.environ.get("MIMO_API_KEY", "tp-crn4wehvqx4zh0xz0k4svjw6tcv38b7kj6y3tcq5k8w96300")
```

**问题：** 虽然环境变量优先，但 fallback 值包含真实的 API 密钥，且已提交到代码仓库。

**风险等级：** 高 — 任何有代码访问权限的人都可以获取此密钥。

**建议：** 
1. 立即轮换此 API 密钥
2. 使用 `.env` 文件或 Docker secrets 管理敏感信息
3. 在 config.py 中使用空字符串作为 fallback
4. 添加启动时的警告日志

### 4.2 无 CORS 头

**位置：** 整个应用

**问题：** 代理没有设置任何 CORS（Cross-Origin Resource Sharing）头。如果前端 Web 应用尝试调用此代理，会被浏览器阻止。

**影响：** 中 — 如果仅用于 CLI 工具（Codex CLI），影响不大。如果需要 Web 前端访问，必须添加 CORS 支持。

**建议：** 添加 CORS 中间件：
```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
```

### 4.3 无请求 ID 追踪

**位置：** 整个应用

**问题：** 没有为每个请求分配唯一 ID 并在日志中追踪。在生产环境中，当多个请求并发时，无法有效追踪请求的完整生命周期。

**建议：** 使用中间件生成请求 ID：
```python
import uuid
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
```

### 4.4 日志中可能泄露敏感信息

**位置：** L553

```python
log.info("Request: model=%s, stream=%s", model, is_stream)
```

**评价：** 当前日志只记录 model 和 stream，不记录用户输入内容，这是安全的。但如果将来添加更多日志，需要注意不要记录用户输入。

---

## 5. 缺失功能 vs 官方规范

### 5.1 previous_response_id

**官方规范：** 允许客户端通过 `previous_response_id` 字段引用上一个响应，代理自动管理多轮上下文。

**当前状态：** 未实现。

**影响：** 高 — Codex CLI 在多轮对话时可能依赖此功能。但 Codex CLI 当前发送的是完整的 input 数组，包含历史消息，因此此功能暂时不是必需的。

### 5.2 metadata 透传

**官方规范：** 请求中可以包含 `metadata` 对象（任意键值对），响应中回传。

**当前状态：** 未实现。

**影响：** 低 — 仅用于客户端自定义追踪。

### 5.3 stream_options

**官方规范：** `stream_options` 控制流式传输的行为，如 `include_usage: true` 在流中包含 usage 信息。

**当前状态：** 代理在 L561 硬编码了 `stream_options: {"include_usage": True}`，但没有将上游返回的 usage 信息传递给客户端。在 `response.completed` 事件中也没有包含 usage 信息。

**影响：** 中 — 客户端可能无法获取准确的 token 使用量。

### 5.4 response_format 透传

**官方规范：** `response_format` 控制输出格式（text/json_object/json_schema）。

**当前状态：** 已透传到上游（L245-246），但代理的响应对象中不包含此信息。

**影响：** 低。

### 5.5 background 模式

**官方规范：** `background: true` 允许异步处理请求，客户端可以通过 `GET /v1/responses/{id}` 获取结果。

**当前状态：** 未实现。

**影响：** 低 — Codex CLI 不使用此功能。

### 5.6 多模态 input content parts

**官方规范：** input message 的 content 可以包含 `image_url`、`input_image`、`input_audio` 等类型。

**当前状态：** L149-158 只处理了 `input_text` 和 `text` 类型，其他类型被忽略。

```python
if isinstance(content, list):
    text_parts = []
    for c in content:
        if isinstance(c, dict):
            if c.get("type") == "input_text":
                text_parts.append(c.get("text", ""))
            elif c.get("type") == "text":
                text_parts.append(c.get("text", ""))
    content = "".join(text_parts)
```

**影响：** 中 — 如果用户发送图片或多模态内容，会被静默丢弃。

### 5.7 output 参数

**官方规范：** `output` 参数允许客户端指定期望的输出类型（如 `"output": ["message"]` 或 `"output": ["function_call"]`）。

**当前状态：** 未实现。

**影响：** 低。

---

## 6. 改进优先级

### P0: 关键修复（必须立即处理）

| 编号 | 问题 | 位置 | 影响 |
|------|------|------|------|
| P0-1 | API Key 硬编码在 config.py | config.py L5 | 安全 |
| P0-2 | 工具调用 delta 事件结构不符合规范 | proxy.py L397 | 正确性 |
| P0-3 | max_output_tokens 使用 walrus 运算符可能跳过 0 值 | proxy.py L221 | 正确性 |

### P1: 性能优化（高优先级）

| 编号 | 问题 | 位置 | 预期收益 |
|------|------|------|---------|
| P1-1 | JSON 序列化可使用 orjson 加速 | L269-468 | 流式事件处理提速 2-10x |
| P1-2 | full_content 在 done 事件中嵌入完整文本，大响应时事件过大 | L358, L431, L467 | 内存/网络优化 |
| P1-3 | httpx 超时配置可更细粒度 | L32 | 流式传输稳定性 |
| P1-4 | 中间件 Content-Length 缺少异常处理 | L47 | 鲁棒性 |

### P2: 功能完善（中优先级）

| 编号 | 问题 | 影响 |
|------|------|------|
| P2-1 | response.in_progress body 不完整 | 规范合规 |
| P2-2 | response.completed 缺少 usage 信息 | 功能 |
| P2-3 | 响应对象缺少 incomplete_details 等字段 | 规范合规 |
| P2-4 | 缺少 GET /v1/responses/{response_id} | 规范合规 |
| P2-5 | 缺少请求 ID 追踪 | 可观测性 |
| P2-6 | 缺少 CORS 支持 | Web 前端兼容性 |

### P3: 锦上添花（低优先级）

| 编号 | 问题 | 影响 |
|------|------|------|
| P3-1 | previous_response_id 支持 | 多轮上下文管理 |
| P3-2 | metadata 透传 | 客户端追踪 |
| P3-3 | background 模式 | 异步处理 |
| P3-4 | 多模态 input 支持 | 多模态输入 |
| P3-5 | DELETE /v1/responses/{response_id} | 响应取消 |

---

## 7. 建议代码修改

### 7.1 P0-1: 移除硬编码 API Key

**修改 config.py：**

```python
# 修改前
API_KEY = os.environ.get("MIMO_API_KEY", "tp-crn4wehvqx4zh0xz0k4svjw6tcv38b7kj6y3tcq5k8w96300")

# 修改后
API_KEY = os.environ.get("MIMO_API_KEY", "")
if not API_KEY:
    import logging
    logging.getLogger("config").warning(
        "⚠️  MIMO_API_KEY 未设置！请通过环境变量设置上游 API 密钥。\n"
        "   示例: export MIMO_API_KEY=your_key_here"
    )
```

### 7.2 P0-2: 修正工具调用 delta 事件结构

**修改 proxy.py L397：**

```python
# 修改前
yield f"data: {json.dumps({'type': 'response.function_call_arguments.delta', 'output_index': tool_calls_output_index + tc_index, 'item': {'type': 'function_call', 'id': call_id, 'call_id': call_id, 'name': acc['name'], 'arguments': tc_args}})}\n\n"

# 修改后（符合官方规范）
yield f"data: {json.dumps({'type': 'response.function_call_arguments.delta', 'item_id': call_id, 'output_index': tool_calls_output_index + tc_index, 'content_index': 0, 'arguments_delta': tc_args})}\n\n"
```

**注意：** 此修改可能需要同时修改 Codex CLI 的解析逻辑，或者保持向后兼容。

### 7.3 P1-1: 使用 orjson 加速 JSON 序列化

**修改 requirements.txt：**

```
fastapi
uvicorn[standard]
httpx
orjson
```

**修改 proxy.py 顶部：**

```python
# 修改前
import json

# 修改后
try:
    import orjson as json
except ImportError:
    import json

# 同时修改为兼容的调用方式
def _dumps(obj):
    try:
        return orjson.dumps(obj).decode()
    except NameError:
        return json.dumps(obj)
```

**影响范围：** 所有 `json.dumps()` 调用（约 20+ 处），需要统一替换为 `_dumps()`。

### 7.4 P1-4: 中间件异常处理

**修改 proxy.py L44-49：**

```python
# 修改前
@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_SIZE:
        return JSONResponse(status_code=413, content={"error": {"type": "invalid_request", "message": "Request body too large"}})
    return await call_next(request)

# 修改后
@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={"error": {"type": "invalid_request", "message": "Request body too large"}}
                )
        except (ValueError, TypeError):
            pass  # Invalid Content-Length header, let it through
    return await call_next(request)
```

### 7.5 P2-2: 流式 response.completed 包含 usage

**修改 proxy.py L442-468：**

```python
# 修改前（L467）
yield f"data: {json.dumps({'type': 'response.completed', 'response': {'id': resp_id, 'object': 'response', 'model': model, 'status': 'completed', 'output': output}})}\n\n"

# 修改后 — 需要从 stream 中捕获 usage（需要增加 usage 累积逻辑）
# 在 stream_response 开头添加：
usage_data = {}

# 在 stream 循环中捕获 usage chunk（上游 stream_options.include_usage 会在最后一个 chunk 返回 usage）：
# if chunk.get("usage"):
#     usage_data = chunk["usage"]

# 在 L467 修改为：
yield f"data: {json.dumps({'type': 'response.completed', 'response': {'id': resp_id, 'object': 'response', 'model': model, 'status': 'completed', 'output': output, 'usage': {'input_tokens': usage_data.get('prompt_tokens', 0), 'output_tokens': usage_data.get('completion_tokens', 0), 'total_tokens': usage_data.get('total_tokens', 0)}}})}\n\n"
```

### 7.6 P2-5: 添加请求 ID 追踪中间件

**修改 proxy.py：**

```python
# 在 imports 后添加
import uuid

# 在 limit_request_body 中间件之前添加
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

# 修改 logging 格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
```

### 7.7 P2-6: 添加 CORS 支持

**修改 proxy.py：**

```python
from fastapi.middleware.cors import CORSMiddleware

# 在 app 创建后添加
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 7.8 P1-2: 优化大响应的 done 事件

**修改 `response.output_item.done` 事件中的 content：**

```python
# 修改前（L358）
yield f"data: {json.dumps({'type': 'response.output_item.done', 'output_index': current_output_index, 'item': {'type': 'message', 'id': msg_id, 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': full_content}]}})}\n\n"

# 修改后 — 对于超长内容，使用截断的 summary
MAX_DONE_TEXT_LEN = 10000  # 10K 字符上限
done_text = full_content if len(full_content) <= MAX_DONE_TEXT_LEN else full_content[:MAX_DONE_TEXT_LEN] + "...[truncated]"
yield f"data: {json.dumps({'type': 'response.output_item.done', 'output_index': current_output_index, 'item': {'type': 'message', 'id': msg_id, 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': done_text}]}})}\n\n"
```

**权衡：** 截断可能导致客户端丢失完整内容。更安全的做法是只在 `response.completed` 中提供完整的 output，而在中间的 `done` 事件中不包含完整 text（但官方规范要求包含）。

### 7.9 P0-3: 修复 max_output_tokens 处理

**修改 proxy.py L221-222：**

```python
# 修改前
if max_tokens := data.get("max_output_tokens"):
    result["max_tokens"] = max_tokens

# 修改后
if "max_output_tokens" in data:
    result["max_tokens"] = data["max_output_tokens"]
```

---

## 附录：审查文件清单

| 文件 | 行数 | 审查状态 |
|------|------|---------|
| proxy.py | 635 | ✅ 全面审查 |
| config.py | 17 | ✅ 全面审查 |
| requirements.txt | 3 | ✅ 审查 |
| API_REFERENCE.md | - | ✅ 已创建 |
| PERFORMANCE_AUDIT.md | - | ✅ 本文件 |

## 附录：关键行号索引

| 行号 | 代码 | 审查结论 |
|------|------|---------|
| L5 | API_KEY fallback 硬编码 | ⚠️ 安全问题 |
| L32 | httpx.AsyncClient timeout | ⚠️ 可优化 |
| L42 | MAX_BODY_SIZE = 10MB | ✅ 合理 |
| L44-49 | 请求体限制中间件 | ⚠️ 缺少异常处理 |
| L56-82 | tool 转换函数 | ✅ 正确 |
| L85-102 | tool_choice 转换函数 | ✅ 正确 |
| L105-204 | convert_input_to_messages | ✅ 完善，⚠️ 不支持多模态 |
| L207-248 | to_chat_completions | ✅ 正确，⚠️ walrus 问题 |
| L259-468 | stream_response | ⚠️ 性能可优化，⚠️ delta 事件结构 |
| L272-273 | full_reasoning/full_content 累积 | ⚠️ 内存开销 |
| L397 | function_call_arguments.delta | ❌ 结构不符合规范 |
| L475-529 | to_responses_format | ⚠️ 缺少多个官方字段 |
| L536-608 | proxy_responses 主端点 | ✅ 架构合理 |
| L560-561 | stream_options 硬编码 | ⚠️ usage 未传递给客户端 |
