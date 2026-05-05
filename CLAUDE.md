# Responses-to-ChatCompletions Proxy

FastAPI proxy translating OpenAI Responses API to Chat Completions API (MiMo backend).

## Architecture
- proxy.py: Main logic (~635 lines) - protocol conversion, streaming, routes
- config.py: Config & env vars
- requirements.txt: fastapi, uvicorn, httpx
- Upstream: https://token-plan-cn.xiaomimimo.com/v1

## Key Commands
- Start: `python proxy.py`
- Health: `curl http://127.0.0.1:8000/health`
- Test: `curl -X POST http://127.0.0.1:8000/v1/responses -H 'Content-Type: application/json' -d '{"model":"mimo-v2.5","input":"hello"}'`

## Code Standards
- Python 3.10+ with type hints
- Chinese comments are OK (this is a CN team project)
- FastAPI lifespan pattern (not deprecated on_event)
- All changes must keep backward compatibility with existing Codex CLI clients
