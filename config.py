import os

# 目标 API 配置 - 从环境变量读取，避免硬编码
TARGET_BASE_URL = os.environ.get("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
API_KEY = os.environ.get("MIMO_API_KEY", "tp-crn4wehvqx4zh0xz0k4svjw6tcv38b7kj6y3tcq5k8w96300")

# 代理服务配置
PROXY_HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
try:
    PROXY_PORT = int(os.environ.get("PROXY_PORT", "8000"))
    if not (1 <= PROXY_PORT <= 65535):
        raise ValueError(f"Port {PROXY_PORT} out of range")
except ValueError as e:
    raise SystemExit(f"Invalid PROXY_PORT: {e}")

# 代理认证（可选）- 如果设置，客户端需要提供此 token
PROXY_AUTH_TOKEN = os.environ.get("PROXY_AUTH_TOKEN", None)
