/* Test Library page — bulk select, single-row actions, grouped-view state,
 * "Add group to suite" modal. Extracted from templates/test_cases/library.html
 * to keep business logic out of Jinja (audit finding U6, 2026-04-19).
 *
 * This file is two IIFEs:
 *   1. Per-group "Add to suite" modal wiring (runs only if the modal is
 *      in the DOM — i.e. the grouped view is active).
 *   2. Single-row + bulk delete/restore/purge + accordion state.
 *
 * Both depend on:
 *   window.PrimeQA.toast(msg, kind)
 *   window.PrimeQA.confirm({title, message, onConfirm, variant, typeTo, submitLabel})
 *   window.PrimeQA.showErrorFromResponse(response)
 *
 * The HTML contract (data-* attributes we read):
 *   button.add-group-to-suite[data-coverage-tc-ids, data-all-tc-ids, data-group-title]
 *   button[data-delete-id, data-tc-title]
 *   button[data-restore-id]
 *   button[data-purge-id, data-tc-title]
 *   input.row-check[value="<tc_id>"]
 *   input.group-check (inside each <details>)
 *   input#select-all (flat view only)
 *   details[data-req-id] (grouped view accordion nodes)
 */

/* ---- Per-group "Add to suite" modal ---------------------------------- */
(function () {
  'use strict';
  const modal = document.getElementById('add-to-suite-modal');
  if (!modal) return;

  const titleEl = document.getElementById('add-to-suite-group-title');
  const chipsEl = document.getElementById('ats-coverage-chips');
  const countEl = document.getElementById('ats-count');
  const suiteSel = document.getElementById('ats-suite-select');
  const newSuiteBlock = document.getElementById('ats-new-suite');
  const newNameEl = document.getElementById('ats-new-name');
  const newTypeEl = document.getElementById('ats-new-type');
  const addBtn = document.getElementById('ats-add-btn');

  const COV_META = {
    positive: { label: 'positive', cls: 'bg-green-50 text-green-700 ring-green-200' },
    negative_validation: { label: 'negative validation', cls: 'bg-red-50 text-red-700 ring-red-200' },
    boundary: { label: 'boundary', cls: 'bg-amber-50 text-amber-800 ring-amber-200' },
    edge_case: { label: 'edge case', cls: 'bg-purple-50 text-purple-700 ring-purple-200' },
    regression: { label: 'regression', cls: 'bg-sky-50 text-sky-700 ring-sky-200' },
    other: { label: 'other', cls: 'bg-gray-50 text-gray-600 ring-gray-200' },
  };

  let covMap = {};
  let selectedCov = new Set();
  let allIds = [];

  function updateCount() {
    const n = Array.from(selectedCov).reduce((s, c) => s + (covMap[c]?.length || 0), 0);
    countEl.textContent = n;
    addBtn.disabled = n === 0 || !suiteSel.value ||
      (suiteSel.value === '__new__' && !newNameEl.value.trim());
  }

  function renderChips() {
    chipsEl.innerHTML = '';
    Object.keys(covMap).forEach((k) => {
      const meta = COV_META[k] || COV_META.other;
      const count = covMap[k].length;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'inline-flex items-center px-2 py-1 rounded text-xs font-medium ring-1 ' +
        meta.cls +
        (selectedCov.has(k) ? ' ring-2 shadow-inner' : ' opacity-60');
      btn.textContent = count + ' ' + meta.label;
      btn.addEventListener('click', () => {
        if (selectedCov.has(k)) selectedCov.delete(k); else selectedCov.add(k);
        renderChips();
        updateCount();
      });
      chipsEl.appendChild(btn);
    });
  }

  suiteSel.addEventListener('change', () => {
    newSuiteBlock.classList.toggle('hidden', suiteSel.value !== '__new__');
    updateCount();
  });
  newNameEl.addEventListener('input', updateCount);

  document.querySelectorAll('.add-group-to-suite').forEach((btn) => {
    btn.addEventListener('click', () => {
      try {
        covMap = JSON.parse(btn.dataset.coverageTcIds || '{}');
      } catch (_) { covMap = {}; }
      try {
        allIds = JSON.parse(btn.dataset.allTcIds || '[]');
      } catch (_) { allIds = []; }
      selectedCov = new Set(Object.keys(covMap));
      titleEl.textContent = btn.dataset.groupTitle || '';
      suiteSel.value = '';
      newSuiteBlock.classList.add('hidden');
      newNameEl.value = (btn.dataset.groupTitle || '') + ' Tests';
      newTypeEl.value = 'smoke';
      renderChips();
      updateCount();
      modal.classList.remove('hidden');
    });
  });

  async function api(method, url, body) {
    const r = await fetch(url, {
      method,
      credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) { await window.PrimeQA.showErrorFromResponse(r); throw new Error('failed'); }
    return r.json();
  }

  addBtn.addEventListener('click', async () => {
    const ids = Array.from(selectedCov).flatMap((c) => covMap[c] || []);
    if (!ids.length) return;

    let suiteId = suiteSel.value;
    try {
      if (suiteId === '__new__') {
        const name = newNameEl.value.trim();
        if (!name) return;
        const created = await api('POST', '/api/suites', {
          name, suite_type: newTypeEl.value,
        });
        suiteId = (created?.data?.id || created?.id || '').toString();
        if (!suiteId) throw new Error('create suite returned no id');
      }
      const result = await api('POST', '/api/suites/' + suiteId + '/test-cases/bulk',
        { test_case_ids: ids });
      const addedN = (result.added || []).length;
      const dupN = (result.already_in || []).length;
      let msg = addedN + ' added to suite';
      if (dupN) msg += ', ' + dupN + ' already there';
      window.PrimeQA.toast(msg, 'success');
      modal.classList.add('hidden');
    } catch (_) {
      addBtn.disabled = false;
    }
  });
})();

/* ---- Row actions, bulk select, accordion state ---------------------- */
(function () {
  'use strict';

  async function apiCall(method, url, body) {
    const resp = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!resp.ok) {
      await window.PrimeQA.showErrorFromResponse(resp);
      throw new Error('request failed');
    }
    return resp;
  }

  /* Single-row delete / restore / purge — shared by flat + grouped views. */
  document.querySelectorAll('[data-delete-id]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.deleteId;
      const title = btn.dataset.tcTitle;
      window.PrimeQA.confirm({
        title: 'Move to trash?',
        variant: 'danger',
        message: '"' + title + '" will be moved to trash. You can restore it from Trash.',
        submitLabel: 'Move to trash',
        onConfirm: async () => {
          try {
            await apiCall('DELETE', '/api/test-cases/' + id);
            window.PrimeQA.toast('Test case moved to trash', 'success');
            btn.closest('tr')?.remove();
          } catch (_) { /* apiCall surfaced the error */ }
        },
      });
    });
  });

  document.querySelectorAll('[data-restore-id]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try {
        await apiCall('POST', '/api/test-cases/' + btn.dataset.restoreId + '/restore');
        window.PrimeQA.toast('Restored', 'success');
        btn.closest('tr')?.remove();
      } catch (_) { /* surfaced */ }
    });
  });

  document.querySelectorAll('[data-purge-id]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.purgeId;
      const title = btn.dataset.tcTitle;
      window.PrimeQA.confirm({
        title: 'Permanently delete?',
        variant: 'danger',
        message: 'This permanently deletes "' + title + '" and all of its versions. This cannot be undone.',
        typeTo: 'DELETE',
        submitLabel: 'Purge forever',
        onConfirm: async () => {
          try {
            await apiCall('POST', '/api/test-cases/' + id + '/purge');
            window.PrimeQA.toast('Purged', 'success');
            btn.closest('tr')?.remove();
          } catch (_) { /* surfaced */ }
        },
      });
    });
  });

  /* Multi-select: shared between flat and grouped because both render
   * rows via the same macro. Re-query on demand so we handle HTMX swaps. */
  function allChecks() { return Array.from(document.querySelectorAll('.row-check')); }
  const bar = document.getElementById('bulk-bar');
  const countLabel = document.getElementById('bulk-count');
  const selectAll = document.getElementById('select-all');
  const groupChecks = Array.from(document.querySelectorAll('.group-check'));

  function refreshBar() {
    const n = allChecks().filter((c) => c.checked).length;
    countLabel.textContent = n;
    bar.classList.toggle('hidden', n === 0);
    groupChecks.forEach((gc) => {
      const scope = gc.closest('details');
      if (!scope) return;
      const rows = Array.from(scope.querySelectorAll('.row-check'));
      const all = rows.length > 0 && rows.every((r) => r.checked);
      gc.checked = all;
      gc.indeterminate = !all && rows.some((r) => r.checked);
    });
  }

  function wireRowChange() {
    allChecks().forEach((c) => c.addEventListener('change', refreshBar));
  }
  wireRowChange();

  if (selectAll) {
    selectAll.addEventListener('change', () => {
      allChecks().forEach((c) => { c.checked = selectAll.checked; });
      refreshBar();
    });
  }

  groupChecks.forEach((gc) => {
    gc.addEventListener('click', (e) => { e.stopPropagation(); });
    gc.addEventListener('change', () => {
      const scope = gc.closest('details');
      if (!scope) return;
      scope.querySelectorAll('.row-check').forEach((r) => { r.checked = gc.checked; });
      refreshBar();
    });
  });

  document.getElementById('bulk-clear')?.addEventListener('click', () => {
    allChecks().forEach((c) => { c.checked = false; });
    if (selectAll) selectAll.checked = false;
    refreshBar();
  });

  document.getElementById('bulk-delete')?.addEventListener('click', () => {
    const ids = allChecks().filter((c) => c.checked).map((c) => parseInt(c.value, 10));
    if (ids.length === 0) return;
    if (ids.length > 100) {
      window.PrimeQA.toast('Bulk actions are limited to 100 items', 'error');
      return;
    }
    window.PrimeQA.confirm({
      title: 'Move ' + ids.length + ' to trash?',
      variant: 'danger',
      message: 'Type DELETE to confirm moving ' + ids.length + ' test cases to trash.',
      typeTo: 'DELETE',
      submitLabel: 'Move to trash',
      onConfirm: async () => {
        try {
          await apiCall('POST', '/api/test-cases/bulk', {
            ids, action: 'soft_delete', confirm: 'DELETE',
          });
          window.PrimeQA.toast(ids.length + ' moved to trash', 'success');
          ids.forEach((id) => document.querySelector('tr[data-tc-id="' + id + '"]')?.remove());
        } catch (_) { /* surfaced */ }
      },
    });
  });

  document.getElementById('bulk-purge')?.addEventListener('click', () => {
    const ids = allChecks().filter((c) => c.checked).map((c) => parseInt(c.value, 10));
    if (ids.length === 0) return;
    if (ids.length > 100) {
      window.PrimeQA.toast('Bulk actions are limited to 100 items', 'error');
      return;
    }
    window.PrimeQA.confirm({
      title: 'Purge ' + ids.length + ' permanently?',
      variant: 'danger',
      message: 'Type DELETE to permanently purge ' + ids.length + ' test cases. This cannot be undone.',
      typeTo: 'DELETE',
      submitLabel: 'Purge forever',
      onConfirm: async () => {
        try {
          await apiCall('POST', '/api/test-cases/bulk/purge', {
            ids, confirm: 'DELETE',
          });
          window.PrimeQA.toast(ids.length + ' purged', 'success');
          ids.forEach((id) => document.querySelector('tr[data-tc-id="' + id + '"]')?.remove());
        } catch (_) { /* surfaced */ }
      },
    });
  });

  /* Grouped view: persist each requirement's open/closed state in
   * localStorage so the accordion doesn't snap shut between pages.
   * Default: open on first visit; otherwise honour stored value. */
  const GROUP_STATE_KEY = 'primeqa:test_library:groups_v1';
  function loadGroupState() {
    try { return JSON.parse(localStorage.getItem(GROUP_STATE_KEY) || '{}'); }
    catch (_) { return {}; }
  }
  function saveGroupState(state) {
    try { localStorage.setItem(GROUP_STATE_KEY, JSON.stringify(state)); } catch (_) { /* quota */ }
  }
  const state = loadGroupState();
  document.querySelectorAll('details[data-req-id]').forEach((det) => {
    const key = det.dataset.reqId;
    if (key in state) {
      det.open = !!state[key];
    } else {
      det.open = true;
    }
    det.addEventListener('toggle', () => {
      const s = loadGroupState();
      s[key] = det.open;
      saveGroupState(s);
      const chev = det.querySelector('.details-chevron');
      if (chev) chev.style.transform = det.open ? 'rotate(90deg)' : 'rotate(0deg)';
    });
    const chev = det.querySelector('.details-chevron');
    if (chev) chev.style.transform = det.open ? 'rotate(90deg)' : 'rotate(0deg)';
  });
})();
