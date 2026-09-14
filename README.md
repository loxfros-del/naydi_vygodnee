# Naydi Vygodnee — Telegram Product Search Assistant

A Telegram bot that turns a free-form shopping request into a structured product-search workflow.

The bot collects requirements, searches multiple sources, ranks candidate products, tracks verification status, lets an admin review results, and generates a final report only from verified data.

## Why this project exists

Shopping search often produces dozens of links with stale prices, weak relevance, or unverified information. This project focuses on a more controlled workflow:

`request → clarification → search → ranking → verification → preview → final report`

Instead of treating every search result as truth, the system keeps explicit verification states and blocks the final report when critical information is not confirmed.

## Tech stack

- Python 3.11+
- aiogram 3
- SQLite
- Pydantic
- python-dotenv
- GitHub Actions

## Architecture

```text
main.py                  application entry point
app/
  config.py              environment configuration
  db.py                  SQLite models and CRUD
  states.py              FSM states
  keyboards.py           Telegram UI
  request_parser.py      request parsing
  search_links.py        fallback search links
  product_search.py      multi-source search and ranking
  report_builder.py      preview and final report generation
  handlers/
    user.py              user flow
    admin.py             admin workflow
```

## Core workflow

1. The user describes what they want in natural language.
2. The bot extracts product, budget, location, use case, and criteria.
3. Missing information triggers a small number of clarification questions.
4. A structured request is created and becomes available to the admin.
5. Multi-source search collects candidate products.
6. Candidates are ranked and reviewed instead of being immediately presented as verified facts.
7. The admin can keep, remove, reorder, edit, and verify candidates.
8. A customer preview can be generated without exposing the complete result set.
9. The full report is generated only after the required product data is verified.

## Multi-source search

The search layer is designed to tolerate partial failures. One source failing does not stop the rest of the pipeline.

Current search logic can use independent sources and site-search fallbacks for marketplaces and electronics retailers. Search results are treated as **candidates**, not automatically trusted product facts.

## Verification model

The project deliberately separates “found” from “verified”.

- A discovered link can remain unverified.
- Price confirmation is tracked separately.
- Store/domain mismatches can be flagged.
- A top candidate over budget is not silently promoted as a valid match.
- Unverified candidates are excluded from the final customer report.
- The highest-priority candidate must have confirmed price and link data before the report can be completed.

## Local run

```bash
python -m pip install -r requirements.txt
cp .env.example .env
# Configure BOT_TOKEN and ADMIN_IDS in .env
python -m compileall .
python tools/test_search.py "Need a TV for PS5 under 45000"
python main.py
```

## What this project demonstrates

- Python backend development
- Telegram bot development with aiogram
- SQLite CRUD and stateful workflows
- Data validation and ranking
- Multi-source integrations
- Failure-tolerant search pipelines
- Human-in-the-loop verification
- Product and user-flow thinking
- AI-assisted development workflow

## Current status

This is an actively developed MVP. The strongest part of the project is the end-to-end workflow and verification model: the system does not hide uncertain data behind confident output and keeps admin review as an explicit step before delivery.
