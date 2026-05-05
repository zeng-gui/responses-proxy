# Responses-to-ChatCompletions Proxy

将 OpenAI Responses API 转换为 Chat Completions API 的轻量代理服务，支持多 provider 路由，使 Codex CLI 等工具能够对接 MiMo、DeepSeek、Qwen 等后端。

## 工作原理

```
Codex CLI              Proxy (FastAPI)              Upstream Providers
    |                       |                              |
    |-- Responses API ----->|                              |
    |                       |-- model="mimo-v2.5-pro" ---->| MiMo API
    |                       |-- model="deepseek-chat" ---->| DeepSeek API
    |                       |-- model="qwen3.6-plus" ----->| Qwen API
    |                       |<--- Chat Completions resp ---|
    |<-- Responses API -----|                              |
```

代理根据请求中的 `model` 字段自动路由到对应的上游 provider。

## 特性

- 多 Provider 路由（providers.json 配置）
- 流式 / 非流式响应
- 完整 tool call 生命周期（合并连续 function_call）
- 多模态输入（input_image → image_url）
- reasoning_content 自动注入（DeepSeek 兼容）
- 启动时 provider 连通性预检
- 请求体大小限制（10MB）、CORS、请求 ID

## 环境要求

- Python 3.10+
- Codex CLI

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 providers.json

```json
{
  "providers": {
    "mimo": {
      "base_url": "https://your-mimo-api/v1",
      "api_key": "your-mimo-key",
      "models": ["mimo-v2.5-pro", "mimo-v2.5"]
    },
    "deepseek": {
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "your-deepseek-key",
      "models": ["deepseek-v4-pro", "deepseek-v4-flash"]
    },
    "qwen": {
      "base_url": "https://coding.dashscope.aliyuncs.com/v1",
      "api_key": "your-qwen-key",
      "models": ["qwen3.6-plus", "glm-5", "kimi-k2.5"]
    }
  }
}
```

### 3. 配置 Codex CLI

编辑 `~/.codex/config.toml`（Windows: `%USERPROFILE%\.codex\config.toml`）：

```toml
model = "mimo-v2.5-pro"

[model_providers.proxy]
name = "proxy"
base_url = "http://127.0.0.1:8000/v1"
wire_api = "responses"
requires_openai_auth = false
```

编辑 `~/.codex/auth.json`（可留空，代理从 providers.json 读取密钥）：

```json
{
  "OPENAI_API_KEY": ""
}
```

### 4. 启动代理

```bash
python proxy.py
```

### 5. 使用 Codex

```bash
codex
```

切换模型：修改 `config.toml` 中的 `model` 字段，重启 Codex。

## providers.json 说明

| 字段 | 说明 |
|------|------|
| `base_url` | 上游 API 地址（必须，需含 `/v1`） |
| `api_key` | 上游 API 密钥（可选，缺省从 ~/.codex/auth.json 读取） |
| `models` | 该 provider 支持的模型列表（必须） |

`api_key` 优先级：providers.json 中的值 > 环境变量 `MIMO_API_KEY` > `~/.codex/auth.json`

## 环境变量（可选）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| PROXY_HOST | 代理监听地址 | 127.0.0.1 |
| PROXY_PORT | 代理监听端口 | 8000 |
| PROXY_AUTH_TOKEN | 代理访问令牌，未设置则关闭鉴权 | None |

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /v1/responses | Responses API 代理入口（按 model 自动路由） |
| GET  | /v1/models | 返回所有已配置的模型列表 |
| GET  | /health | 健康检查 |

## 关于 "Model metadata not found" 警告

Codex CLI 对非内置模型会显示此警告，属于正常现象，不影响功能。可通过在 `config.toml` 中为模型添加详细元数据来消除：

```toml
[model_providers.proxy.models.mimo-v2.5-pro]
slug = "mimo-v2.5-pro"
context_window = 1048576
supports_parallel_tool_calls = true
```

## 项目结构

```
.
├── config.py           # 配置加载 (providers.json + 环境变量)
├── proxy.py            # 代理主逻辑 (FastAPI、协议转换、多 provider 路由)
├── providers.json      # provider 配置 (不提交 git，含密钥)
├── requirements.txt    # Python 依赖
└── README.md           # 本文件
```

## 许可

本项目仅供内部使用。
