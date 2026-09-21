/* Requirements list + detail — row delete / restore / purge actions.
 * Extracted from templates/requirements/list.html (audit U6, 2026-04-19).
 *
 * DOM contract:
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

/* ---- Bulk generate: DELETED (round 3, AUD-005) ----------------------
 * The v1 per-row and bulk generate affordances were removed from the list
 * template in D-165; this block kept calling POST /api/requirements/bulk-generate,
 * a route retired with the v1 model layer (7fd518f, D-221.4). No element it
 * named has existed in the template since — it was inert code shipped on every
 * requirements-list render, and the dead-link sweep could not see it until
 * AUD-044 taught the sweep about converters. Row actions below are live.
 */

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
