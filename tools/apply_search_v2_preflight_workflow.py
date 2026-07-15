"""One-shot patch for quota-gated Search V2 acceptance workflow."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".github" / "workflows" / "search-v2-acceptance.yml"
TRIGGER = ROOT / ".ci" / "apply-search-v2-preflight-workflow"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"preflight workflow anchor not found: {label}")


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''      - name: Install dependencies
        run: python -m pip install -r requirements.txt
      - name: Run bounded acceptance
        id: acceptance''',
        '''      - name: Install dependencies
        run: python -m pip install -r requirements.txt
      - name: Provider quota preflight
        id: preflight
        continue-on-error: true
        run: |
          python tools/searchapi_preflight.py \\
            --cases tools/live_acceptance_cases.json \\
            --limit "${{ inputs.limit }}" \\
            --buffer 2 \\
            --output data/searchapi_preflight.json \\
            > searchapi-preflight.log 2>&1
      - name: Run bounded acceptance
        if: steps.preflight.outcome == 'success'
        id: acceptance''',
        "preflight step",
    )
    text = replace_once(
        text,
        '''      - name: Show final summary
        if: always()
        run: |
          tail -120 search-v2-acceptance.log || true
          python - <<'PY'
          import json
          from pathlib import Path
          path = Path('data/search_v2_acceptance.json')
          if path.exists():
              payload = json.loads(path.read_text(encoding='utf-8'))
              print(json.dumps(payload.get('summary', {}), ensure_ascii=False, indent=2))
          PY''',
        '''      - name: Show final summary
        if: always()
        run: |
          cat searchapi-preflight.log || true
          tail -120 search-v2-acceptance.log || true
          python - <<'PY'
          import json
          from pathlib import Path
          for filename, field in (
              ('data/searchapi_preflight.json', None),
              ('data/search_v2_acceptance.json', 'summary'),
          ):
              path = Path(filename)
              if not path.exists():
                  continue
              payload = json.loads(path.read_text(encoding='utf-8'))
              print(json.dumps(payload if field is None else payload.get(field, {}), ensure_ascii=False, indent=2))
          PY''',
        "summary step",
    )
    text = replace_once(
        text,
        '''          path: |
            data/search_v2_acceptance.json
            search-v2-acceptance.log''',
        '''          path: |
            data/searchapi_preflight.json
            searchapi-preflight.log
            data/search_v2_acceptance.json
            search-v2-acceptance.log''',
        "artifact paths",
    )
    text = replace_once(
        text,
        '''      - name: Enforce requested result
        if: always() && inputs.require_pass
        run: test "${{ steps.acceptance.outcome }}" = "success"''',
        '''      - name: Enforce quota preflight
        if: always()
        run: test "${{ steps.preflight.outcome }}" = "success"
      - name: Enforce requested result
        if: always() && inputs.require_pass && steps.preflight.outcome == 'success'
        run: test "${{ steps.acceptance.outcome }}" = "success"''',
        "enforcement steps",
    )
    TARGET.write_text(text, encoding="utf-8")
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
