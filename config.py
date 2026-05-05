import os
import logging
from dataclasses import dataclass

from config_store import read_raw_config

_config_log = logging.getLogger("config")


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str


def _read_codex_api_key() -> str:
    """从 Codex CLI 的 auth.json 读取 OPENAI_API_KEY。"""
    from pathlib import Path
    codex_dir = Path.home() / ".codex"
    auth_file = codex_dir / "auth.json"
    if auth_file.exists():
        try:
            import json
            data = json.loads(auth_file.read_text(encoding="utf-8"))
            key = data.get("OPENAI_API_KEY", "")
            if key:
                _config_log.info("已从 %s 读取 API Key", auth_file)
                return key
        except Exception as e:
            _config_log.warning("读取 %s 失败: %s", auth_file, e)
    return ""


def _build_providers(raw: dict) -> dict[str, ProviderConfig]:
    """从原始配置字典构建 {model_name: ProviderConfig} 映射。"""
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


def _do_load() -> dict[str, ProviderConfig]:
    """执行一次完整的 provider 加载。"""
    providers = _build_providers(read_raw_config())
    if not providers:
        _config_log.info("providers.json 不存在或为空，尝试从环境变量加载")
        providers = _load_providers_from_env()
    return providers


# ── 全局 provider 配置（可通过 reload_providers() 热更新） ──────────────────
PROVIDERS: dict[str, ProviderConfig] = _do_load()

if not PROVIDERS:
    _config_log.warning("没有可用的 provider，请通过 Web UI 或 providers.json 配置")

_config_log.info("已加载 %d 个模型: %s", len(PROVIDERS), ", ".join(PROVIDERS.keys()) if PROVIDERS else "(无)")


def reload_providers() -> int:
    """热重载 providers.json，原地更新 PROVIDERS 字典，返回模型数量。"""
    new_providers = _do_load()
    PROVIDERS.clear()
    PROVIDERS.update(new_providers)
    _config_log.info("配置已重载，%d 个模型: %s", len(PROVIDERS), ", ".join(PROVIDERS.keys()))
    return len(PROVIDERS)


# ── 代理服务配置（providers.json > 环境变量 > 默认值） ──────────────────────
_global_cfg = read_raw_config().get("global", {})
PROXY_HOST = os.environ.get("PROXY_HOST") or _global_cfg.get("host", "127.0.0.1")
try:
    _port_raw = os.environ.get("PROXY_PORT") or _global_cfg.get("port", "8000")
    PROXY_PORT = int(_port_raw)
    if not (1 <= PROXY_PORT <= 65535):
        raise ValueError(f"Port {PROXY_PORT} out of range")
except ValueError as e:
    raise SystemExit(f"Invalid PROXY_PORT: {e}")

PROXY_AUTH_TOKEN = os.environ.get("PROXY_AUTH_TOKEN", None)
