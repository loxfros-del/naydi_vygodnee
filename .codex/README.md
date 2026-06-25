# Codex setup

## Агенты

- `code-mapper` — read-only карта файлов и план правки.
- `bug-fixer` — маленький точечный баг, минимальные изменения.
- `report-reviewer` — проверка `app/report_builder.py` и ролей отчёта.
- `alice-parser-guardian` — только `app/alice_service.py` и `tools/test_alice_parser.py`.
- `admin-flow-reviewer` — админка, кнопки, `callback_data`, FSM.
- `test-runner` — только запуск проверок, без изменений.

## Промты

- `fix-small-bug.md` — маленький баг.
- `check-before-commit.md` — проверка перед коммитом.
- `improve-admin-flow.md` — админский сценарий.
- `improve-client-report.md` — клиентский отчёт.
- `fix-alice-parser.md` — парсер Алисы.
- `mvp-telegram-test.md` — MVP-проверка без запуска polling.

## Команды

```bash
python tools/codex_guard.py
python tools/codex_smoke.py
python -m compileall .
python tools/test_alice_parser.py
```

## Как пользоваться

- Для карты проекта: используй `code-mapper`.
- Для маленького бага: используй `bug-fixer` + `.codex/prompts/fix-small-bug.md`.
- Для отчёта: используй `report-reviewer` + `.codex/prompts/improve-client-report.md`.
- Для парсера: используй `alice-parser-guardian` + `.codex/prompts/fix-alice-parser.md`.
- Перед коммитом:

```bash
python tools/codex_guard.py
python tools/codex_smoke.py
```

## Когда НЕ использовать агентов

- Когда надо просто написать текст клиенту.
- Когда надо протестировать Telegram руками.
- Когда надо сделать продажный пост.
- Когда надо решить бизнес-вопрос.
- Когда можно поправить одну строку вручную.
