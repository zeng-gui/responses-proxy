# Responses-to-ChatCompletions Proxy

FastAPI 代理，将 OpenAI Responses API 转换为 Chat Completions API。

## 架构
- proxy.py: 主逻辑（~733 行）— 协议转换、流式传输、工具调用、多模态
- config.py: 配置加载、providers.json 解析、ProviderConfig 数据类
- requirements.txt: fastapi, uvicorn, httpx, orjson
- providers.json: 多 provider 配置（已 gitignore，含 API 密钥）
- 支持: MiMo、DeepSeek、Qwen 及任何 OpenAI 兼容 provider

## 常用命令
- 启动: `python proxy.py`
- 健康检查: `curl http://127.0.0.1:8000/health`
- 模型列表: `curl http://127.0.0.1:8000/v1/models`
- 测试: `curl -X POST http://127.0.0.1:8000/v1/responses -H 'Content-Type: application/json' -d '{"model":"mimo-v2.5","input":"hello"}'`

## 代码规范
- Python 3.10+，使用类型注释
- 可使用中文注释
- 使用 FastAPI lifespan 模式（不要用已废弃的 on_event）
- 所有改动必须保持与现有 Codex CLI 客户端的向后兼容性

## 关键实现细节
- 多 provider 路由: 通过 config.py 中的 PROVIDERS 字典按 model 名解析
- reasoning_content: 所有 assistant 消息自动注入，兼容 DeepSeek
- 连续 function_call: 合并到单条 assistant 消息的 tool_calls 数组
- 多模态: input_image → image_url 转换，通过 _convert_content_parts() 实现
- 启动预检: 对每个 provider 的 base_url 做连通性检查
