/* Jira chip picker for the Requirements "Import from Jira" modal.
 *
 * Distinct from the Run Wizard's picker (different element IDs, different
 * submit target). Uses /api/jira/search via conn_id for multi-tenant.
 *
 * DOM contract:
 *   #import-jira-form, #import-jira-conn, #import-jira-search,
 *   #import-jira-results, #import-jira-chips, #import-jira-chips-placeholder,
 *   #import-chip-count, #import-jira-keys, #import-jira-submit,
 *   #import-jira-paste, #import-jira-paste-apply
 *
 * Depends on window.PrimeQA.toast (warning when MAX exceeded) and HTMX.
 */

(function () {
  'use strict';
  const form       = document.getElementById('import-jira-form');
  const connSel    = document.getElementById('import-jira-conn');
  const searchEl   = document.getElementById('import-jira-search');
  const resultsEl  = document.getElementById('import-jira-results');
  const chipsEl    = document.getElementById('import-jira-chips');
  const chipsPlace = document.getElementById('import-jira-chips-placeholder');
  const countEl    = document.getElementById('import-chip-count');
  const keysHidden = document.getElementById('import-jira-keys');
  const submitBtn  = document.getElementById('import-jira-submit');
  const pasteTa    = document.getElementById('import-jira-paste');
  const pasteApply = document.getElementById('import-jira-paste-apply');
  if (!form) return;

  const selected = new Map();
  const MAX = 50;

  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function render() {
    chipsEl.querySelectorAll('.chip').forEach((el) => el.remove());
    const keys = [...selected.keys()];
    chipsPlace.classList.toggle('hidden', keys.length > 0);
    keys.forEach((k) => {
      const info = selected.get(k);
      const chip = document.createElement('span');
      chip.className = 'chip inline-flex items-center space-x-1 px-2 py-0.5 rounded-md bg-indigo-100 text-indigo-800 text-xs';
      chip.title = info.summary || k;
      chip.innerHTML = '<span class="font-mono font-medium">' + k + '</span>' +
        (info.summary ? '<span class="max-w-[18rem] truncate">\u2014 ' + escapeHtml(info.summary) + '</span>' : '');
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.textContent = '\u00d7';
      rm.className = 'ml-1 text-indigo-600 hover:text-indigo-900 text-sm leading-none';
      rm.setAttribute('aria-label', 'Remove ' + k);
      rm.addEventListener('click', () => { selected.delete(k); render(); });
      chip.appendChild(rm);
      chipsEl.appendChild(chip);
    });
    keysHidden.value = keys.join(',');
    countEl.textContent = keys.length ? '(' + keys.length + ')' : '';
    submitBtn.disabled = keys.length === 0;
  }

  function add(info) {
    if (!info || !info.key) return;
    if (selected.has(info.key)) return;
    if (selected.size >= MAX) {
      window.PrimeQA?.toast?.('Max ' + MAX + ' tickets per import', 'warning');
      return;
    }
    selected.set(info.key, info);
    render();
  }

  function datasetToInfo(el) {
    /* Must match data-* attributes in templates/runs/_jira_search_results.html */
    return {
      key: el.dataset.key,
      summary: el.dataset.summary,
      status: el.dataset.status,
      issue_type: el.dataset.issueType,
      project_key: el.dataset.projectKey,
    };
  }

  resultsEl.addEventListener('click', (evt) => {
    const row = evt.target.closest('.jira-result');
    if (row) add(datasetToInfo(row));
  });
  resultsEl.addEventListener('keydown', (evt) => {
    const row = evt.target.closest('.jira-result');
    if (!row) return;
    if (evt.key === 'Enter' || evt.key === ' ') {
      evt.preventDefault();
      add(datasetToInfo(row));
    }
  });
  searchEl.addEventListener('keydown', (evt) => {
    if (evt.key === 'ArrowDown') {
      const first = resultsEl.querySelector('.jira-result');
      if (first) { evt.preventDefault(); first.focus(); }
    } else if (evt.key === 'Escape') {
      searchEl.value = '';
      resultsEl.innerHTML = '';
    }
  });

  connSel.addEventListener('change', () => {
    searchEl.value = '';
    resultsEl.innerHTML = '';
    if (window.htmx) window.htmx.trigger(searchEl, 'load');
  });

  pasteApply?.addEventListener('click', () => {
    const raw = (pasteTa.value || '').trim();
    if (!raw) return;
    raw.split(/[\s,]+/).filter(Boolean).forEach((k) => add({ key: k, summary: '' }));
    pasteTa.value = '';
  });

  render();
})();
