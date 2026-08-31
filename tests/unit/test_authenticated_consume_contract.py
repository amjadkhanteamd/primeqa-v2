"""The AUTHENTICATED consume branch — the path that had ZERO coverage.

**Why this file exists.** `_consume_authenticated` is reached only when
a job's manifest carries `auth.mode` in {`vault`, `totp_env`}. Every
automated test in this repo — the fixture suites, the browser-gated
tests and the end-to-end milestone — runs GUEST jobs, and the only
authenticated execution the programme ever performed was P-1, a manual
act. **The branch was therefore untested by construction.**

That absence is what let a `TypeError` reach production: the D-465 fix
(`da8b907`) added `run_set` to the CALL at `consume.py:182` while its
edit to the DEFINITION silently failed to match, so
`_consume_authenticated(..., run_set=…)` raised
`TypeError: got an unexpected keyword argument 'run_set'` on every
authenticated job, which `consume_job`'s catch-all wall then recorded as
`failed_permanent`. It survived two merges (`3ba0c9f`, `a2679c9`) and a
production deploy because no test — and no re-run of any suite — ever
called this function.

These tests are the standing guard. Two are deliberately MECHANICAL —
they read the real call sites and bind them against the real signatures
— so the whole regression class fails at test time rather than in
production.

**On the fakes.** The browser, the login and the queue are faked; the
consume code path itself is real. Where a fake stands in for a function
the product must call correctly, the fake BINDS ITS ARGUMENTS AGAINST
THE REAL SIGNATURE (`_binding_double`) — otherwise a `**kwargs` double
would happily absorb the very keyword the product could not pass, and
the test would be blind to the defect it exists to catch.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from primeqa.browser_worker import consume

pytestmark = pytest.mark.unit

_SRC_PATH = Path(inspect.getsourcefile(consume))
_SRC = _SRC_PATH.read_text("utf-8")


def _call_keywords(func_name: str) -> set:
    """The keyword names the module actually passes at its call site."""
    tree = ast.parse(_SRC)
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == func_name):
            found |= {kw.arg for kw in node.keywords if kw.arg}
    return found


def _binding_double(real, record):
    """A test double that refuses arguments the REAL function could not
    accept. Without this, a ``**kw`` double absorbs anything and the
    assertions downstream test the double, not the product."""
    sig = inspect.signature(real)

    def _double(*args, **kwargs):
        sig.bind(*args, **kwargs)          # raises TypeError exactly as prod would
        return record(*args, **kwargs)

    return _double


# --- the general guard: call site vs signature ------------------------

@pytest.mark.parametrize("func_name", ["_consume_authenticated",
                                       "_run_surfaces"])
def test_call_site_keywords_are_accepted_by_the_signature(func_name):
    """The exact regression class. Reads the REAL call site out of the
    module and binds it against the REAL signature."""
    kwargs = _call_keywords(func_name)
    assert kwargs, f"no call site found for {func_name} — test is stale"
    sig = inspect.signature(getattr(consume, func_name))
    missing = kwargs - set(sig.parameters)
    assert not missing, (
        f"{func_name} is CALLED with {sorted(missing)} but its signature "
        f"does not accept it — this is exactly the da8b907 defect")


def test_authenticated_signature_accepts_the_full_caller_binding():
    """Bind every argument consume_job actually passes."""
    inspect.signature(consume._consume_authenticated).bind(
        None, "job-1", 1, [], {}, manifest_id="m-1",
        auth={"mode": "vault", "persona": "customer"},
        should_stop=None, run_set=["image-alt"])


# --- the functional guard: the branch actually runs -------------------

class _FakeContext:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self):
        self.context = _FakeContext()
        self.closed = False

    def new_context(self, **kw):
        return self.context

    def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self):
        self.browser = _FakeBrowser()
        self.chromium = SimpleNamespace(launch=lambda **kw: self.browser)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def wired(monkeypatch):
    """Exercise the consume paths with the browser, the login and the
    queue faked — everything else is the real code path."""
    pw = _FakePlaywright()
    calls = {"scans": [], "logins": 0, "finalized": [], "heartbeats": 0,
             "pw": pw}

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: pw)
    monkeypatch.setattr(consume, "_resolve_auth_credentials",
                        lambda session, auth: SimpleNamespace(
                            username="u", password="p", totp_seed="s"))

    def _login_rec(context, base_url, start_path, creds, max_wait_s=30):
        calls["logins"] += 1
        calls["login_args"] = (base_url, start_path, max_wait_s)
        return SimpleNamespace(events=["LOGIN_SUBMITTED", "MFA_SUBMITTED"])
    monkeypatch.setattr(consume, "login",
                        _binding_double(consume.login, _login_rec))

    def _scan_rec(url, **kw):
        calls["scans"].append({"url": url, **kw})
        return {"status": "OK", "fingerprint": {"sha256": "f" * 64},
                "timings_ms": {"navigate": 1.0}}
    monkeypatch.setattr(consume, "scan_page",
                        _binding_double(consume.scan_page, _scan_rec))

    def _hb(session, job_id):
        calls["heartbeats"] += 1
    monkeypatch.setattr(consume.q, "heartbeat", _hb)
    monkeypatch.setattr(
        consume.q, "finalize_surface",
        lambda s, j, key, attempt, obs, evidence=None: calls[
            "finalized"].append((key, obs.get("status"))))
    monkeypatch.setattr(consume.q, "mark_succeeded",
                        lambda s, j: calls.setdefault("succeeded", []).append(j))
    monkeypatch.setattr(
        consume.q, "mark_failed",
        lambda s, j, err, attempt, retryable=False: calls.setdefault(
            "failed", []).append((j, err)))
    return calls


_SURFACES = [
    {"key": "portal|/s|customer|-|-",
     "url": "https://portal.example.com/s/"},
    {"key": "portal|/s/x|customer|-|-",
     "url": "https://portal.example.com/s/x"},
]
_RUN_SET = ["image-alt", "label", "region"]
_AUTH = {"mode": "vault", "persona": "customer"}


def _job(job_id, *, auth=None, surfaces=None, run_set=_RUN_SET):
    payload = {"surfaces": surfaces or _SURFACES, "stabilisation": {},
               "engine_run_set": run_set}
    if auth:
        payload["auth"] = auth
    return {"job_id": job_id, "attempts": 1, "manifest_id": "m-1",
            "payload": payload}


def test_authenticated_branch_runs_and_threads_the_run_set(wired):
    """The end-to-end shape of the branch: ONE login for the batch, ONE
    shared context across surfaces, the manifest-pinned run set reaching
    every scan, a heartbeat per surface, and the browser cleaned up."""
    completed = consume._consume_authenticated(
        object(), "job-1", 1, _SURFACES, {"max_wait_s": 45},
        manifest_id="m-1", auth=_AUTH, should_stop=None, run_set=_RUN_SET)

    assert completed is True
    assert wired["logins"] == 1                     # ONE login per batch
    assert len(wired["scans"]) == 2                 # both surfaces scanned
    shared = wired["pw"].browser.context
    for scan in wired["scans"]:
        assert scan["run_set"] == _RUN_SET          # the pin reaches the engine
        assert scan["max_wait_s"] == 45
        assert scan["context"] is shared            # the SAME session, not just non-None
        assert scan["landed_check"] is consume.assert_session
    # one beat before the login (the slow step) + one per surface
    assert wired["heartbeats"] == 3
    assert [f[0] for f in wired["finalized"]] == [s["key"] for s in _SURFACES]
    assert {f[1] for f in wired["finalized"]} == {"OK"}
    assert wired["pw"].browser.closed                # no leaked Chromium


def test_guest_and_authenticated_paths_pass_the_same_run_set(wired):
    """Parity across the REAL fork in consume_job — both jobs go in as
    production job dicts, so this compares the two production branches
    rather than two hand-made calls. The defect broke exactly this."""
    consume.consume_job(object(), _job("job-guest", surfaces=_SURFACES[:1]))
    guest = wired["scans"][-1]
    assert wired["logins"] == 0                      # guest never logs in
    wired["scans"].clear()

    consume.consume_job(object(), _job("job-auth", auth=_AUTH,
                                       surfaces=_SURFACES[:1]))
    authenticated = wired["scans"][-1]
    assert wired["logins"] == 1

    assert not wired.get("failed"), wired.get("failed")
    assert guest["run_set"] == authenticated["run_set"] == _RUN_SET
    assert guest["context"] is None                  # guest: no shared session
    assert authenticated["context"] is not None      # authenticated: one


def test_should_stop_is_honoured_on_the_authenticated_path(wired):
    """``should_stop`` is the SIGTERM lifecycle and reaches the branch by
    the same keyword mechanism ``run_set`` did. Stopping after the first
    surface must leave the job incomplete for the reaper."""
    completed = consume._consume_authenticated(
        object(), "job-stop", 1, _SURFACES, {}, manifest_id="m-1",
        auth=_AUTH, should_stop=lambda: True, run_set=_RUN_SET)

    assert completed is False                        # lease left for the reaper
    assert len(wired["scans"]) == 1                  # only the current surface
    assert wired["logins"] == 1


def test_consume_job_reaches_the_authenticated_branch_without_typeerror(
        wired):
    """The production shape: a vault job through consume_job. Before the
    fix this raised TypeError at the call site and the job was walled to
    failed_permanent with error_text='TypeError'.

    The scan-count and status assertions matter: `_run_surfaces` wraps
    each scan in its own `except Exception`, so a fault one level deeper
    would become an ERROR observation and the job would STILL be marked
    succeeded. Asserting only 'not failed' would pass vacuously.
    """
    consume.consume_job(object(), _job("job-prod", auth=_AUTH))

    assert not wired.get("failed"), (
        f"authenticated job failed: {wired.get('failed')}")
    assert wired.get("succeeded") == ["job-prod"]
    assert wired["logins"] == 1
    assert len(wired["scans"]) == 2                  # not vacuous
    assert {f[1] for f in wired["finalized"]} == {"OK"}   # no swallowed fault
    assert all(s["run_set"] == _RUN_SET for s in wired["scans"])


# --- the package-wide sweep: the same defect class, everywhere ----------
#
# The guards above pin the ONE call site that broke. This one asks the same
# question of every call in `primeqa` whose callee this repo defines at
# module level, INCLUDING across modules — the defect's class is not
# specific to a file.
#
# SCOPE, measured 2026-08-31 and stated plainly, because a guard trusted for
# more than it does is worse than no guard:
#   * primeqa holds 24,567 calls; 4,019 carry keywords.
#   * The sweep resolves a callee for 3,591 calls and binds each against its
#     real signature. In scope: a bare-name call to a def in the same module,
#     a bare-name call to a name imported from another primeqa module, and
#     `alias.fn(...)` where alias is an imported primeqa module.
#   * OUT OF SCOPE, never checked: method calls (`self.fn`, `obj.fn`), calls
#     into third-party libraries, names resolved dynamically, and — the one
#     that matters here — ANY CALL THAT UNPACKS `*args` / `**kwargs`.
#
# That last exclusion is not academic. `run_set` reaches the engine through
# `scan_page(url, ..., **_scan_kwargs(surface, stabilisation, run_set))`, so
# deleting `run_set` from `scan_page` is INVISIBLE to this sweep. The
# functional tests above cover that hop instead: their doubles bind against
# the real signatures (`_binding_double`), so the same deletion fails four of
# them. Static sweep and binding doubles are complements, and neither alone
# closes the class.
#
# The sweep found ZERO instances when written. It exists to keep that true.

_PKG = Path(__file__).resolve().parents[2] / "primeqa"


def _module_symbols():
    """Every primeqa module's UNAMBIGUOUS module-level defs — a name is
    dropped when the module binds it more than once (def + import, def +
    assignment, two defs), because then a call site cannot be attributed
    to the def with confidence. Skipping is the safe direction: a false
    positive here would get the test disabled."""
    trees, defs = {}, {}
    for path in sorted(_PKG.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except SyntaxError:
            continue
        mod = ".".join(path.relative_to(_PKG.parent).with_suffix("").parts)
        trees[mod] = (path, tree)
        bound, found = {}, {}
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names, found[node.name] = [node.name], node
            elif isinstance(node, ast.ClassDef):
                names = [node.name]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                names = [n.id for t in targets for n in ast.walk(t)
                         if isinstance(n, ast.Name)]
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [(a.asname or a.name).split(".")[0]
                         for a in node.names]
            for n in names:
                bound[n] = bound.get(n, 0) + 1
        defs[mod] = {k: v for k, v in found.items() if bound.get(k) == 1}
    return trees, defs


def _signature_of(fn):
    """An inspect.Signature for an ast function def, so one bind() covers
    the whole class: an unacceptable keyword, a MISSING REQUIRED argument
    (the mirror image of the defect), and positional over-supply."""
    P, a = inspect.Parameter, fn.args
    params = [P(x.arg, P.POSITIONAL_ONLY) for x in a.posonlyargs]
    n_pos, n_def = len(a.posonlyargs) + len(a.args), len(a.defaults)
    for i, x in enumerate(a.args):
        has_default = (len(a.posonlyargs) + i) >= (n_pos - n_def)
        params.append(P(x.arg, P.POSITIONAL_OR_KEYWORD,
                        default=None if has_default else P.empty))
    if a.vararg:
        params.append(P(a.vararg.arg, P.VAR_POSITIONAL))
    for x, d in zip(a.kwonlyargs, a.kw_defaults):
        params.append(P(x.arg, P.KEYWORD_ONLY,
                        default=None if d is not None else P.empty))
    if a.kwarg:
        params.append(P(a.kwarg.arg, P.VAR_KEYWORD))
    return inspect.Signature(params)


def _imports_of(tree, mod):
    """Names bound to primeqa functions/modules anywhere in the file.
    Function-local imports are folded in because this codebase uses them
    pervasively; a name that is BOTH imported and defined here was already
    dropped as ambiguous by _module_symbols."""
    frm, aliases = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = mod.split(".")[:-node.level]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if not target.startswith("primeqa"):
                continue
            for a in node.names:
                if a.name != "*":
                    frm[a.asname or a.name] = (target, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("primeqa"):
                    aliases[a.asname or a.name] = a.name
    return frm, aliases


def _resolve(node, local, frm, aliases, defs):
    """The ast def this call targets, or None when out of scope."""
    if isinstance(node.func, ast.Name):
        name = node.func.id
        if name in local:
            return local[name]
        if name in frm:
            mod, orig = frm[name]
            return defs.get(mod, {}).get(orig)
    elif (isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)):
        alias = node.func.value.id
        if alias in aliases:
            return defs.get(aliases[alias], {}).get(node.func.attr)
        if alias in frm:                       # from pkg import mod; mod.fn()
            mod, orig = frm[alias]
            return defs.get(f"{mod}.{orig}", {}).get(node.func.attr)
    return None


def test_no_call_site_in_the_package_outruns_its_signature():
    """The da8b907 defect class, swept across ``primeqa``.

    A keyword the callee cannot bind, a required parameter the caller
    omits, or a positional argument past the end of the signature is a
    guaranteed ``TypeError`` the moment that line executes. In
    `_consume_authenticated` that line sat on a branch no test reached,
    so the TypeError shipped to production instead of failing a build.
    Read the SCOPE note above before trusting this to mean more than it
    does — in particular, calls that unpack ``**kwargs`` are invisible
    here and are covered by the binding doubles instead.
    """
    trees, defs = _module_symbols()
    findings, checked = [], 0

    for mod, (path, tree) in trees.items():
        local = defs[mod]
        frm, aliases = _imports_of(tree, mod)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = _resolve(node, local, frm, aliases, defs)
            if fn is None:
                continue
            if (any(isinstance(a, ast.Starred) for a in node.args)
                    or any(k.arg is None for k in node.keywords)):
                continue                       # *args / **kwargs at the call
            checked += 1
            try:
                _signature_of(fn).bind(
                    *[None] * len(node.args),
                    **{k.arg: None for k in node.keywords})
            except TypeError as exc:
                findings.append(
                    f"{path.relative_to(_PKG.parent)}:{node.lineno} "
                    f"{ast.unparse(node.func)}(...) cannot bind against its "
                    f"own signature ({fn.name}, line {fn.lineno}): {exc}")

    assert len(trees) >= 330, (
        f"only {len(trees)} modules parsed — the sweep is not reaching "
        f"the package")
    assert checked >= 3200, (
        f"only {checked} calls checked (3,591 when written) — the resolver "
        f"has regressed and the sweep is going quiet")
    assert not findings, (
        f"{len(findings)} call site(s) cannot bind against their own "
        f"signature:\n  " + "\n  ".join(findings))
