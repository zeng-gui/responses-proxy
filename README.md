# Responses-to-ChatCompletions Proxy

将 OpenAI Responses API 转换为 Chat Completions API 的轻量代理服务，使 Codex CLI 等工具能够对接 MiMo 等仅支持 Chat Completions 的后端。

## 工作原理

`
Codex CLI              Proxy (FastAPI)              MiMo / Upstream
    |                       |                             |
    |-- Responses API ----->|                             |
    |                       |-- Chat Completions API ---->|
    |                       |<--- Chat Completions resp --|
    |<-- Responses API -----|                             |
`

代理在两个 API 格式之间进行双向转换，保留:

- 流式 (SSE) 与非流式响应
- 工具调用 (function_call) 完整生命周期
- 推理内容 (reasoning_content)
- 会话历史与系统指令

## 环境要求

- Python 3.10+

## 快速开始

`ash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
export MIMO_API_KEY="your-api-key-here"
export MIMO_BASE_URL="https://your-upstream/v1"  # 可选
export PROXY_HOST="127.0.0.1"                     # 可选
export PROXY_PORT="8000"                          # 可选
export PROXY_AUTH_TOKEN="your-proxy-token"         # 可选，启用代理鉴权

# 3. 启动服务
python proxy.py
`

或使用 uvicorn:

`ash
uvicorn proxy:app --host 127.0.0.1 --port 8000
`

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| MIMO_API_KEY | 上游 API 密钥 | *(必须配置)* |
| MIMO_BASE_URL | 上游 API 地址 | https://token-plan-cn.xiaomimimo.com/v1 |
| PROXY_HOST | 代理监听地址 | 127.0.0.1 |
| PROXY_PORT | 代理监听端口 | 8000 |
| PROXY_AUTH_TOKEN | 代理访问令牌，未设置则关闭鉴权 | None |

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /v1/responses | Responses API 代理入口 (流式/非流式) |
| GET  | /v1/models | 透传上游模型列表 |
| GET  | /health | 健康检查 |

## 与 Codex CLI 集成

在 Codex CLI 配置中将 base URL 指向本代理:

`ash
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"
export OPENAI_API_KEY="your-proxy-token-or-dummy"
codex
`

## 项目结构

`
.
├── config.py           # 配置项与环境变量读取
├── proxy.py            # 代理主逻辑 (FastAPI 应用、协议转换、流式处理)
├── requirements.txt    # Python 依赖
├── REVIEW.md           # 项目审查报告
└── README.md           # 本文件
`

## 许可

本项目仅供内部使用。
