/* Requirements list page — bulk-generate selection + row actions.
 * Extracted from templates/requirements/list.html (audit U6, 2026-04-19).
 *
 * DOM contract:
 *   #bulk-bar, #bulk-count, #bulk-env, #bulk-generate-btn, #bulk-clear-btn
 *   #bulk-gen-modal, #bulk-gen-running, #bulk-gen-done, #bulk-gen-status,
 *   #bulk-gen-summary, #bulk-gen-results, #bulk-gen-close
 *   input.req-check[value, data-req-key|data-req-title]
 *   button[data-delete-req, data-req-title]
 *   button[data-restore-req]
 *   button[data-purge-req, data-req-title]
 *
 * Depends on window.PrimeQA.{toast, confirm, showErrorFromResponse}.
 */

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/* ---- Bulk generate --------------------------------------------------- */
(function () {
  'use strict';
  const bar = document.getElementById('bulk-bar');
  if (!bar) return;

  const countEl = document.getElementById('bulk-count');
  const envSel = document.getElementById('bulk-env');
  const goBtn = document.getElementById('bulk-generate-btn');
  const clearBtn = document.getElementById('bulk-clear-btn');
  const modal = document.getElementById('bulk-gen-modal');
  const running = document.getElementById('bulk-gen-running');
  const done = document.getElementById('bulk-gen-done');
  const statusEl = document.getElementById('bulk-gen-status');
  const summaryEl = document.getElementById('bulk-gen-summary');
  const resultsEl = document.getElementById('bulk-gen-results');
  const closeBtn = document.getElementById('bulk-gen-close');

  function selected() {
    return [...document.querySelectorAll('.req-check:checked')];
  }

  function refresh() {
    const s = selected();
    countEl.textContent = s.length;
    bar.classList.toggle('hidden', s.length === 0);
    goBtn.disabled = s.length === 0 || !envSel.value;
  }

  document.querySelectorAll('.req-check').forEach((cb) => cb.addEventListener('change', refresh));
  envSel.addEventListener('change', refresh);

  clearBtn.addEventListener('click', () => {
    document.querySelectorAll('.req-check:checked').forEach((cb) => { cb.checked = false; });
    refresh();
  });

  goBtn.addEventListener('click', async () => {
    const picks = selected();
    if (!picks.length || !envSel.value) return;
    if (picks.length > 20) {
      window.PrimeQA?.toast?.('Select at most 20 per batch', 'warning');
      return;
    }

    const req_ids = picks.map((cb) => parseInt(cb.value, 10));
    const titles = Object.fromEntries(picks.map((cb) => [
      cb.value, cb.dataset.reqKey || cb.dataset.reqTitle || ('#' + cb.value),
    ]));

    running.classList.remove('hidden');
    done.classList.add('hidden');
    statusEl.textContent = 'Generating ' + req_ids.length + ' test case' +
      (req_ids.length === 1 ? '' : 's') + '\u2026';
    modal.classList.remove('hidden');
    goBtn.disabled = true;

    try {
      const r = await fetch('/api/requirements/bulk-generate', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          environment_id: parseInt(envSel.value, 10),
          requirement_ids: req_ids,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        statusEl.textContent = 'Bulk generate failed: ' + (body?.error?.message || r.statusText);
        return;
      }
      const body = await r.json();
      running.classList.add('hidden');
      done.classList.remove('hidden');

      const ok = body.results.filter((x) => x && x.status === 'ok').length;
      const err = body.results.filter((x) => x && x.status === 'error').length;
      const totalTc = body.results.reduce((a, rr) => a + (rr?.test_case_count || 0), 0);
      summaryEl.innerHTML = 'Generated <strong>' + totalTc + '</strong> test case' +
        (totalTc === 1 ? '' : 's') + ' across <strong>' + ok + '</strong> requirement' +
        (ok === 1 ? '' : 's') + (err ? ', <strong>' + err + '</strong> failed.' : '.');

      resultsEl.innerHTML = '';
      body.results.forEach((res) => {
        if (!res) return;
        const li = document.createElement('li');
        li.className = 'py-1 flex items-start space-x-2';
        const name = titles[res.requirement_id] || ('#' + res.requirement_id);
        if (res.status === 'ok') {
          const n = res.test_case_count || 0;
          const cov = (res.coverage_types || []).join(', ').replace(/_/g, ' ') || 'mixed';
          const link = res.test_case_id
            ? ' \u2014 <a href="/requirements/' + res.requirement_id +
              '" class="text-indigo-600 hover:underline">view on requirement</a>'
            : '';
          const sup = res.superseded_count
            ? ' \u00b7 ' + res.superseded_count + ' superseded'
            : '';
          li.innerHTML = '<span class="text-green-600">\u2713</span>' +
            '<span class="font-mono">' + escapeHtml(name) + '</span>' +
            '<span class="text-gray-500">\u2014 ' + n + ' test case' +
            (n === 1 ? '' : 's') + ' (' + cov + ')' + sup + link + '</span>';
        } else {
          li.innerHTML = '<span class="text-red-600">\u2717</span>' +
            '<span class="font-mono">' + escapeHtml(name) + '</span>' +
            '<span class="text-red-600">\u2014 ' + escapeHtml(res.error || 'unknown error') + '</span>';
        }
        resultsEl.appendChild(li);
      });
    } catch (e) {
      statusEl.textContent = 'Network error: ' + e;
    } finally {
      goBtn.disabled = false;
    }
  });

  closeBtn.addEventListener('click', () => {
    modal.classList.add('hidden');
    window.location.reload();
  });

  refresh();
})();

/* ---- Row delete / restore / purge ----------------------------------- */
(function () {
  'use strict';
  async function apiCall(method, url) {
    const r = await fetch(url, { method, credentials: 'same-origin' });
    if (!r.ok) { await window.PrimeQA.showErrorFromResponse(r); throw new Error('failed'); }
    return r;
  }

  document.querySelectorAll('[data-delete-req]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.deleteReq;
      const title = btn.dataset.reqTitle;
      window.PrimeQA.confirm({
        title: 'Move to trash?',
        variant: 'danger',
        message: '"' + title + '" will be moved to trash.',
        submitLabel: 'Move to trash',
        onConfirm: async () => {
          try {
            await apiCall('DELETE', '/api/requirements/' + id);
            window.PrimeQA.toast('Requirement moved to trash', 'success');
            btn.closest('li')?.remove();
          } catch (_) { /* surfaced */ }
        },
      });
    });
  });

  document.querySelectorAll('[data-restore-req]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try {
        await apiCall('POST', '/api/requirements/' + btn.dataset.restoreReq + '/restore');
        window.PrimeQA.toast('Restored', 'success');
        btn.closest('li')?.remove();
      } catch (_) { /* surfaced */ }
    });
  });

  document.querySelectorAll('[data-purge-req]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.purgeReq;
      const title = btn.dataset.reqTitle;
      window.PrimeQA.confirm({
        title: 'Permanently delete?',
        variant: 'danger',
        message: 'This permanently deletes "' + title + '". This cannot be undone.',
        typeTo: 'DELETE',
        submitLabel: 'Purge forever',
        onConfirm: async () => {
          try {
            await apiCall('POST', '/api/requirements/' + id + '/purge');
            window.PrimeQA.toast('Purged', 'success');
            btn.closest('li')?.remove();
          } catch (_) { /* surfaced */ }
        },
      });
    });
  });
})();
