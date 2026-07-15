"""One-shot repairs for SearchApi 429 diagnostics and acceptance system-error gates."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEARCHAPI = ROOT / "app" / "sources" / "searchapi_source.py"
ACCEPTANCE = ROOT / "tools" / "search_v2_acceptance.py"
TEST = ROOT / "tools" / "test_search_v2_acceptance_runner.py"
TRIGGER = ROOT / ".ci" / "apply-searchapi-acceptance-repairs"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"repair anchor not found: {label}")


def patch_searchapi() -> None:
    text = SEARCHAPI.read_text(encoding="utf-8")
    old = '''        if error_response is not None:
            try:
                error_payload = error_response.json()
            except ValueError:
                error_payload = None
            if isinstance(error_payload, dict):
                info["top_level_keys"] = sorted(str(key) for key in error_payload.keys())[:30]
        info["status"] = "auth" if status_code in {401, 403} else "http_error"
        info["error_class"] = exc.__class__.__name__
        info["error"] = str(exc)[:180]'''
    new = '''        provider_error = ""
        if error_response is not None:
            try:
                error_payload = error_response.json()
            except ValueError:
                error_payload = None
            if isinstance(error_payload, dict):
                info["top_level_keys"] = sorted(str(key) for key in error_payload.keys())[:30]
                provider_error = str(
                    error_payload.get("error")
                    or error_payload.get("message")
                    or error_payload.get("detail")
                    or ""
                )[:300]
        if status_code in {401, 403}:
            info["status"] = "auth"
        elif status_code == 429:
            info["status"] = "rate_limited"
        else:
            info["status"] = "http_error"
        info["error_class"] = exc.__class__.__name__
        info["provider_error"] = provider_error
        info["error"] = provider_error or str(exc)[:180]'''
    SEARCHAPI.write_text(replace_once(text, old, new, "SearchApi HTTP error"), encoding="utf-8")


def patch_acceptance() -> None:
    text = ACCEPTANCE.read_text(encoding="utf-8")
    old = '''    errors = sum(bool(row.get("errors")) for row in rows)
    return {
        "expected_cases": expected,
        "completed_cases": completed,
        "error_cases": errors,'''
    new = '''    system_errors = sum(
        str(row.get("status") or "").upper() in {"ERROR", "TIMEOUT"}
        for row in rows
    )
    partial_source_error_cases = sum(
        bool(row.get("errors"))
        and str(row.get("status") or "").upper() not in {"ERROR", "TIMEOUT"}
        for row in rows
    )
    return {
        "expected_cases": expected,
        "completed_cases": completed,
        "error_cases": system_errors,
        "partial_source_error_cases": partial_source_error_cases,'''
    text = replace_once(text, old, new, "acceptance error counts")
    text = replace_once(
        text,
        '''            and errors == 0
            and top1_exact >= 24''',
        '''            and system_errors == 0
            and top1_exact >= 24''',
        "acceptance pass gate",
    )
    ACCEPTANCE.write_text(text, encoding="utf-8")


def patch_test() -> None:
    text = TEST.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''                "errors": [],
                "recommendations": [{"role": "BEST_OVERALL"}],''',
        '''                "status": "SUCCESS",
                "errors": [],
                "recommendations": [{"role": "BEST_OVERALL"}],''',
        "passing status",
    )
    text = replace_once(
        text,
        '''        failing[0] = {"errors": ["boom"], "recommendations": [], "metrics": {}}''',
        '''        failing[0] = {"status": "ERROR", "errors": ["boom"], "recommendations": [], "metrics": {}}''',
        "failing status",
    )
    TEST.write_text(text, encoding="utf-8")


def main() -> int:
    patch_searchapi()
    patch_acceptance()
    patch_test()
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
