# Portfolio Notes

## Naydi Vygodnee — Telegram Product Search Assistant

**Role:** creator / developer

Naydi Vygodnee is a Python Telegram product-search workflow built around a simple principle: search results are only candidates until critical information is verified.

### What it demonstrates

- Python backend development
- aiogram 3 and Telegram bot architecture
- SQLite CRUD and stateful workflows
- Request parsing and structured user input
- Multi-source search and ranking
- Failure-tolerant integrations
- Human-in-the-loop verification
- Product/user-flow design

### Technical stack

Python 3.11+, aiogram 3, SQLite, Pydantic, python-dotenv, GitHub Actions.

### Key engineering decisions

- Separate discovered candidates from verified product facts.
- Track price and link verification explicitly.
- Exclude uncertain records from the final customer report.
- Keep admin review as a first-class step in the workflow.
- Allow partial source failures without stopping the entire search process.

### End-to-end flow

`free-form request → clarification → structured request → multi-source search → ranking → admin review → verification → preview → final report`
