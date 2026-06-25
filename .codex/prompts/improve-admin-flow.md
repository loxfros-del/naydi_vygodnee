# improve-admin-flow

Назначение: аккуратно улучшать админский сценарий, кнопки, callback_data и FSM.

Запреты: не трогать `.env`, `.venv`, `bot.db`, `*.db`, `main.py`, оплату/кредиты и Telegram token.

После правок запустить:

```bash
python -m compileall .
python tools/test_alice_parser.py
```
