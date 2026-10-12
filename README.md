# telegram-notify

Уведомления в Telegram из Python и ноутбуков. Python 3.10+, без обязательных зависимостей.

```bash
pip install "git+https://github.com/ferraro-ai/telegram-notify.git"
```

Создай бота через [@BotFather](https://t.me/BotFather) (`/newbot`), открой своего бота и отправь `/start`.
Задай `TELEGRAM_BOT_TOKEN`, затем получи ID своего чата:

```bash
python -m telegram_notify chat-id
python -m telegram_notify test
```

`chat-id` показывает бота, которому принадлежит токен. Если список пуст, отправь **новое** сообщение этому боту: команда читает очередь событий, а не историю чата.

Настройки: **`TELEGRAM_BOT_TOKEN`** и **`TELEGRAM_CHAT_ID`**.
Для каждого значения: Colab Secrets → окружение → ввод (токен скрыт).
В Colab разреши ноутбуку доступ к секретам. Значения не сохраняются; `.env` автоматически не загружается.
Внутри ноутбука получить ID можно так: `from telegram_notify import get_chat_ids; get_chat_ids()`.
В PyCharm Run/Debug скрытый ввод может быть недоступен: используй **Terminal** либо задай переменные в **Run → Edit Configurations → Environment variables**.

```python
from telegram_notify import configure, notify, enable_notebook_errors

configure()
enable_notebook_errors("Запуск задачи")  # необязательно

# После нужной ячейки или в конце ноутбука:
notify("✅ Задача завершена")
```

Ошибки ячеек: контекст, номер ячейки, тип и описание до 800 символов (`max_error_chars`), без stacktrace.
Исходная ошибка остаётся в IPython. Повторное подключение не дублирует сообщения; отключение — `disable_notebook_errors()`.

Отправка возвращает `True`/`False`; сбой Telegram не ломает задачу. `notify(..., raise_on_error=True)` — строгий режим.
Для сервисов/MCP: `configure(allow_prompt=False, timeout=5)`; ошибки настройки поднимаются сразу.
Сам импорт ничего не запрашивает и не отправляет. При полной смерти runtime уведомление не гарантируется.
