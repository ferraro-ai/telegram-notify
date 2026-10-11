import contextlib
import importlib.util
import io
import json
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from telegram_notify import (
    ConfigurationError,
    Notifier,
    TelegramError,
    configure,
    disable_notebook_errors,
    enable_notebook_errors,
    get_chat_ids,
    notify,
)
from telegram_notify import _core
from telegram_notify.__main__ import main

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ_secret"


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.saved = _core._default
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.secrets = patch.object(_core, "_colab_secret", return_value=None)
        self.secret = self.secrets.start()

    def tearDown(self):
        self.secrets.stop()
        self.env.stop()
        _core._default = self.saved

    def test_each_value_resolves_independently_and_secrets_win(self):
        self.secret.side_effect = lambda name: TOKEN if name == "TELEGRAM_BOT_TOKEN" else None
        os.environ.update(TELEGRAM_BOT_TOKEN="999:env_token", TELEGRAM_CHAT_ID="42")
        with patch("getpass.getpass", side_effect=AssertionError("Unexpected prompt")):
            client = configure(allow_prompt=False)
        self.assertEqual(client.token, TOKEN)
        self.assertEqual(client.chat_id, "42")

    def test_explicit_values_skip_all_secret_lookups(self):
        client = configure(token=TOKEN, chat_id=-123, allow_prompt=False)
        self.secret.assert_not_called()
        self.assertEqual(client.chat_id, "-123")

    def test_environment_fallback_then_hidden_and_plain_input(self):
        with patch("getpass.getpass", return_value=TOKEN) as hidden, patch(
            "builtins.input", return_value="42"
        ) as plain:
            client = configure()
        hidden.assert_called_once()
        plain.assert_called_once()
        self.assertEqual(client.chat_id, "42")
        os.environ["TELEGRAM_CHAT_ID"] = "43"
        with patch("getpass.getpass", return_value=TOKEN), patch(
            "builtins.input", side_effect=AssertionError("Unexpected prompt")
        ):
            self.assertEqual(configure().chat_id, "43")
        self.assertNotIn("TELEGRAM_BOT_TOKEN", os.environ)

    def test_no_input_never_prompts_and_failed_setup_keeps_previous_client(self):
        previous = configure(token=TOKEN, chat_id="42")
        with patch("getpass.getpass", side_effect=AssertionError("Unexpected prompt")), patch(
            "builtins.input", side_effect=AssertionError("Unexpected prompt")
        ):
            with self.assertRaises(ConfigurationError):
                configure(allow_prompt=False)
        self.assertIs(_core._default, previous)

    def test_echoing_getpass_fallback_is_refused(self):
        import getpass
        import warnings

        def unavailable(*args):
            warnings.warn("Cannot hide input", getpass.GetPassWarning)
            self.fail("Must stop before echoing the secret")

        with patch("getpass.getpass", side_effect=unavailable):
            with self.assertRaises(ConfigurationError):
                configure()

    def test_ide_console_without_tty_never_enters_native_getpass(self):
        hidden = Mock(side_effect=AssertionError("Native getpass would hang"))
        hidden.__module__ = "getpass"
        stdin = Mock()
        stdin.isatty.return_value = False
        with patch("getpass.getpass", hidden), patch.object(sys, "stdin", stdin), patch(
            "builtins.input", side_effect=AssertionError("Must not echo the secret")
        ):
            with self.assertRaisesRegex(ConfigurationError, "PyCharm.*Terminal"):
                configure()
        hidden.assert_not_called()

    def test_terminal_getpass_shows_notice_and_reads_token_securely(self):
        hidden = Mock(return_value=TOKEN)
        hidden.__module__ = "getpass"
        stdin = Mock()
        stdin.isatty.return_value = True
        with patch("getpass.getpass", hidden), patch.object(sys, "stdin", stdin), patch(
            "builtins.input", return_value="42"
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            client = configure()
        self.assertEqual(client.token, TOKEN)
        hidden.assert_called_once_with("TELEGRAM_BOT_TOKEN: ")
        self.assertIn("waiting for hidden input", stdout.getvalue())
        self.assertNotIn(TOKEN, stdout.getvalue())

    def test_notebook_password_reader_works_without_tty(self):
        hidden = Mock(return_value=TOKEN)
        hidden.__module__ = "ipykernel.kernelbase"
        stdin = Mock()
        stdin.isatty.return_value = False
        with patch("getpass.getpass", hidden), patch.object(sys, "stdin", stdin), patch(
            "builtins.input", return_value="42"
        ), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(configure().token, TOKEN)
        hidden.assert_called_once()

    def test_env_token_in_ide_console_skips_password_prompt(self):
        os.environ.update(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="42")
        stdin = Mock()
        stdin.isatty.return_value = False
        with patch.object(sys, "stdin", stdin), patch(
            "getpass.getpass", side_effect=AssertionError("Unexpected prompt")
        ):
            self.assertEqual(configure().token, TOKEN)

    def test_colab_missing_or_denied_secret_falls_back(self):
        userdata = types.ModuleType("google.colab.userdata")
        userdata.get = Mock(side_effect=RuntimeError("Access denied"))
        colab = types.ModuleType("google.colab")
        colab.userdata = userdata
        with patch.dict(sys.modules, {"google.colab": colab, "google.colab.userdata": userdata}):
            # Exercise the real loader, not the setup mock.
            self.secrets.stop()
            try:
                os.environ.update(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="42")
                self.assertEqual(configure(allow_prompt=False).token, TOKEN)
                self.assertEqual(userdata.get.call_count, 2)
                userdata.get.side_effect = None
                userdata.get.return_value = TOKEN
                self.assertEqual(_core._colab_secret("TELEGRAM_BOT_TOKEN"), TOKEN)
            finally:
                self.secret = self.secrets.start()

    def test_validation_and_repr_do_not_expose_token(self):
        self.assertNotIn(TOKEN, repr(Notifier(TOKEN, 42)))
        for token, chat_id, timeout in [("bad/token", 42, 5), (TOKEN, 0, 5), (TOKEN, "bad chat", 5),
                                       (TOKEN, 42, 0), (TOKEN, 42, float("nan"))]:
            with self.assertRaises(ConfigurationError):
                Notifier(token, chat_id, timeout)

    def test_notify_never_initializes_or_prompts(self):
        _core._default = None
        with patch("getpass.getpass", side_effect=AssertionError("Unexpected prompt")), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(notify("done"))
        with self.assertRaises(ConfigurationError):
            notify("done", raise_on_error=True)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.client = Notifier(TOKEN, 42, timeout=2.5)

    def test_plain_text_payload_redaction_and_timeout(self):
        response = io.BytesIO(json.dumps({"ok": True, "result": {"message_id": 1}}).encode())
        with patch.object(_core.request, "urlopen", return_value=response) as opened:
            self.assertTrue(self.client.send(f"<hello> _world_ {TOKEN}"))
        req = opened.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(payload, {"chat_id": "42", "text": "<hello> _world_ [REDACTED]"})
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(opened.call_args.kwargs["timeout"], 2.5)

    def test_long_unicode_message_stays_within_telegram_limit(self):
        with patch.object(_core, "_request") as send:
            self.assertTrue(self.client.send("😀" * 5000))
        text = send.call_args.args[2]["text"]
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 4096)
        self.assertTrue(text.endswith("…"))

    def test_transport_failure_is_soft_and_strict_error_hides_url(self):
        for failure in [URLError(f"https://api.telegram.org/bot{TOKEN}/sendMessage"),
                        TimeoutError("timeout"),
                        HTTPError(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 429, "limited", {}, None)]:
            with self.subTest(failure=type(failure).__name__), patch.object(
                _core.request, "urlopen", side_effect=failure
            ) as opened, contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertFalse(self.client.send("done"))
                self.assertEqual(opened.call_count, 1)  # no duplicate-causing retries
                self.assertNotIn(TOKEN, stderr.getvalue())
                with self.assertRaises(TelegramError) as caught:
                    self.client.send("done", raise_on_error=True)
                self.assertNotIn(TOKEN, str(caught.exception))

    def test_bad_or_rejected_api_response(self):
        for body in [b"not-json", b"[]", b'{"ok": false, "error_code": 403}', b'{}']:
            with self.subTest(body=body), patch.object(_core.request, "urlopen", return_value=io.BytesIO(body)):
                with self.assertRaises(TelegramError):
                    self.client.send("done", raise_on_error=True)

    def test_empty_message_fails_without_network(self):
        with patch.object(_core, "_request") as send, contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.client.send("  "))
        send.assert_not_called()


class CLITests(unittest.TestCase):
    def test_chat_discovery_needs_only_token_and_never_consumes_updates(self):
        updates = [{"message": {"chat": {"id": 42, "type": "private", "first_name": "Arthur"}}},
                   {"message": {"chat": {"id": 42, "type": "private", "first_name": "Arthur"}}},
                   {"my_chat_member": {"chat": {"id": -7, "type": "group", "title": "Dev"}}}]
        with patch.object(sys, "argv", ["telegram-notify", "chat-id", "--no-input"]), patch(
            "telegram_notify._core._resolve", return_value=TOKEN
        ) as resolve, patch("telegram_notify._core._request", return_value=updates) as req, contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(), 0)
        resolve.assert_called_once_with("TELEGRAM_BOT_TOKEN", None, False)
        self.assertNotIn("offset", req.call_args.args[2])
        self.assertEqual(len(stdout.getvalue().splitlines()), 2)
        self.assertNotIn(TOKEN, stdout.getvalue())

    def test_no_updates_explains_start_and_returns_failure(self):
        with patch.object(sys, "argv", ["telegram-notify", "chat-id"]), patch(
            "telegram_notify._core._resolve", return_value=TOKEN
        ), patch("telegram_notify._core._request", return_value=[]), contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(), 1)
        self.assertIn("/start", stdout.getvalue())

    def test_notebook_chat_discovery_uses_secrets_without_chat_configuration(self):
        updates = [{"message": {"chat": {"id": 42, "type": "private", "first_name": "Артур"}}}]
        with patch.object(_core, "_colab_secret", return_value=TOKEN) as secret, patch.object(
            _core, "_request", return_value=updates
        ), patch("getpass.getpass", side_effect=AssertionError("Unexpected prompt")):
            self.assertEqual(get_chat_ids(allow_prompt=False), {42: "private: Артур"})
        secret.assert_called_once_with("TELEGRAM_BOT_TOKEN")


@unittest.skipUnless(importlib.util.find_spec("IPython"), "IPython integration extra not installed")
class NotebookIntegrationTests(unittest.TestCase):
    def setUp(self):
        from IPython.core.interactiveshell import InteractiveShell

        self.saved = _core._default
        self.shell = InteractiveShell.instance()
        self.ip = patch("IPython.get_ipython", return_value=self.shell)
        self.ip.start()
        configure(token=TOKEN, chat_id=42)
        self.transport = patch.object(_core, "_request", return_value={"message_id": 1})
        self.send = self.transport.start()

    def tearDown(self):
        disable_notebook_errors()
        self.transport.stop()
        self.ip.stop()
        _core._default = self.saved

    def run_cell(self, code):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.shell.run_cell(code, store_history=True)

    def last_text(self):
        return self.send.call_args.args[2]["text"]

    def test_runtime_error_keeps_identity_and_successful_cells_do_not_notify(self):
        enable_notebook_errors("Aiva training")
        self.assertTrue(self.run_cell("1 + 1").success)
        self.send.assert_not_called()
        original = RuntimeError("CUDA out of memory " + "x" * 1500)
        self.shell.user_ns["_notify_test_error"] = original
        result = self.run_cell("raise _notify_test_error")
        self.assertIs(result.error_in_exec, original)
        self.assertFalse(result.success)
        self.assertEqual(self.send.call_count, 1)
        text = self.last_text()
        self.assertIn("Aiva training", text)
        self.assertIn(f"In[{result.execution_count}]", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(text.split("\n\n", 1)[1]), 800)
        self.assertTrue(text.endswith("…"))

    def test_syntax_errors_are_notified(self):
        enable_notebook_errors()
        result = self.run_cell("if :")
        self.assertIsInstance(result.error_before_exec, SyntaxError)
        self.assertFalse(result.success)
        self.assertIn("SyntaxError", self.last_text())

    def test_send_failure_never_replaces_original_exception(self):
        enable_notebook_errors()
        self.send.side_effect = TelegramError("Network error")
        original = ValueError("original error")
        self.shell.user_ns["_notify_test_error"] = original
        result = self.run_cell("raise _notify_test_error")
        self.assertIs(result.error_in_exec, original)
        self.assertFalse(result.success)

    def test_repeated_enable_and_disable_do_not_duplicate(self):
        enable_notebook_errors("old context")
        enable_notebook_errors("new context")
        self.run_cell("raise ValueError('oops')")
        self.assertEqual(self.send.call_count, 1)
        self.assertIn("new context", self.last_text())
        disable_notebook_errors()
        disable_notebook_errors()
        self.run_cell("raise ValueError('oops again')")
        self.assertEqual(self.send.call_count, 1)

    def test_interrupt_and_secret_in_diagnostic(self):
        enable_notebook_errors(TOKEN, max_error_chars=100)
        self.shell.user_ns["_notify_test_token"] = TOKEN
        result = self.run_cell("raise KeyboardInterrupt(_notify_test_token)")
        self.assertIsInstance(result.error_in_exec, KeyboardInterrupt)
        self.assertIn("KeyboardInterrupt", self.last_text())
        self.assertNotIn(TOKEN, self.last_text())


if __name__ == "__main__":
    unittest.main()
