"""Telegram notifications; importing this module never prompts or sends requests."""

from ._core import (
    ConfigurationError,
    Notifier,
    TelegramError,
    configure,
    disable_notebook_errors,
    enable_notebook_errors,
    get_chat_ids,
    notify,
)

__all__ = [
    "ConfigurationError",
    "Notifier",
    "TelegramError",
    "configure",
    "disable_notebook_errors",
    "enable_notebook_errors",
    "get_chat_ids",
    "notify",
]
__version__ = "0.1.0"
