/* Drawer behaviour — the right slide-in peek panel (requirement workspace).
 *
 * Public API:
 *   window.PrimeQA.openDrawer(id)
 *   window.PrimeQA.closeDrawer(id)
 *   window.PrimeQA.openTestDrawer(testId)   — requirement-page test walker
 *
 * Generic contract (mirrors modal.js):
 *   - [data-drawer] root, [data-drawer-panel] sliding pane,
 *     [data-drawer-close] closes from anywhere inside (incl. the overlay).
 *   - Escape closes; focus returns to the opener; body scroll locks.
 *
 * Test walker (the requirement page's drawer):
 *   - Rows carry [data-test-row][data-test-id]; DOM order IS the plan order.
 *   - openTestDrawer(testId) opens the drawer, sets "Test n of N", loads
 *     GET /claims/<id>/panel into #test-drawer-body via htmx, and wires
 *     [data-drawer-prev]/[data-drawer-next] (+ ArrowLeft/ArrowRight) to walk
 *     neighbours without closing.
 */

(function () {
  'use strict';

  let _lastFocus = null;
  let _openId = null;
  let _currentTestId = null;

  function _root(idOrEl) {
    return typeof idOrEl === 'string' ? document.getElementById(idOrEl) : idOrEl;
  }

  function openDrawer(idOrEl) {
    const d = _root(idOrEl);
    if (!d) return;
    if (_openId !== d.id) {
      _lastFocus = document.activeElement;
    }
    d.classList.remove('hidden');
    _openId = d.id;
    document.body.style.overflow = 'hidden';
    const panel = d.querySelector('[data-drawer-panel]');
    if (panel) {
      requestAnimationFrame(function () {
        panel.classList.remove('translate-x-full');
      });
    }
  }

  function closeDrawer(idOrEl) {
    const d = _root(idOrEl || _openId);
    if (!d) return;
    const panel = d.querySelector('[data-drawer-panel]');
    if (panel) panel.classList.add('translate-x-full');
    setTimeout(function () { d.classList.add('hidden'); }, 200);
    _openId = null;
    _currentTestId = null;
    document.body.style.overflow = '';
    if (_lastFocus && document.contains(_lastFocus)) _lastFocus.focus();
  }

  /* --- the requirement page's test walker ------------------------------- */

  function _rows() {
    return Array.from(document.querySelectorAll('[data-test-row][data-test-id]'));
  }

  function openTestDrawer(testId) {
    const drawer = document.getElementById('test-drawer');
    if (!drawer || !window.htmx) return false;
    const rows = _rows();
    const idx = rows.findIndex(function (r) { return r.dataset.testId === testId; });
    if (idx < 0) return false;
    _currentTestId = testId;
    const pos = drawer.querySelector('[data-drawer-position]');
    if (pos) pos.textContent = 'Test ' + (idx + 1) + ' of ' + rows.length;
    const prev = drawer.querySelector('[data-drawer-prev]');
    const next = drawer.querySelector('[data-drawer-next]');
    if (prev) prev.disabled = idx === 0;
    if (next) next.disabled = idx === rows.length - 1;
    const body = drawer.querySelector('#test-drawer-body');
    if (body) {
      body.innerHTML = '<div class="px-5 py-6 text-sm text-gray-400 flex items-center gap-2">' +
        '<svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
        '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
        '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path></svg>' +
        'Loading test case…</div>';
      window.htmx.ajax('GET', '/claims/' + testId + '/panel',
        { target: '#test-drawer-body', swap: 'innerHTML' });
    }
    openDrawer(drawer);
    return true;
  }

  function _step(delta) {
    if (!_currentTestId) return;
    const rows = _rows();
    const idx = rows.findIndex(function (r) { return r.dataset.testId === _currentTestId; });
    const to = idx + delta;
    if (idx < 0 || to < 0 || to >= rows.length) return;
    openTestDrawer(rows[to].dataset.testId);
  }

  /* Click delegation: row-open (plain left-click only — modified clicks keep
     the row link's native open-in-new-tab), prev/next, close. */
  document.addEventListener('click', function (ev) {
    const closer = ev.target.closest('[data-drawer-close]');
    if (closer) {
      const d = closer.closest('[data-drawer]');
      if (d) { ev.preventDefault(); closeDrawer(d); return; }
    }
    const prev = ev.target.closest('[data-drawer-prev]');
    if (prev) { ev.preventDefault(); _step(-1); return; }
    const next = ev.target.closest('[data-drawer-next]');
    if (next) { ev.preventDefault(); _step(1); return; }
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button !== 0) return;
    const row = ev.target.closest('[data-test-row][data-test-id]');
    if (row && !ev.target.closest('a[data-drawer-ignore], button, form, input, select, label')) {
      if (openTestDrawer(row.dataset.testId)) ev.preventDefault();
    }
  });

  /* Production-confirm reveal for run forms living in fragments: any
     select[data-run-env] toggles its form's [data-prodgate] off the selected
     option's data-is-production. Delegated — works for panels loaded later.
     The server's environment_can_bulk_run stays the authoritative gate. */
  document.addEventListener('change', function (ev) {
    const sel = ev.target.closest('select[data-run-env]');
    if (!sel) return;
    const form = sel.closest('form');
    const gate = form && form.querySelector('[data-prodgate]');
    if (!gate) return;
    const opt = sel.options[sel.selectedIndex];
    const isProd = !!(opt && opt.dataset.isProduction === 'true');
    gate.classList.toggle('hidden', !isProd);
    if (!isProd) {
      const box = gate.querySelector('input[type=checkbox]');
      if (box) box.checked = false;
    }
  });

  document.addEventListener('keydown', function (ev) {
    if (!_openId) return;
    const t = ev.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
      t.tagName === 'SELECT' || t.isContentEditable);
    if (ev.key === 'Escape') { closeDrawer(_openId); return; }
    if (typing || !_currentTestId) return;
    if (ev.key === 'ArrowLeft') { ev.preventDefault(); _step(-1); }
    if (ev.key === 'ArrowRight') { ev.preventDefault(); _step(1); }
  });

  window.PrimeQA = window.PrimeQA || {};
  window.PrimeQA.openDrawer = openDrawer;
  window.PrimeQA.closeDrawer = closeDrawer;
  window.PrimeQA.openTestDrawer = openTestDrawer;
})();
