# mvp-telegram-test

Назначение: проверить MVP-сценарий Telegram-бота без запуска реального polling.

Запреты: не трогать `.env`, `.venv`, `bot.db`, `*.db`, `main.py`, оплату/кредиты и Telegram token.

После правок запустить:

```bash
python -m compileall .
python tools/test_alice_parser.py
```
