"""配置持久化层 — 封装 providers.json 和 Codex config.toml 的读写逻辑。"""

import json
import os
import re
import logging
from pathlib import Path

_log = logging.getLogger("config_store")
_PROVIDERS_FILE = Path(__file__).parent / "providers.json"
_DEFAULT_CODEX_CONFIG = Path.home() / ".codex" / "config.toml"


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


def save_providers(providers_config: dict, global_config: dict | None = None,
                   codex_config_paths: list[str] | None = None) -> None:
    """保存 providers 部分到文件（保留其他顶层字段）。global_config 如非空则同时更新。"""
    current = read_raw_config()
    current["providers"] = providers_config
    if global_config is not None:
        current["global"] = global_config
    if codex_config_paths is not None:
        current["codex_config_paths"] = codex_config_paths
    write_config(current)


def get_config_path() -> str:
    """返回 providers.json 的绝对路径。"""
    return str(_PROVIDERS_FILE.resolve())


# ── Codex config.toml 多路径支持 ─────────────────────────────────────────────

def _expand_path(p: str) -> Path:
    """展开 ~ 和环境变量。"""
    return Path(os.path.expandvars(p)).expanduser().resolve()


def get_codex_config_paths() -> list[Path]:
    """获取所有需要更新的 config.toml 路径。"""
    raw = read_raw_config()
    paths = raw.get("codex_config_paths", [])

    if not paths:
        return [_DEFAULT_CODEX_CONFIG] if _DEFAULT_CODEX_CONFIG.exists() else []

    result = []
    for p in paths:
        expanded = _expand_path(p)
        if expanded.exists():
            result.append(expanded)
        else:
            _log.warning("config.toml 路径不存在: %s", expanded)
    return result


# ── 单文件操作（内部辅助函数）────────────────────────────────────────────────

def _read_model_from_file(config_file: Path) -> str:
    """从单个 config.toml 读取 model 字段。"""
    try:
        text = config_file.read_text(encoding="utf-8")
        m = re.search(r'^model\s*=\s*"([^"]*)"', text, re.MULTILINE)
        return m.group(1) if m else ""
    except OSError as e:
        _log.warning("读取 %s 失败: %s", config_file, e)
        return ""


def _write_model_to_file(config_file: Path, model: str) -> None:
    """向单个 config.toml 写入 model 字段并确保模型元数据存在。"""
    try:
        text = config_file.read_text(encoding="utf-8")
        new_text, n = re.subn(
            r'^(model\s*=\s*)"[^"]*"',
            f'\\1"{model}"',
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if n == 0:
            new_text = f'model = "{model}"\n' + text
        # 确保模型元数据段存在（完整字段，Codex CLI 需要）
        meta_section = f'[model_providers.proxy.models.{model}]'
        if meta_section not in new_text:
            new_text = new_text.rstrip('\n') + f'\n\n{meta_section}\n'
            new_text += f'slug = "{model}"\n'
            new_text += f'display_name = "{model}"\n'
            new_text += f'description = "{model} model via proxy"\n'
            new_text += 'default_reasoning_level = "high"\n'
            new_text += 'supported_reasoning_levels = ["low", "medium", "high", "xhigh"]\n'
            new_text += 'shell_type = "shell_command"\n'
            new_text += 'apply_patch_tool_type = "freeform"\n'
            new_text += 'context_window = 1048576\n'
            new_text += 'max_context_window = 1048576\n'
            new_text += 'effective_context_window_percent = 95\n'
            new_text += 'supports_parallel_tool_calls = true\n'
            new_text += 'supports_search_tool = false\n'
        config_file.write_text(new_text, encoding="utf-8")
        _log.info("%s model 已更新为: %s", config_file, model)
    except OSError as e:
        _log.error("写入 %s 失败: %s", config_file, e)
        raise


def _write_base_url_to_file(config_file: Path, host: str, port: int) -> None:
    """向单个 config.toml 写入 base_url 字段。"""
    try:
        base_url = f"http://{host}:{port}/v1"
        text = config_file.read_text(encoding="utf-8")
        new_text, n = re.subn(
            r'^(base_url\s*=\s*)"[^"]*"',
            f'\\1"{base_url}"',
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if n > 0:
            config_file.write_text(new_text, encoding="utf-8")
            _log.info("%s base_url 已更新为: %s", config_file, base_url)
    except OSError as e:
        _log.error("写入 %s base_url 失败: %s", config_file, e)


# ── 公开 API（遍历所有配置路径）─────────────────────────────────────────────

def read_codex_model() -> str:
    """读取第一个可用的 config.toml 中的 model 字段。"""
    for config_file in get_codex_config_paths():
        model = _read_model_from_file(config_file)
        if model:
            return model
    return ""


def write_codex_model(model: str) -> None:
    """向所有配置的 config.toml 写入 model 字段。"""
    for config_file in get_codex_config_paths():
        _write_model_to_file(config_file, model)


def read_codex_base_url() -> str:
    """读取第一个可用的 config.toml 中的 base_url 字段。"""
    for config_file in get_codex_config_paths():
        try:
            text = config_file.read_text(encoding="utf-8")
            m = re.search(r'^base_url\s*=\s*"([^"]*)"', text, re.MULTILINE)
            if m:
                return m.group(1)
        except OSError:
            continue
    return ""


def write_codex_base_url(host: str, port: int) -> None:
    """更新所有配置的 config.toml 中的 base_url 字段。"""
    for config_file in get_codex_config_paths():
        _write_base_url_to_file(config_file, host, port)
