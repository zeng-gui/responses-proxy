import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass

_config_log = logging.getLogger("config")


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str


def _read_codex_api_key() -> str:
    """从 Codex CLI 的 auth.json 读取 OPENAI_API_KEY。"""
    codex_dir = Path.home() / ".codex"
    auth_file = codex_dir / "auth.json"
    if auth_file.exists():
        try:
            data = json.loads(auth_file.read_text(encoding="utf-8"))
            key = data.get("OPENAI_API_KEY", "")
            if key:
                _config_log.info("已从 %s 读取 API Key", auth_file)
                return key
        except (json.JSONDecodeError, OSError) as e:
            _config_log.warning("读取 %s 失败: %s", auth_file, e)
    return ""


def _load_providers_from_file() -> dict[str, ProviderConfig]:
    """从 providers.json 加载 provider 配置，返回 {model_name: ProviderConfig} 映射。"""
    providers_file = Path(__file__).parent / "providers.json"
    if not providers_file.exists():
        return {}

    try:
        raw = json.loads(providers_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _config_log.error("读取 providers.json 失败: %s", e)
        return {}

    fallback_key = os.environ.get("MIMO_API_KEY", "") or _read_codex_api_key()
    result: dict[str, ProviderConfig] = {}

    for name, cfg in raw.get("providers", {}).items():
        base_url = cfg.get("base_url", "").rstrip("/")
        api_key = cfg.get("api_key", "") or fallback_key
        models = cfg.get("models", [])

        if not base_url:
            _config_log.warning("Provider '%s' 缺少 base_url，跳过", name)
            continue
        if not models:
            _config_log.warning("Provider '%s' 没有定义 models，跳过", name)
            continue

        for model_name in models:
            if model_name in result:
                _config_log.warning("模型 '%s' 重复定义，后者覆盖前者", model_name)
            result[model_name] = ProviderConfig(name=name, base_url=base_url, api_key=api_key)

    return result


def _load_providers_from_env() -> dict[str, ProviderConfig]:
    """从环境变量加载单个 provider（向后兼容）。"""
    api_key = os.environ.get("MIMO_API_KEY", "") or _read_codex_api_key()
    base_url = os.environ.get("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1").rstrip("/")

    if not api_key:
        return {}

    return {
        "mimo-v2.5-pro": ProviderConfig(name="mimo", base_url=base_url, api_key=api_key),
        "mimo-v2.5": ProviderConfig(name="mimo", base_url=base_url, api_key=api_key),
    }


# ── 加载 provider 配置 ──────────────────────────────────────────────────────
PROVIDERS = _load_providers_from_file()

if not PROVIDERS:
    _config_log.info("providers.json 不存在或为空，尝试从环境变量加载")
    PROVIDERS = _load_providers_from_env()

if not PROVIDERS:
    raise SystemExit(
        "没有可用的 provider！请通过以下方式之一配置:\n"
        "  1. 创建 providers.json 文件\n"
        "  2. 设置 MIMO_API_KEY 环境变量"
    )

_config_log.info("已加载 %d 个模型: %s", len(PROVIDERS), ", ".join(PROVIDERS.keys()))

# ── 代理服务配置 ────────────────────────────────────────────────────────────
PROXY_HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
try:
    PROXY_PORT = int(os.environ.get("PROXY_PORT", "8000"))
    if not (1 <= PROXY_PORT <= 65535):
        raise ValueError(f"Port {PROXY_PORT} out of range")
except ValueError as e:
    raise SystemExit(f"Invalid PROXY_PORT: {e}")

PROXY_AUTH_TOKEN = os.environ.get("PROXY_AUTH_TOKEN", None)
