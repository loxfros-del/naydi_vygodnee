# post-change-check

После правки выполнить:

```bash
python -m compileall .
python tools/test_alice_parser.py
git diff --name-only
```

Проверить, что в diff нет:

- `.env`
- `.venv`
- `bot.db`
- `*.db`
- `main.py`, если его не просили менять напрямую
