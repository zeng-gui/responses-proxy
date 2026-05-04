# Responses API 接口详细说明文档

> 本文档详细说明本代理服务器提供的 Responses API 接口规范，包括请求格式、响应格式、流式传输协议，以及代理层的字段映射关系。

---

## 目录

1. [概述](#1-概述)
2. [创建响应 — POST /v1/responses](#2-创建响应--post-v1responses)
3. [请求字段详解](#3-请求字段详解)
4. [响应对象详解](#4-响应对象详解)
5. [流式传输事件 (SSE)](#5-流式传输事件-sse)
6. [代理字段映射](#6-代理字段映射)
7. [完整示例](#7-完整示例)
8. [错误响应格式](#8-错误响应格式)
9. [与 Chat Completions API 的差异](#9-与-chat-completions-api-的差异)
10. [辅助端点](#10-辅助端点)

---

## 1. 概述

### 什么是 Responses API？

Responses API 是 OpenAI 于 2024 年推出的新一代 API 接口，用于替代传统的 Chat Completions API。它由 Codex CLI、Codex Agent 等客户端工具直接使用。

### 为什么需要 Responses API？

| 特性 | Chat Completions API | Responses API |
|------|---------------------|---------------|
| 输入格式 | messages 数组 | input 数组（更灵活） |
| 工具调用 | 嵌套在 message.tool_calls 中 | 扁平化 function_call / function_call_output 项 |
| 推理内容 | 无标准字段 | reasoning output item |
| 流式协议 | 简单 delta | 丰富的事件类型（added/done/delta） |
| 多轮上下文 | messages 数组手动管理 | previous_response_id 自动管理 |
| 中间状态 | 无 | status 字段（in_progress / completed / failed） |

### 本代理的作用

本代理服务器（`127.0.0.1:8000`）将 Codex CLI 发出的 **Responses API** 请求翻译为 **Chat Completions API** 请求，转发至小米 MiMo Token Plan API（`https://token-plan-cn.xiaomimimo.com/v1`），然后将上游的 Chat Completions 响应重新格式化为 Responses API 格式返回。

```
Codex CLI → POST /v1/responses → 代理 → POST /chat/completions → MiMo API
         ← Responses API 格式 ← 代理 ← Chat Completions 格式 ←
```

---

## 2. 创建响应 — POST /v1/responses

### 端点

```
POST http://127.0.0.1:8000/v1/responses
```

### 请求头

| Header | 值 | 必需 |
|--------|---|------|
| `Content-Type` | `application/json` | 是 |
| `Authorization` | `Bearer <PROXY_AUTH_TOKEN>` | 仅当代理配置了 PROXY_AUTH_TOKEN 时 |

### 请求体结构

```json
{
  "model": "mimo-v2.5",
  "input": "你好",
  "instructions": "你是一个有帮助的助手",
  "tools": [...],
  "tool_choice": "auto",
  "stream": false,
  "temperature": 0.7,
  "top_p": 1.0,
  "max_output_tokens": 4096,
  "frequency_penalty": 0.0,
  "presence_penalty": 0.0,
  "stop": ["\n\n"],
  "seed": 42,
  "response_format": {"type": "text"}
}
```

---

## 3. 请求字段详解

### 3.1 核心字段

#### `model`（string，必需）

使用的模型名称。直接传递给上游。

```json
"model": "mimo-v2.5"
```

支持的模型（MiMo Token Plan）：
- `mimo-v2.5-pro`
- `mimo-v2.5`（默认推荐）
- `mimo-v2-pro`
- `mimo-v2-omni`

#### `input`（string 或 array，必需）

用户输入内容。支持两种格式：

**格式一：纯字符串**
```json
"input": "请解释量子计算的基本原理"
```

**格式二：消息数组**
```json
"input": [
  {"role": "user", "content": "你好"},
  {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
  {"role": "user", "content": "解释一下量子计算"}
]
```

#### `instructions`（string，可选）

系统指令，设置 AI 的行为和角色。代理将其转换为 Chat Completions 的 system message。

```json
"instructions": "你是一个专业的编程助手，回答要简洁准确。"
```

#### `stream`（boolean，可选，默认 false）

是否使用流式传输。当为 `true` 时，代理返回 SSE 事件流。

### 3.2 生成参数

#### `temperature`（number，可选）

采样温度，范围 0.0 ~ 2.0。值越低输出越确定，值越高输出越随机。

```json
"temperature": 0.7
```

#### `top_p`（number，可选，默认 1.0）

核采样参数。与 temperature 二选一使用效果更佳。

```json
"top_p": 0.9
```

#### `max_output_tokens`（integer，可选）

最大输出 token 数。代理将其映射为 Chat Completions 的 `max_tokens`。

```json
"max_output_tokens": 4096
```

#### `frequency_penalty`（number，可选）

频率惩罚，范围 -2.0 ~ 2.0。正值降低重复 token 的概率。

#### `presence_penalty`（number，可选）

存在惩罚，范围 -2.0 ~ 2.0。正值鼓励模型谈论新话题。

#### `stop`（string 或 string[]，可选）

停止序列。当生成包含这些字符串时停止。

```json
"stop": ["\n\n", "END"]
```

#### `seed`（integer，可选）

随机种子，用于可复现输出。

#### `response_format`（object，可选）

输出格式控制。

```json
"response_format": {"type": "text"}
"response_format": {"type": "json_object"}
```

### 3.3 Input Item 类型

当 `input` 为数组时，每个元素可以是以下类型：

#### 字符串

```json
"input": ["第一条消息", "第二条消息"]
```
每个字符串被转换为 `{"role": "user", "content": string}`。

#### Message 对象

```json
{
  "type": "message",
  "role": "user",
  "content": "你好"
}
```

**支持的 role 值：**
| role | 代理处理 |
|------|---------|
| `user` | 直接映射为 user message |
| `assistant` | 直接映射为 assistant message |
| `system` | 映射为 system message |
| `developer` | 映射为 system message |

**content 格式：**
- 字符串：`"content": "Hello"`
- 数组（content parts）：`"content": [{"type": "input_text", "text": "..."}]` 或 `[{"type": "text", "text": "..."}]`

数组中的 `input_text` 和 `text` 类型会被提取并拼接。**注意：`image_url`、`input_image` 等类型当前不支持，会被忽略。**

#### Function Call 项（assistant 调用工具的结果）

```json
{
  "type": "function_call",
  "call_id": "call_abc123",
  "name": "get_weather",
  "arguments": "{\"city\": \"北京\"}"
}
```

代理将其转换为 Chat Completions 的 assistant message with tool_calls：
```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [{
    "id": "call_abc123",
    "type": "function",
    "function": {
      "name": "get_weather",
      "arguments": "{\"city\": \"北京\"}"
    }
  }]
}
```

#### Function Call Output 项（工具执行结果）

```json
{
  "type": "function_call_output",
  "call_id": "call_abc123",
  "output": "{\"temperature\": 25, \"condition\": \"晴天\"}"
}
```

代理将其转换为 Chat Completions 的 tool message：
```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "content": "{\"temperature\": 25, \"condition\": \"晴天\"}"
}
```

**注意：** 如果 `output` 是数组，代理会先 `json.dumps()` 序列化为字符串。

### 3.4 Tools 格式

Responses API 的工具定义采用扁平格式：

```json
{
  "tools": [
    {
      "type": "function",
      "name": "get_weather",
      "description": "获取指定城市的天气信息",
      "parameters": {
        "type": "object",
        "properties": {
          "city": {
            "type": "string",
            "description": "城市名称"
          }
        },
        "required": ["city"]
      }
    }
  ]
}
```

代理将其转换为 Chat Completions 的嵌套格式：

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取指定城市的天气信息",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string", "description": "城市名称"}
          },
          "required": ["city"]
        }
      }
    }
  ]
}
```

**智能检测：** 如果传入的工具已经是 Chat Completions 的嵌套格式（即存在 `function` 子对象且有 `name` 字段），代理会直接透传，不做二次包装。

### 3.5 Tool Choice

控制模型如何选择工具：

#### 字符串形式

```json
"tool_choice": "auto"      // 自动选择
"tool_choice": "none"      // 不使用工具
"tool_choice": "required"  // 必须使用工具
```

#### 对象形式

```json
{
  "tool_choice": {
    "type": "auto"
  }
}
```

```json
{
  "tool_choice": {
    "type": "function",
    "name": "get_weather",
    "mode": "auto"
  }
}
```

**代理映射规则：**

| Responses API 格式 | Chat Completions 格式 |
|--------------------|-----------------------|
| `"auto"` | `"auto"` |
| `"none"` | `"none"` |
| `"required"` | `"required"` |
| `{"type": "auto"}` | `"auto"` |
| `{"type": "required"}` | `"required"` |
| `{"type": "function", "name": "..."}` | `{"type": "function", "function": {"name": "..."}}` |

---

## 4. 响应对象详解

### 4.1 非流式响应（stream: false）

完整的响应对象结构：

```json
{
  "id": "resp_a1b2c3d4e5f6789012345678",
  "object": "response",
  "created_at": 1714838400,
  "model": "mimo-v2.5",
  "output": [
    {
      "type": "reasoning",
      "id": "rs_abc123def4567890123456",
      "summary": [
        {
          "type": "summary_text",
          "text": "用户询问了关于量子计算的问题，我需要解释..."
        }
      ]
    },
    {
      "type": "message",
      "id": "msg_xyz789abc123def4567890",
      "role": "assistant",
      "status": "completed",
      "content": [
        {
          "type": "output_text",
          "text": "量子计算是利用量子力学原理进行计算的技术..."
        }
      ]
    }
  ],
  "usage": {
    "input_tokens": 150,
    "output_tokens": 320,
    "total_tokens": 470
  },
  "status": "completed"
}
```

#### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 响应唯一标识符，格式为 `resp_<24字符hex>` |
| `object` | string | 固定值 `"response"` |
| `created_at` | integer | 创建时间（Unix 时间戳，秒） |
| `model` | string | 生成响应的模型名称 |
| `output` | array | 输出项数组，包含 reasoning / message / function_call |
| `usage` | object | token 使用量统计 |
| `status` | string | 响应状态：`completed` / `in_progress` / `failed` |

### 4.2 Output Item 类型

#### Reasoning 项

```json
{
  "type": "reasoning",
  "id": "rs_xxx",
  "summary": [
    {
      "type": "summary_text",
      "text": "推理过程的摘要（最多500字符）"
    }
  ]
}
```

**注意：** 代理从上游 MiMo 的 `reasoning_content` 字段提取推理内容。如果内容超过 500 字符，会被截断。

#### Message 项

```json
{
  "type": "message",
  "id": "msg_xxx",
  "role": "assistant",
  "status": "completed",
  "content": [
    {
      "type": "output_text",
      "text": "模型的回复文本"
    }
  ]
}
```

#### Function Call 项

```json
{
  "type": "function_call",
  "id": "fc_xxx",
  "call_id": "fc_xxx",
  "name": "get_weather",
  "arguments": "{\"city\": \"北京\"}"
}
```

| 字段 | 说明 |
|------|------|
| `id` | 函数调用 ID |
| `call_id` | 调用 ID（与 id 相同），用于关联 tool result |
| `name` | 被调用的函数名 |
| `arguments` | 函数参数（JSON 字符串） |

### 4.3 Usage 字段

```json
{
  "usage": {
    "input_tokens": 150,
    "output_tokens": 320,
    "total_tokens": 470
  }
}
```

| 字段 | 说明 | 映射来源 |
|------|------|---------|
| `input_tokens` | 输入 token 数 | `usage.prompt_tokens` |
| `output_tokens` | 输出 token 数 | `usage.completion_tokens` |
| `total_tokens` | 总 token 数 | `usage.total_tokens` |

**注意：** `completion_tokens_details` 和 `prompt_tokens_details` 在非流式响应中未包含（上游未提供详细拆分）。

---

## 5. 流式传输事件 (SSE)

当 `stream: true` 时，代理返回 `Content-Type: text/event-stream` 格式的事件流。

### 5.1 事件序列概览

完整的流式事件序列（文本响应）：

```
data: {"type":"response.created", "response":{...}}
data: {"type":"response.in_progress", "response":{...}}

[可选：推理阶段]
data: {"type":"response.output_item.added", "output_index":0, "item":{"type":"reasoning",...}}
data: {"type":"response.output_item.done", "output_index":0, "item":{"type":"reasoning",...}}

[文本生成阶段]
data: {"type":"response.output_item.added", "output_index":1, "item":{"type":"message",...}}
data: {"type":"response.content_part.added", "output_index":1, "content_index":0, "part":{...}}
data: {"type":"response.output_text.delta", "output_index":1, "content_index":0, "delta":"你"}
data: {"type":"response.output_text.delta", "output_index":1, "content_index":0, "delta":"好"}
data: {"type":"response.content_part.done", "output_index":1, "content_index":0, "part":{...}}
data: {"type":"response.output_item.done", "output_index":1, "item":{"type":"message",...}}

[完成]
data: {"type":"response.completed", "response":{...}}
data: [DONE]
```

### 5.2 各事件详解

#### `response.created`

流式开始时的第一个事件。包含响应对象的基本信息。

```json
{
  "type": "response.created",
  "response": {
    "id": "resp_xxx",
    "object": "response",
    "model": "mimo-v2.5",
    "status": "in_progress"
  }
}
```

#### `response.in_progress`

紧随 `response.created` 之后，表示响应正在生成中。

```json
{
  "type": "response.in_progress",
  "response": {
    "id": "resp_xxx",
    "status": "in_progress"
  }
}
```

**注意：** 代理发送的 `response.in_progress` 事件中 `response` 对象不包含完整的 `body` 字段（如 `object`、`model` 等）。这是与官方规范的一个小差异。

#### `response.output_item.added`

表示一个新的输出项（reasoning / message / function_call）开始生成。

```json
{
  "type": "response.output_item.added",
  "output_index": 0,
  "item": {
    "type": "reasoning",
    "id": "rs_xxx",
    "summary": []
  }
}
```

对于 message 类型：
```json
{
  "type": "response.output_item.added",
  "output_index": 1,
  "item": {
    "type": "message",
    "id": "msg_xxx",
    "role": "assistant",
    "status": "in_progress",
    "content": []
  }
}
```

#### `response.content_part.added`

表示 message 输出项中的一个新的内容部分开始。

```json
{
  "type": "response.content_part.added",
  "output_index": 1,
  "content_index": 0,
  "part": {
    "type": "output_text",
    "text": ""
  }
}
```

#### `response.output_text.delta`

文本内容的增量数据。这是最频繁的事件。

```json
{
  "type": "response.output_text.delta",
  "output_index": 1,
  "content_index": 0,
  "delta": "这是新增的文本片段"
}
```

#### `response.content_part.done`

表示一个内容部分完成。

```json
{
  "type": "response.content_part.done",
  "output_index": 1,
  "content_index": 0,
  "part": {
    "type": "output_text",
    "text": "完整的文本内容"
  }
}
```

#### `response.function_call_arguments.delta`

工具调用参数的增量数据。

```json
{
  "type": "response.function_call_arguments.delta",
  "output_index": 2,
  "item": {
    "type": "function_call",
    "id": "call_xxx",
    "call_id": "call_xxx",
    "name": "get_weather",
    "arguments": "{\"ci"
  }
}
```

**注意：** 每个 delta 事件中的 `arguments` 是当前增量的参数片段，不是累计值。

#### `response.output_item.done`

表示一个输出项完成。

```json
{
  "type": "response.output_item.done",
  "output_index": 1,
  "item": {
    "type": "message",
    "id": "msg_xxx",
    "role": "assistant",
    "status": "completed",
    "content": [{"type": "output_text", "text": "完整的回复"}]
  }
}
```

#### `response.completed`

流式结束前的最后一个数据事件。包含完整的响应对象。

```json
{
  "type": "response.completed",
  "response": {
    "id": "resp_xxx",
    "object": "response",
    "model": "mimo-v2.5",
    "status": "completed",
    "output": [
      {"type": "message", "id": "msg_xxx", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "..."}]}
    ]
  }
}
```

#### `[DONE]`

流式传输结束标记。不包含 JSON 数据。

```
data: [DONE]
```

### 5.3 流式事件中的特殊字段

每个事件都包含以下公共字段：

| 字段 | 说明 |
|------|------|
| `type` | 事件类型（见上表） |
| `output_index` | 输出项在 output 数组中的索引 |
| `content_index` | 内容部分在 content 数组中的索引（仅 content_part 相关事件） |

### 5.4 工具调用的流式事件序列

当模型决定调用工具时的事件序列：

```
[推理阶段（可选）]
data: {"type":"response.output_item.added", "output_index":0, "item":{"type":"reasoning",...}}
data: {"type":"response.output_item.done", "output_index":0, "item":{"type":"reasoning",...}}

[工具调用参数生成]
data: {"type":"response.function_call_arguments.delta", "output_index":1, "item":{"type":"function_call", "name":"get_weather", "arguments":""}}
data: {"type":"response.function_call_arguments.delta", "output_index":1, "item":{"type":"function_call", "name":"get_weather", "arguments":"{\"ci"}}
data: {"type":"response.function_call_arguments.delta", "output_index":1, "item":{"type":"function_call", "name":"get_weather", "arguments":"ty\":\"北京\"}"}}
data: {"type":"response.output_item.done", "output_index":1, "item":{"type":"function_call", "id":"call_xxx", "call_id":"call_xxx", "name":"get_weather", "arguments":"{\"city\":\"北京\"}"}}

[完成]
data: {"type":"response.completed", "response":{...}}
data: [DONE]
```

---

## 6. 代理字段映射

### 6.1 请求映射（Responses API → Chat Completions API）

| Responses API 字段 | Chat Completions 字段 | 说明 |
|--------------------|----------------------|------|
| `model` | `model` | 直接传递 |
| `input` | `messages` | 经 `convert_input_to_messages()` 转换 |
| `instructions` | `messages[0]` (system) | 作为 system message 插入 |
| `tools` | `tools` | 扁平格式 → 嵌套格式 |
| `tool_choice` | `tool_choice` | 对象格式 → 字符串/嵌套格式 |
| `temperature` | `temperature` | 直接传递 |
| `top_p` | `top_p` | 直接传递 |
| `max_output_tokens` | `max_tokens` | 字段名映射 |
| `frequency_penalty` | `frequency_penalty` | 直接传递 |
| `presence_penalty` | `presence_penalty` | 直接传递 |
| `stop` | `stop` | 直接传递 |
| `seed` | `seed` | 直接传递 |
| `response_format` | `response_format` | 直接传递 |
| `stream` | `stream` | 直接传递 |

### 6.2 Input Item 映射

| Responses API Item | Chat Completions Message |
|--------------------|--------------------------|
| `{"type": "message", "role": "user", "content": "..."}` | `{"role": "user", "content": "..."}` |
| `{"type": "message", "role": "assistant", "content": "..."}` | `{"role": "assistant", "content": "..."}` |
| `{"type": "message", "role": "system", ...}` | `{"role": "system", "content": "..."}` |
| `{"type": "message", "role": "developer", ...}` | `{"role": "system", "content": "..."}` |
| `{"type": "function_call", "call_id": "x", "name": "f", "arguments": "{}"}` | `{"role": "assistant", "content": null, "tool_calls": [{"id": "x", "type": "function", "function": {"name": "f", "arguments": "{}"}}]}` |
| `{"type": "function_call_output", "call_id": "x", "output": "..."}` | `{"role": "tool", "tool_call_id": "x", "content": "..."}` |
| `"纯字符串"` | `{"role": "user", "content": "纯字符串"}` |

### 6.3 响应映射（Chat Completions → Responses API）

| Chat Completions 字段 | Responses API 字段 | 说明 |
|----------------------|-------------------|------|
| 自动生成 | `id` | 格式 `resp_<hex24>` |
| 自动生成 | `object` | 固定值 `"response"` |
| 当前时间 | `created_at` | Unix 时间戳 |
| `model` (来自请求) | `model` | 代理回传请求中的 model |
| `choices[0].message` | `output[]` | 组装为 output items |
| `usage.prompt_tokens` | `usage.input_tokens` | 字段名映射 |
| `usage.completion_tokens` | `usage.output_tokens` | 字段名映射 |
| `usage.total_tokens` | `usage.total_tokens` | 直接传递 |
| (无) | `status` | 固定值 `"completed"` |

### 6.4 Output Item 映射

| Chat Completions 字段 | Responses API Output Item |
|----------------------|--------------------------|
| `message.reasoning_content` | `{"type": "reasoning", "summary": [{"type": "summary_text", "text": "..."}]}` |
| `message.tool_calls[]` | `{"type": "function_call", "id": "...", "call_id": "...", "name": "...", "arguments": "..."}` |
| `message.content` | `{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "..."}]}` |

---

## 7. 完整示例

### 7.1 非流式请求 — 简单文本对话

**请求：**
```json
POST /v1/responses
Content-Type: application/json

{
  "model": "mimo-v2.5",
  "input": "你好，请简单介绍一下自己",
  "stream": false
}
```

**响应：**
```json
{
  "id": "resp_a1b2c3d4e5f6789012345678",
  "object": "response",
  "created_at": 1714838400,
  "model": "mimo-v2.5",
  "output": [
    {
      "type": "message",
      "id": "msg_xyz789abc123def4567890",
      "role": "assistant",
      "status": "completed",
      "content": [
        {
          "type": "output_text",
          "text": "你好！我是 MiMo，一个由小米开发的 AI 助手。我可以帮助你解答问题、编写代码、进行对话等。有什么我可以帮你的吗？"
        }
      ]
    }
  ],
  "usage": {
    "input_tokens": 20,
    "output_tokens": 50,
    "total_tokens": 70
  },
  "status": "completed"
}
```

### 7.2 流式请求 — 简单文本对话

**请求：**
```json
POST /v1/responses
Content-Type: application/json

{
  "model": "mimo-v2.5",
  "input": "你好",
  "stream": true
}
```

**响应（SSE 事件流）：**
```
data: {"type":"response.created","response":{"id":"resp_a1b2c3d4e5f6789012345678","object":"response","model":"mimo-v2.5","status":"in_progress"}}

data: {"type":"response.in_progress","response":{"id":"resp_a1b2c3d4e5f6789012345678","status":"in_progress"}}

data: {"type":"response.output_item.added","output_index":0,"item":{"type":"message","id":"msg_xyz789abc123def4567890","role":"assistant","status":"in_progress","content":[]}}

data: {"type":"response.content_part.added","output_index":0,"content_index":0,"part":{"type":"output_text","text":""}}

data: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"你好"}

data: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"！我是"}

data: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"MiMo"}

data: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"。"}

data: {"type":"response.content_part.done","output_index":0,"content_index":0,"part":{"type":"output_text","text":"你好！我是MiMo。"}}

data: {"type":"response.output_item.done","output_index":0,"item":{"type":"message","id":"msg_xyz789abc123def4567890","role":"assistant","status":"completed","content":[{"type":"output_text","text":"你好！我是MiMo。"}]}}

data: {"type":"response.completed","response":{"id":"resp_a1b2c3d4e5f6789012345678","object":"response","model":"mimo-v2.5","status":"completed","output":[{"type":"message","id":"msg_xyz789abc123def4567890","role":"assistant","status":"completed","content":[{"type":"output_text","text":"你好！我是MiMo。"}]}]}}

data: [DONE]

```

### 7.3 带工具调用的请求

**请求：**
```json
POST /v1/responses
Content-Type: application/json

{
  "model": "mimo-v2.5",
  "input": "北京今天天气怎么样？",
  "tools": [
    {
      "type": "function",
      "name": "get_weather",
      "description": "获取指定城市的天气信息",
      "parameters": {
        "type": "object",
        "properties": {
          "city": {"type": "string", "description": "城市名称"}
        },
        "required": ["city"]
      }
    }
  ],
  "tool_choice": "auto",
  "stream": false
}
```

**响应：**
```json
{
  "id": "resp_d4e5f6a7b8c9012345678901",
  "object": "response",
  "created_at": 1714838400,
  "model": "mimo-v2.5",
  "output": [
    {
      "type": "function_call",
      "id": "call_abc123def456789012345",
      "call_id": "call_abc123def456789012345",
      "name": "get_weather",
      "arguments": "{\"city\":\"北京\"}"
    }
  ],
  "usage": {
    "input_tokens": 200,
    "output_tokens": 30,
    "total_tokens": 230
  },
  "status": "completed"
}
```

### 7.4 多轮对话（带工具结果）

**请求：**
```json
POST /v1/responses
Content-Type: application/json

{
  "model": "mimo-v2.5",
  "instructions": "你是一个天气助手",
  "input": [
    {"role": "user", "content": "北京天气怎么样？"},
    {
      "type": "function_call",
      "call_id": "call_abc123",
      "name": "get_weather",
      "arguments": "{\"city\":\"北京\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "call_abc123",
      "output": "{\"temperature\":25,\"condition\":\"晴\",\"humidity\":40}"
    }
  ],
  "tools": [
    {
      "type": "function",
      "name": "get_weather",
      "description": "获取指定城市的天气信息",
      "parameters": {
        "type": "object",
        "properties": {
          "city": {"type": "string"}
        },
        "required": ["city"]
      }
    }
  ],
  "stream": false
}
```

**代理内部转换的 Chat Completions 请求：**
```json
{
  "model": "mimo-v2.5",
  "messages": [
    {"role": "system", "content": "你是一个天气助手"},
    {"role": "user", "content": "北京天气怎么样？"},
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"city\":\"北京\"}"
        }
      }]
    },
    {
      "role": "tool",
      "tool_call_id": "call_abc123",
      "content": "{\"temperature\":25,\"condition\":\"晴\",\"humidity\":40}"
    }
  ],
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "获取指定城市的天气信息",
      "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
      }
    }
  }]
}
```

**响应：**
```json
{
  "id": "resp_e5f6a7b8c9d0123456789012",
  "object": "response",
  "created_at": 1714838400,
  "model": "mimo-v2.5",
  "output": [
    {
      "type": "message",
      "id": "msg_f6a7b8c9d0e123456789012",
      "role": "assistant",
      "status": "completed",
      "content": [
        {
          "type": "output_text",
          "text": "北京今天天气晴朗，气温 25°C，湿度 40%，非常适合外出活动！"
        }
      ]
    }
  ],
  "usage": {
    "input_tokens": 180,
    "output_tokens": 35,
    "total_tokens": 215
  },
  "status": "completed"
}
```

### 7.5 带推理内容的流式请求

**请求：**
```json
{
  "model": "mimo-v2.5-pro",
  "input": "证明勾股定理",
  "stream": true
}
```

**响应事件序列：**
```
data: {"type":"response.created","response":{"id":"resp_xxx","object":"response","model":"mimo-v2.5-pro","status":"in_progress"}}

data: {"type":"response.in_progress","response":{"id":"resp_xxx","status":"in_progress"}}

data: {"type":"response.output_item.added","output_index":0,"item":{"type":"reasoning","id":"rs_xxx","summary":[]}}

data: {"type":"response.output_item.done","output_index":0,"item":{"type":"reasoning","id":"rs_xxx","summary":[{"type":"summary_text","text":"用户要求证明勾股定理，我需要给出一个经典的几何证明..."}]}}

data: {"type":"response.output_item.added","output_index":1,"item":{"type":"message","id":"msg_xxx","role":"assistant","status":"in_progress","content":[]}}

data: {"type":"response.content_part.added","output_index":1,"content_index":0,"part":{"type":"output_text","text":""}}

data: {"type":"response.output_text.delta","output_index":1,"content_index":0,"delta":"勾股定理的证明如下..."}

data: {"type":"response.output_text.delta","output_index":1,"content_index":0,"delta":"\n\n在一个直角三角形中..."}

... (更多 delta 事件) ...

data: {"type":"response.content_part.done","output_index":1,"content_index":0,"part":{"type":"output_text","text":"完整的证明文本..."}}

data: {"type":"response.output_item.done","output_index":1,"item":{"type":"message","id":"msg_xxx","role":"assistant","status":"completed","content":[{"type":"output_text","text":"完整的证明文本..."}]}}

data: {"type":"response.completed","response":{"id":"resp_xxx","object":"response","model":"mimo-v2.5-pro","status":"completed","output":[...]}}

data: [DONE]
```

---

## 8. 错误响应格式

### 8.1 通用错误格式

```json
{
  "error": {
    "type": "错误类型",
    "message": "错误描述信息"
  }
}
```

### 8.2 错误类型一览

| HTTP 状态码 | error.type | 说明 | 示例 |
|------------|-----------|------|------|
| 400 | `invalid_request` | 请求格式错误 | 无效 JSON、缺少必需字段 |
| 401 | `auth_error` | 认证失败 | 无效的代理认证 token |
| 413 | `invalid_request` | 请求体过大 | 超过 10MB 限制 |
| 500 | `server_error` | 代理内部错误 | 代理服务器异常 |
| 502 | `upstream_error` | 上游 API 错误 | 上游返回非 JSON 或不可达 |
| 504 | `timeout` | 上游超时 | 上游 API 响应超时 |

### 8.3 错误示例

**认证失败：**
```json
HTTP/1.1 401 Unauthorized

{
  "error": {
    "type": "auth_error",
    "message": "Invalid proxy token"
  }
}
```

**请求体过大：**
```json
HTTP/1.1 413 Request Entity Too Large

{
  "error": {
    "type": "invalid_request",
    "message": "Request body too large"
  }
}
```

**上游 API 错误：**
```json
HTTP/1.1 502 Bad Gateway

{
  "error": {
    "type": "upstream_error",
    "message": "Upstream returned unexpected response format"
  }
}
```

**上游 API 返回业务错误（透传）：**
```json
HTTP/1.1 429 Too Many Requests

{
  "error": {
    "type": "api_error",
    "message": "Rate limit exceeded",
    "code": "429"
  }
}
```

**流式传输中的错误事件：**
```
data: {"type":"error","error":{"type":"upstream_error","message":"Upstream returned 500"}}
```

```
data: {"type":"error","error":{"type":"timeout","message":"Upstream request timed out"}}
```

---

## 9. 与 Chat Completions API 的差异

### 9.1 核心差异总结

| 特性 | Chat Completions API | Responses API |
|------|---------------------|---------------|
| **端点** | `POST /v1/chat/completions` | `POST /v1/responses` |
| **输入** | `messages` 数组 | `input` 数组/字符串 |
| **系统指令** | messages 中的 system role | 顶层 `instructions` 字段 |
| **工具定义** | `{"type":"function","function":{...}}` | `{"type":"function","name":..., ...}` (扁平) |
| **工具调用** | assistant message 的 `tool_calls` 字段 | 独立的 `function_call` output item |
| **工具结果** | tool role message | `function_call_output` input item |
| **推理内容** | `reasoning_content` 字段 | `reasoning` output item |
| **流式事件** | `choices[0].delta` | 丰富的事件类型 |
| **响应 ID** | `chatcmpl-xxx` | `resp_xxx` |
| **对象标识** | `"object": "chat.completion"` | `"object": "response"` |
| **状态追踪** | 无 | `status` 字段 |
| **Usage 格式** | `prompt_tokens` / `completion_tokens` | `input_tokens` / `output_tokens` |

### 9.2 流式传输差异

**Chat Completions 流式：**
```
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":"你好"}}]}
data: [DONE]
```

**Responses API 流式：**
```
data: {"type":"response.created","response":{...}}
data: {"type":"response.in_progress","response":{...}}
data: {"type":"response.output_item.added",...}
data: {"type":"response.content_part.added",...}
data: {"type":"response.output_text.delta","delta":"你好",...}
data: {"type":"response.content_part.done",...}
data: {"type":"response.output_item.done",...}
data: {"type":"response.completed","response":{...}}
data: [DONE]
```

### 9.3 Responses API 的优势

1. **结构化输出**：每个输出项有明确的类型和 ID，便于追踪
2. **推理透明**：reasoning 内容作为独立 output item，不混在 message 中
3. **工具调用解耦**：function_call 是独立的 output item，与 message 分离
4. **多轮上下文**：支持 `previous_response_id` 自动管理上下文
5. **状态管理**：response 有明确的 status 状态机
6. **丰富的流式事件**：added/done/delta 三级事件，客户端可以精确追踪状态

### 9.4 代理的兼容性

本代理实现了 Responses API 的核心子集，包括：

- ✅ 基本文本输入/输出
- ✅ 多轮对话
- ✅ 工具调用（双向转换）
- ✅ 推理内容（reasoning_content）
- ✅ 流式传输（SSE）
- ✅ temperature、top_p、max_output_tokens 等参数
- ✅ system/developer instructions

暂未实现的特性：

- ❌ `previous_response_id`（自动上下文管理）
- ❌ `metadata` 透传
- ❌ `background` 异步模式
- ❌ `stream_options`（include_usage / include_obfuscation）
- ❌ GET/DELETE `/v1/responses/{response_id}`
- ❌ 图片/音频等多模态 input content parts
- ❌ `incomplete_details` 信息
- ❌ `service_tier` 信息

---

## 10. 辅助端点

### 10.1 健康检查

```
GET /health
```

**响应：**
```json
{"status": "ok"}
```

### 10.2 模型列表

```
GET /v1/models
Authorization: Bearer <API_KEY>
```

代理将此请求透传至上游 MiMo API，返回可用模型列表。

**响应示例：**
```json
{
  "data": [
    {"id": "mimo-v2.5-pro", "object": "model", ...},
    {"id": "mimo-v2.5", "object": "model", ...},
    {"id": "mimo-v2-pro", "object": "model", ...}
  ]
}
```

---

## 附录：配置参考

代理通过环境变量配置，也可在 `config.py` 中设置默认值：

| 环境变量 | 默认值 | 说明 |
|---------|-------|------|
| `MIMO_BASE_URL` | `https://token-plan-cn.xiaomimimo.com/v1` | 上游 MiMo API 地址 |
| `MIMO_API_KEY` | (硬编码在 config.py) | MiMo API 密钥 |
| `PROXY_HOST` | `127.0.0.1` | 代理监听地址 |
| `PROXY_PORT` | `8000` | 代理监听端口 |
| `PROXY_AUTH_TOKEN` | `None` | 代理认证 token（可选） |

**启动方式：**
```bash
# 使用环境变量
MIMO_API_KEY=your_key python proxy.py

# 或直接使用 config.py 默认值
python proxy.py
```

服务器启动后监听 `http://127.0.0.1:8000`，所有请求转发至 `https://token-plan-cn.xiaomimimo.com/v1`。
