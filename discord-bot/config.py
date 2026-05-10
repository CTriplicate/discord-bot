"""
Конфигурация бота — загружается из config.json.
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Конфиг не найден: {CONFIG_PATH}. "
            "Скопируйте config.json.example и заполните поля."
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_raw = _load_config()

TOKEN: str = _raw["token"]
ADMIN_ROLES: list[str] = _raw.get("admin_roles", [])
WARNING_ROLES: list[str] = _raw.get("warning", {}).get("roles_on_warn", [])
MAX_WARNINGS: int = _raw.get("warning", {}).get("max_warnings_before_ban", 0)
DATABASE: str = _raw.get("database", "bot.db")
EMBED_COLOR: int = int(_raw.get("embed_color", "#5865F2").lstrip("#"), 16)
LOCALE: str = _raw.get("locale", "ru")


def reload_config() -> dict:
    """Перезагрузка конфига из файла (горячая перезагрузка)."""
    global _raw, TOKEN, ADMIN_ROLES, WARNING_ROLES, MAX_WARNINGS, DATABASE, EMBED_COLOR, LOCALE
    _raw = _load_config()
    TOKEN = _raw["token"]
    ADMIN_ROLES = _raw.get("admin_roles", [])
    WARNING_ROLES = _raw.get("warning", {}).get("roles_on_warn", [])
    MAX_WARNINGS = _raw.get("warning", {}).get("max_warnings_before_ban", 0)
    DATABASE = _raw.get("database", "bot.db")
    EMBED_COLOR = int(_raw.get("embed_color", "#5865F2").lstrip("#"), 16)
    LOCALE = _raw.get("locale", "ru")
    return _raw
