"""Runner for the PrimeQA self-validation suite.

Loads a JSON file expressed in the system-validation step grammar and
executes each test against a Flask test client (default) or a real HTTP
endpoint (when `base_url` is passed).

See docs/design/system-validation.md for the grammar reference.
"""

from __future__ import annotations

import json
import re
import time
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------- Variable substitution ------------------------------------------

_VAR_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_.]*)")


def _lookup(vars: Dict[str, Any], dotted: str) -> Any:
    """Walk $foo.bar.baz into nested dicts, list indices, or obj attrs.

    Numeric segments (`$rows.0.0`) index into list/tuple values, which is
    what `assert_db` produces for raw SQL results.
    """
    parts = dotted.split(".")
    cur: Any = vars.get(parts[0])
    for p in parts[1:]:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, (list, tuple)) and p.lstrip("-").isdigit():
            idx = int(p)
            cur = cur[idx] if -len(cur) <= idx < len(cur) else None
        else:
            cur = getattr(cur, p, None)
    return cur


def _substitute(value: Any, vars: Dict[str, Any]) -> Any:
    """Replace $name / $name.sub in strings; recurse through dicts/lists."""
    if isinstance(value, str):
        def _sub(m):
            name = m.group(1)
            if name == "uuid":
                return _uuid.uuid4().hex[:8]
            got = _lookup(vars, name)
            return "" if got is None else str(got)
        # If the whole string is a single $-reference, return the raw value
        stripped = value.strip()
        if stripped.startswith("$") and _VAR_RE.fullmatch(stripped):
            return _lookup(vars, stripped[1:])
        return _VAR_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _substitute(v, vars) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, vars) for v in value]
    return value


# ---------- HTTP adapter ---------------------------------------------------

class FlaskTestClientAdapter:
    """Adapter around Flask's test_client so the runner can target it."""

    def __init__(self, client):
        self.client = client
        self._cookie = None

    def request(self, method: str, url: str, *, body=None, headers=None):
        headers = dict(headers or {})
        method = method.upper()
        kwargs = {"headers": headers}
        if body is not None:
            if headers.get("Content-Type") == "application/x-www-form-urlencoded":
                kwargs["data"] = body
            else:
                kwargs["json"] = body
        r = self.client.open(url, method=method, **kwargs)
        try:
            parsed_body = r.get_json()
        except Exception:
            parsed_body = None
        if parsed_body is None and r.data:
            # HTML / text response
            try:
                parsed_body = r.data.decode("utf-8", errors="replace")
            except Exception:
                parsed_body = None
        return {
            "status_code": r.status_code,
            "body": parsed_body,
            "headers": dict(r.headers),
            "text": r.data.decode("utf-8", errors="replace") if r.data else "",
        }

    def set_cookie(self, name, value):
        self._cookie = (name, value)
        self.client.set_cookie(name, value)


# ---------- Step dispatch --------------------------------------------------

class StepError(Exception):
    def __init__(self, msg, step, detail=None):
        super().__init__(msg)
        self.step = step
        self.detail = detail


@dataclass
class StepResult:
    action: str
    ok: bool
    detail: Optional[Dict[str, Any]] = None


def _do_login(step, vars, adapter):
    body = {
        "email": step["email"], "password": step["password"],
        "tenant_id": step.get("tenant_id", 1),
    }
    resp = adapter.request("POST", "/api/auth/login", body=body)
    if resp["status_code"] != 200:
        raise StepError("login failed",
                        step, detail={"status": resp["status_code"], "body": resp["body"]})
    tok = (resp["body"] or {}).get("access_token")
    if not tok:
        raise StepError("login returned no access_token", step, detail=resp)
    adapter.set_cookie("access_token", tok)
    save = step.get("save_as")
    if save:
        vars[save] = tok
    return StepResult("login", True)


def _do_http(step, vars, adapter):
    method = step["method"]
    url = _substitute(step["url"], vars)
    body = _substitute(step.get("body"), vars)
    headers = _substitute(step.get("headers"), vars)
    resp = adapter.request(method, url, body=body, headers=headers)
    save = step.get("save_as")
    if save:
        vars[save] = resp
    # Optional inline assertions
    want = step.get("expect_status")
    if want is not None and resp["status_code"] != want:
        raise StepError(
            f"{method} {url} expected status {want}, got {resp['status_code']}",
            step,
            detail={"status": resp["status_code"], "body": _trim(resp["body"])},
        )
    return StepResult("http", True, detail={"status": resp["status_code"]})


def _do_verify(step, vars, adapter):
    target = step["target"]
    # target may be "$foo.bar" (dotted) or any substituted string literal
    if target.startswith("$"):
        actual = _lookup(vars, target[1:])
    else:
        actual = _substitute(target, vars)
    checks = 0
    if "equals" in step:
        checks += 1
        expected = _substitute(step["equals"], vars)
        if actual != expected:
            raise StepError(f"verify: expected {target} == {expected!r}, got {actual!r}",
                            step, detail={"expected": expected, "actual": actual})
    if "in" in step:
        checks += 1
        allowed = _substitute(step["in"], vars)
        if actual not in allowed:
            raise StepError(f"verify: {target} not in {allowed!r}", step,
                            detail={"expected": allowed, "actual": actual})
    if "gte" in step:
        checks += 1
        bound = _substitute(step["gte"], vars)
        if not (actual is not None and actual >= bound):
            raise StepError(f"verify: {target} >= {bound} failed (got {actual!r})",
                            step, detail={"expected_gte": bound, "actual": actual})
    if "lte" in step:
        checks += 1
        bound = _substitute(step["lte"], vars)
        if not (actual is not None and actual <= bound):
            raise StepError(f"verify: {target} <= {bound} failed (got {actual!r})",
                            step, detail={"expected_lte": bound, "actual": actual})
    if "contains" in step:
        checks += 1
        needle = _substitute(step["contains"], vars)
        if needle not in (actual or ""):
            raise StepError(f"verify: {target} did not contain {needle!r}", step,
                            detail={"expected_contains": needle, "actual": _trim(actual)})
    if "matches" in step:
        checks += 1
        pattern = _substitute(step["matches"], vars)
        if not re.search(pattern, str(actual or "")):
            raise StepError(f"verify: {target} did not match /{pattern}/",
                            step, detail={"pattern": pattern, "actual": _trim(actual)})
    if "is_not_none" in step and step["is_not_none"]:
        checks += 1
        if actual is None:
            raise StepError(f"verify: {target} expected non-null, got None",
                            step, detail={"actual": None})
    if checks == 0:
        raise StepError("verify step has no assertion", step)
    return StepResult("verify", True)


def _do_save(step, vars, adapter):
    src = step["from"]
    if not src.startswith("$"):
        raise StepError("save.from must start with $", step)
    val = _lookup(vars, src[1:])
    vars[step["as"]] = val
    return StepResult("save", True)


def _do_wait(step, vars, adapter):
    time.sleep(float(step.get("seconds") or 0))
    return StepResult("wait", True)


def _do_assert_db(step, vars, adapter):
    """Last-resort direct DB assertion. Read-only; tests remain observational."""
    from sqlalchemy import text
    from primeqa.db import SessionLocal
    sql = _substitute(step["sql"], vars)
    expect = step.get("expect_rows")
    db = SessionLocal()
    try:
        rows = list(db.execute(text(sql)))
        if expect is not None and len(rows) != expect:
            raise StepError(
                f"assert_db: expected {expect} rows, got {len(rows)} (sql={sql!r})",
                step, detail={"rowcount": len(rows), "sql": sql},
            )
        if step.get("save_as"):
            vars[step["save_as"]] = [tuple(r) for r in rows]
        return StepResult("assert_db", True, detail={"rowcount": len(rows)})
    finally:
        db.close()


DISPATCH = {
    "login": _do_login,
    "http": _do_http,
    "verify": _do_verify,
    "save": _do_save,
    "wait": _do_wait,
    "assert_db": _do_assert_db,
}


# ---------- Test + suite execution ----------------------------------------

@dataclass
class TestOutcome:
    category: str
    name: str
    status: str            # 'passed' | 'failed' | 'skipped' | 'error'
    step_index: Optional[int] = None
    error: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None


@dataclass
class SuiteReport:
    suite_name: str
    outcomes: List[TestOutcome] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status in ("failed", "error"))

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "skipped")

    @property
    def all_green(self) -> bool:
        return self.failed == 0 and self.passed > 0

    def render(self) -> str:
        lines = [f"\n=== {self.suite_name} ==="]
        for o in self.outcomes:
            marker = {"passed": "PASS", "failed": "FAIL", "error": "ERROR",
                      "skipped": "SKIP"}.get(o.status, "?")
            label = f"[{o.category}] {o.name}"
            if o.status in ("failed", "error") and o.error:
                loc = f" @step {o.step_index}" if o.step_index is not None else ""
                lines.append(f"  {marker}  {label}{loc}: {o.error}")
            else:
                lines.append(f"  {marker}  {label}")
        lines.append("")
        lines.append(f"Totals: {self.passed} passed, {self.failed} failed, "
                     f"{self.skipped} skipped (of {len(self.outcomes)})")
        return "\n".join(lines)


def _trim(value, limit=500):
    if value is None:
        return None
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "\u2026"


def load_suite(path: str) -> Dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text())


def run_suite(suite: Dict[str, Any], *, client=None, base_url: Optional[str] = None
              ) -> SuiteReport:
    """Execute a suite dict. Returns a SuiteReport.

    By default uses `primeqa.app.app.test_client()`. Passing `client` lets
    the caller inject a Flask test client (useful when running from tests).
    `base_url` is accepted for future real-HTTP mode but currently ignored.
    """
    if client is None:
        from primeqa.app import app as flask_app
        client = flask_app.test_client()
    adapter = FlaskTestClientAdapter(client)
    report = SuiteReport(suite_name=suite.get("name", "unnamed"))

    # Shared variables across the whole suite
    shared_vars: Dict[str, Any] = {}

    # Run suite-level setup once (login, etc.)
    for setup_step in suite.get("setup", []):
        try:
            _execute_step(setup_step, shared_vars, adapter)
        except StepError as e:
            # Fail every test if setup breaks
            for cat in suite.get("categories", []):
                for t in cat.get("tests", []):
                    report.outcomes.append(TestOutcome(
                        category=cat["name"], name=t["name"],
                        status="error",
                        error=f"suite setup failed: {e}",
                    ))
            return report

    for cat in suite.get("categories", []):
        for t in cat.get("tests", []):
            if t.get("skip_reason"):
                report.outcomes.append(TestOutcome(
                    category=cat["name"], name=t["name"],
                    status="skipped", error=t["skip_reason"],
                ))
                continue
            # Per-test copy of vars so tests don't pollute each other,
            # but seeded from the shared (post-setup) vars
            vars = dict(shared_vars)
            step_idx = None
            try:
                for step_idx, step in enumerate(t.get("steps", []), start=1):
                    _execute_step(step, vars, adapter)
                report.outcomes.append(TestOutcome(
                    category=cat["name"], name=t["name"], status="passed",
                ))
            except StepError as e:
                report.outcomes.append(TestOutcome(
                    category=cat["name"], name=t["name"],
                    status="failed", step_index=step_idx,
                    error=str(e), detail=e.detail,
                ))
            except Exception as e:
                import traceback; traceback.print_exc()
                report.outcomes.append(TestOutcome(
                    category=cat["name"], name=t["name"],
                    status="error", step_index=step_idx,
                    error=f"{type(e).__name__}: {e}",
                ))
    return report


def _execute_step(step, vars, adapter):
    action = step.get("action")
    handler = DISPATCH.get(action)
    if not handler:
        raise StepError(f"unknown action: {action!r}", step)
    return handler(step, vars, adapter)
