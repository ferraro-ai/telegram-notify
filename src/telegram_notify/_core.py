"""Synchronous, best-effort notifications using the standard library."""

from __future__ import annotations

import getpass
import json
import math
import os
import re
import sys
import warnings
from dataclasses import dataclass, field
from typing import Any
from urllib import error, request

_TOKEN_PATTERN = re.compile(r"[0-9]+:[A-Za-z0-9_-]+\Z")
_TOKEN_IN_TEXT = re.compile(r"\b[0-9]{5,}:[A-Za-z0-9_-]{20,}")
_default: Notifier | None = None
_notebook_shell: Any = None
_HOOK_ATTR = "_telegram_notify_error_hook"


class ConfigurationError(ValueError):
    """Required settings are missing or invalid."""


class TelegramError(RuntimeError):
    """Notification delivery failed. Messages never include the request URL."""


def _redact(text: str, token: str = "") -> str:
    if token:
        text = text.replace(token, "[REDACTED]")
    return _TOKEN_IN_TEXT.sub("[REDACTED]", text)


def _truncate(text: str, limit: int, *, utf16: bool = False) -> str:
    """Keep the ellipsis within the limit; UTF-16 is conservative for Telegram."""
    if not utf16:
        return text if len(text) <= limit else text[: limit - 1] + "…"
    encoded = text.encode("utf-16-le", errors="replace")
    if len(encoded) <= limit * 2:
        return text
    return encoded[: (limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def _report(message: str) -> None:
    # A broken stderr must not replace the training/job exception either.
    try:
        print(f"telegram-notify: {message}", file=sys.stderr)
    except Exception:
        pass


def _colab_secret(name: str) -> str | None:
    try:
        from google.colab import userdata

        value = userdata.get(name)
        return str(value).strip() if value is not None else None
    except Exception:
        # Not Colab, missing secret, denied access, or unavailable secret service.
        return None


def _prompt_token() -> str:
    reader = getpass.getpass
    # The standard reader may use /dev/tty or msvcrt rather than the IDE's
    # console, leaving Run/Debug waiting without a visible prompt. Notebook
    # kernels supply their own getpass reader and must keep using that reader.
    if getattr(reader, "__module__", "getpass") == "getpass":
        try:
            terminal = sys.stdin is not None and sys.stdin.isatty()
        except (AttributeError, OSError):
            terminal = False
        if not terminal:
            raise ConfigurationError(
                "Hidden token input requires a terminal. In PyCharm use the Terminal "
                "tool window, or set TELEGRAM_BOT_TOKEN in Run > Edit Configurations "
                "> Environment variables."
            )
    print("telegram-notify: TELEGRAM_BOT_TOKEN is missing; waiting for hidden input.", flush=True)
    # Refuse getpass's echoing fallback when hidden input is unavailable.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return reader("TELEGRAM_BOT_TOKEN: ").strip()


def _resolve(name: str, explicit: str | int | None, allow_prompt: bool) -> str:
    if explicit is not None:
        value = str(explicit).strip()
    else:
        value = _colab_secret(name) or os.environ.get(name, "").strip()
        if not value and allow_prompt:
            try:
                if name == "TELEGRAM_BOT_TOKEN":
                    value = _prompt_token()
                else:
                    value = input(f"{name}: ").strip()
            except (EOFError, OSError, getpass.GetPassWarning):
                raise ConfigurationError(
                    f"Cannot read {name}; set it in Colab Secrets or the environment."
                ) from None
    if not value:
        raise ConfigurationError(f"Missing {name}.")
    return value


def _validate_token(token: str) -> None:
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ConfigurationError("Invalid TELEGRAM_BOT_TOKEN format.")


def _request(token: str, method: str, payload: dict[str, Any], timeout: float) -> Any:
    req = request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read())
    except error.HTTPError as exc:
        # Telegram's error body is not needed and may contain reflected input.
        raise TelegramError(f"Telegram HTTP {exc.code}.") from None
    except (error.URLError, OSError):
        raise TelegramError("Network error or timeout contacting Telegram.") from None
    except (ValueError, UnicodeError):
        raise TelegramError("Invalid response from Telegram.") from None
    if not isinstance(data, dict) or data.get("ok") is not True:
        code = data.get("error_code") if isinstance(data, dict) else None
        suffix = f" (code {code})" if isinstance(code, int) else ""
        raise TelegramError(f"Telegram rejected the request{suffix}.")
    return data.get("result")


@dataclass(frozen=True)
class Notifier:
    """Independent client for services; use send(..., raise_on_error=True) for strict mode."""

    token: str = field(repr=False)
    chat_id: str | int
    timeout: float = 5.0

    def __post_init__(self) -> None:
        token = str(self.token).strip()
        chat_id = str(self.chat_id).strip()
        _validate_token(token)
        if not re.fullmatch(r"-?[0-9]+|@[A-Za-z0-9_]+", chat_id):
            raise ConfigurationError("Invalid TELEGRAM_CHAT_ID format.")
        if not chat_id.startswith("@") and int(chat_id) == 0:
            raise ConfigurationError("TELEGRAM_CHAT_ID cannot be zero.")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ConfigurationError("timeout must be a positive finite number.")
        object.__setattr__(self, "token", token)
        object.__setattr__(self, "chat_id", chat_id)

    def send(self, message: str, *, raise_on_error: bool = False) -> bool:
        """Send one plain-text message, truncate at 4096 units, return delivery status.

        No retries: a lost response may still mean Telegram received the message.
        """
        try:
            text = _truncate(_redact(str(message), self.token), 4096, utf16=True)
            if not text.strip():
                raise TelegramError("Cannot send an empty message.")
            _request(self.token, "sendMessage", {"chat_id": self.chat_id, "text": text}, self.timeout)
            return True
        except Exception as exc:
            # Never expose raw transport exceptions (they may include the bot URL).
            failure = exc if isinstance(exc, TelegramError) else TelegramError("Notification failed.")
            if raise_on_error:
                raise failure from None
            _report(str(failure))
            return False


def configure(
    *,
    token: str | None = None,
    chat_id: str | int | None = None,
    allow_prompt: bool = True,
    timeout: float = 5.0,
) -> Notifier:
    """Resolve each setting: explicit value > Colab Secrets > environment > input.

    Setup errors raise immediately; send failures are best-effort by default.
    Nothing is written to files or environment variables.
    """
    global _default
    resolved_token = _resolve("TELEGRAM_BOT_TOKEN", token, allow_prompt)
    _validate_token(resolved_token)
    resolved_chat = _resolve("TELEGRAM_CHAT_ID", chat_id, allow_prompt)
    client = Notifier(resolved_token, resolved_chat, timeout)
    _default = client
    return client


def get_chat_ids(
    *, token: str | None = None, allow_prompt: bool = True, timeout: float = 5.0
) -> dict[int, str]:
    """List known chat IDs and labels after /start; does not need a configured chat.

    Reads updates without confirming/consuming them. Works inside Colab too.
    Never chooses a recipient automatically; the caller selects their own chat.
    """
    resolved = _resolve("TELEGRAM_BOT_TOKEN", token, allow_prompt)
    _validate_token(resolved)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigurationError("timeout must be a positive finite number.")
    updates = _request(resolved, "getUpdates", {"timeout": 0, "limit": 100}, timeout)
    if not isinstance(updates, list):
        raise TelegramError("Invalid updates response from Telegram.")
    chats = {}
    for update in updates:
        if not isinstance(update, dict):
            continue
        for key in ("message", "channel_post", "my_chat_member"):
            event = update.get(key)
            chat = event.get("chat") if isinstance(event, dict) else None
            if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
                continue
            label = chat.get("title") or chat.get("username") or chat.get("first_name", "")
            chats[chat["id"]] = f"{chat.get('type', '')}: {label}"
    return chats


def _get_default() -> Notifier:
    if _default is None:
        raise ConfigurationError("Call configure() before using notifications.")
    return _default


def notify(message: str, *, raise_on_error: bool = False) -> bool:
    """Manual notification; never prompts. False on failure unless strict mode is set."""
    try:
        client = _get_default()
    except ConfigurationError as exc:
        if raise_on_error:
            raise
        _report(str(exc))
        return False
    return client.send(message, raise_on_error=raise_on_error)


@dataclass
class _NotebookHook:
    context: str
    max_error_chars: int

    def callback(self, result: Any) -> None:
        try:
            exc = getattr(result, "error_in_exec", None)
            if exc is None:
                exc = getattr(result, "error_before_exec", None)
            if exc is None:
                return
            client = _get_default()
            try:
                description = " ".join(str(exc).split())
            except Exception:
                description = "<description unavailable>"
            detail = _truncate(
                _redact(f"{type(exc).__name__}: {description}", client.token),
                self.max_error_chars,
            )
            count = getattr(result, "execution_count", None)
            cell = f"\nCell In[{count}]" if count is not None else ""
            context = _truncate(_redact(self.context, client.token), 256)
            notify(f"❌ {context}{cell}\n\n{detail}")
        except Exception:
            _report("Could not report the notebook error.")
        # IPython owns the original exception: never raise, clear, or replace it here.


def enable_notebook_errors(context: str = "Notebook", *, max_error_chars: int = 800) -> None:
    """Subscribe to cell failures, including syntax errors and interruptions.

    Repeated calls replace the hook. Does not notify on successful cells.
    """
    global _notebook_shell
    _get_default()
    if isinstance(max_error_chars, bool) or not isinstance(max_error_chars, int) or max_error_chars < 1:
        raise ValueError("max_error_chars must be a positive integer.")
    try:
        from IPython import get_ipython
    except ImportError:
        raise RuntimeError("IPython is required for notebook error notifications.") from None
    shell = get_ipython()
    if shell is None:
        raise RuntimeError("Run enable_notebook_errors() inside IPython/Jupyter/Colab.")
    hook = _NotebookHook(str(context), max_error_chars)
    disable_notebook_errors()
    previous = getattr(shell, _HOOK_ATTR, None)
    if previous is not None:
        shell.events.unregister("post_run_cell", previous.callback)
    shell.events.register("post_run_cell", hook.callback)
    setattr(shell, _HOOK_ATTR, hook)
    _notebook_shell = shell


def disable_notebook_errors() -> None:
    """Remove this package's notebook callback; safe to call repeatedly."""
    global _notebook_shell
    if _notebook_shell is None:
        return
    hook = getattr(_notebook_shell, _HOOK_ATTR, None)
    if hook is not None:
        _notebook_shell.events.unregister("post_run_cell", hook.callback)
        delattr(_notebook_shell, _HOOK_ATTR)
    _notebook_shell = None
