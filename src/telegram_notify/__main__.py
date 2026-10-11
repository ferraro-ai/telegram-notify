"""Setup helpers. Tokens are read securely, never accepted as command arguments."""

import argparse
import sys

from ._core import ConfigurationError, TelegramError, configure, get_chat_ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram notification setup")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("chat-id", "List chat IDs after sending /start to your bot"),
        ("test", "Send a test notification"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--no-input", action="store_true", help="Do not prompt for settings")
    args = parser.parse_args()
    try:
        if args.command == "test":
            client = configure(allow_prompt=not args.no_input)
            client.send("✅ Telegram notifications are working.", raise_on_error=True)
            print("Test notification sent.")
            return 0
        chats = get_chat_ids(allow_prompt=not args.no_input)
        if not chats:
            print("No chats found. Send /start to your bot, then run this command again.")
            return 1
        for chat_id, label in chats.items():
            # repr prevents names from injecting new terminal lines/controls.
            print(f"{chat_id}\t{label!r}")
        return 0
    except (ConfigurationError, TelegramError) as exc:
        print(f"telegram-notify: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
