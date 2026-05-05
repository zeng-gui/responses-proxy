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
- **Web 配置管理界面**（浏览器可视化配置 Provider，无需手动编辑 JSON）
- **配置热重载**（通过 Web UI 修改后无需重启进程）

## 环境要求

- Python 3.10+

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 初始化配置

首次使用请复制示例配置并填入自己的 API Key：

```bash
cp providers.json.example providers.json
```

然后编辑 `providers.json`，将 `api_key` 替换为你的实际密钥。

> 💡 也可以直接启动代理，通过 Web 界面完成配置（见下方说明）。

### 3. 启动代理

```bash
python proxy.py
```

启动后终端会显示：

```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### 4. 打开 Web 配置管理界面

浏览器访问 **http://127.0.0.1:8000** 即可打开配置管理面板。

### 5. 配置 Codex CLI

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

### 6. 使用 Codex

```bash
codex
```

切换模型：修改 `config.toml` 中的 `model` 字段，重启 Codex。

---

## Web 配置管理界面

启动代理后，浏览器访问 **http://127.0.0.1:8000** 打开深色主题的配置管理面板。

### 界面功能

| 功能 | 操作方式 |
|------|----------|
| 添加 Provider | 点击底部「+ 添加 Provider」卡片 |
| 编辑 Provider | 直接修改卡片中的字段 |
| 删除 Provider | 点击卡片右上角「删除」按钮 |
| 管理模型列表 | 输入框输入模型名后按回车添加，点击 × 删除 |
| 查看 API Key | 点击眼睛图标切换明文/掩码显示 |
| 修改 API Key | 点击输入框直接编辑，保存后生效 |
| 测试连通性 | 点击「测试」按钮，自动 ping 上游 `/v1/models` |
| 保存配置 | 点击右上角「保存配置」，自动热重载无需重启 |

### 全局设置

顶部工具栏可配置：
- **Host** — 代理监听地址（默认 127.0.0.1）
- **Port** — 代理监听端口（默认 8000）

### 工作流程

```
浏览器打开 http://127.0.0.1:8000
        |
        v
  查看/编辑 Provider 配置
        |
        v
  点击「测试」验证连通性
        |
        v
  点击「保存配置」→ 自动热重载
        |
        v
  代理立即生效，无需重启
```

---

## providers.json 说明

### 字段说明

| 字段 | 说明 |
|------|------|
| `base_url` | 上游 API 地址（必须，需含 `/v1`） |
| `api_key` | 上游 API 密钥（可选，缺省从 ~/.codex/auth.json 读取） |
| `models` | 该 provider 支持的模型列表（必须） |

### API Key 优先级

providers.json 中的值 > 环境变量 > ~/.codex/auth.json

### 示例

参考 `providers.json.example`，支持的 provider：

| Provider | Base URL | 模型 |
|----------|----------|------|
| MiMo | https://token-plan-cn.xiaomimimo.com/v1 | mimo-v2.5-pro, mimo-v2.5 |
| DeepSeek | https://api.deepseek.com/v1 | deepseek-v4-pro, deepseek-v4-flash |
| Qwen | https://coding.dashscope.aliyuncs.com/v1 | qwen3.6-plus, glm-5, kimi-k2.5 |

---

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
| GET  | / | Web 配置管理界面 |
| GET  | /api/config | 获取当前配置 |
| POST | /api/config | 保存配置并热重载 |
| POST | /api/config/test | 测试 provider 连通性 |
| POST | /api/reload | 热重载配置 |

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
├── proxy.py              # 代理主逻辑 (FastAPI、协议转换、多 provider 路由)
├── config.py             # 配置加载 (providers.json + 环境变量)
├── config_store.py       # 配置持久化 (providers.json 读写)
├── static/
│   └── index.html        # Web 配置管理界面
├── providers.json.example  # 配置示例文件（需复制为 providers.json 并填入密钥）
├── providers.json        # 实际配置 (不提交 git，含密钥)
├── requirements.txt      # Python 依赖
└── README.md             # 本文件
```

## 许可

本项目仅供内部使用。
