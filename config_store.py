"""配置持久化层 — 封装 providers.json 和 Codex config.toml 的读写逻辑。"""

import json
import re
import logging
from pathlib import Path

_log = logging.getLogger("config_store")
_PROVIDERS_FILE = Path(__file__).parent / "providers.json"
_CODEX_CONFIG_FILE = Path.home() / ".codex" / "config.toml"


def read_raw_config() -> dict:
    """读取 providers.json 原始内容，不存在或解析失败时返回空结构。"""
    if not _PROVIDERS_FILE.exists():
        return {"providers": {}}
    try:
        return json.loads(_PROVIDERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _log.error("读取 providers.json 失败: %s", e)
        return {"providers": {}}


def write_config(config: dict) -> None:
    """将完整配置写入 providers.json。"""
    _PROVIDERS_FILE.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _log.info("配置已写入 %s", _PROVIDERS_FILE)


def save_providers(providers_config: dict, global_config: dict | None = None) -> None:
    """保存 providers 部分到文件（保留其他顶层字段）。global_config 如非空则同时更新。"""
    current = read_raw_config()
    current["providers"] = providers_config
    if global_config is not None:
        current["global"] = global_config
    write_config(current)


def get_config_path() -> str:
    """返回 providers.json 的绝对路径。"""
    return str(_PROVIDERS_FILE.resolve())


# ── Codex config.toml 读写 ─────────────────────────────────────────────────

def read_codex_model() -> str:
    """读取 ~/.codex/config.toml 中的 model 字段。"""
    if not _CODEX_CONFIG_FILE.exists():
        return ""
    try:
        text = _CODEX_CONFIG_FILE.read_text(encoding="utf-8")
        m = re.search(r'^model\s*=\s*"([^"]*)"', text, re.MULTILINE)
        return m.group(1) if m else ""
    except OSError as e:
        _log.warning("读取 config.toml 失败: %s", e)
        return ""


def write_codex_model(model: str) -> None:
    """写入 ~/.codex/config.toml 中的 model 字段，并确保模型元数据存在。"""
    if not _CODEX_CONFIG_FILE.exists():
        _log.warning("config.toml 不存在: %s", _CODEX_CONFIG_FILE)
        return
    try:
        text = _CODEX_CONFIG_FILE.read_text(encoding="utf-8")
        new_text, n = re.subn(
            r'^(model\s*=\s*)"[^"]*"',
            f'\\1"{model}"',
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if n == 0:
            new_text = f'model = "{model}"\n' + text
        # 确保模型元数据段存在
        meta_section = f'[model_providers.proxy.models.{model}]'
        if meta_section not in new_text:
            new_text = new_text.rstrip('\n') + f'\n\n{meta_section}\n'
            new_text += f'slug = "{model}"\n'
            new_text += 'context_window = 1048576\n'
            new_text += 'supports_parallel_tool_calls = true\n'
        _CODEX_CONFIG_FILE.write_text(new_text, encoding="utf-8")
        _log.info("config.toml model 已更新为: %s", model)
    except OSError as e:
        _log.error("写入 config.toml 失败: %s", e)
        raise


def read_codex_base_url() -> str:
    """读取 config.toml 中的 base_url 字段。"""
    if not _CODEX_CONFIG_FILE.exists():
        return ""
    try:
        text = _CODEX_CONFIG_FILE.read_text(encoding="utf-8")
        m = re.search(r'^base_url\s*=\s*"([^"]*)"', text, re.MULTILINE)
        return m.group(1) if m else ""
    except OSError:
        return ""


def write_codex_base_url(host: str, port: int) -> None:
    """更新 config.toml 中的 base_url 字段。"""
    if not _CODEX_CONFIG_FILE.exists():
        return
    try:
        base_url = f"http://{host}:{port}/v1"
        text = _CODEX_CONFIG_FILE.read_text(encoding="utf-8")
        new_text, n = re.subn(
            r'^(base_url\s*=\s*)"[^"]*"',
            f'\\1"{base_url}"',
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if n > 0:
            _CODEX_CONFIG_FILE.write_text(new_text, encoding="utf-8")
            _log.info("config.toml base_url 已更新为: %s", base_url)
    except OSError as e:
        _log.error("写入 config.toml base_url 失败: %s", e)
