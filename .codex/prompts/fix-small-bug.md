# fix-small-bug

Назначение: точечно исправить маленький баг без переписывания проекта.

Запреты: не трогать `.env`, `.venv`, `bot.db`, `*.db`, `main.py`, оплату/кредиты и Telegram token.

После правок запустить:

```bash
python -m compileall .
python tools/test_alice_parser.py
```
