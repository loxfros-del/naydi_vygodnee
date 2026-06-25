# improve-client-report

Назначение: улучшать клиентский предпросмотр и полный отчёт без раскрытия лишнего до оплаты.

Запреты: не трогать `.env`, `.venv`, `bot.db`, `*.db`, `main.py`, оплату/кредиты и Telegram token.

После правок запустить:

```bash
python -m compileall .
python tools/test_alice_parser.py
```
