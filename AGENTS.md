# AGENTS.md

## Проект

`Найди выгоднее` — Telegram-бот подбора выгодных товаров. Сценарий: клиентский запрос -> заявка -> админ -> Gemini/Алиса -> карточки -> роли ТОП-1/запасной/бюджетный/осторожно -> предпросмотр -> оплата -> полный отчёт.

## Запреты

- Не трогать `.env`, `.venv`, `bot.db`, `*.db`.
- Не менять Telegram token.
- Не менять `main.py` без прямого запроса.
- Не ломать SQLite-схему, оплату и кредиты.
- Не переписывать проект целиком.
- Не менять `app`-код, если задача про инструкции, агентов, промты или безопасные проверки.

## Роли отчёта

- `BEST` / `TOP` / `TOP1` = ТОП-1.
- `BACKUP` / `APPROVED_BACKUP` = запасной.
- `BUDGET` / `APPROVED_BUDGET` = бюджетный.
- `DO_NOT_BUY` / `CAUTION` = осторожно.
- `REJECTED` / `REJECTED_AUTO` не показывать клиенту.

## Правила парсера

- URL не должен становиться отдельной карточкой.
- Любая правка парсера Алисы требует теста в `tools/test_alice_parser.py`.

## Проверки после правок

```bash
python -m compileall .
python tools/test_alice_parser.py
```

Codex всегда пишет изменённые файлы и тесты, которые запускались.

## ExecPlans

When writing complex features or significant refactors, use an ExecPlan (as described in `.agent/PLANS.md`) from design to implementation.
