/* Phase 7: thumbs-up/down feedback on AI-generated test cases.
 *
 * Used on the TC detail page. Expects `components/feedback_modal.html` to
 * be included on the page (IDs `tcFeedbackModal`, `tcFeedbackModalForm`,
 * etc.). Expects `window.PrimeQA.toast` from the shared toast module.
 *
 * API:
 *   window.PrimeQA.submitThumbsUp(tcId)    — fires-and-forgets the 👍
 *   window.PrimeQA.openFeedbackModal(tcId) — opens the 👎 reason modal
 *   window.PrimeQA.closeFeedbackModal()
 *
 * Rate-limit: server throttles at 5 per TC / user / day. Throttled
 * responses arrive with {throttled: true} and we surface a neutral
 * info toast rather than an error.
 */

(function () {
  'use strict';
  window.PrimeQA = window.PrimeQA || {};

  function flash(kind, msg) {
    if (window.PrimeQA && window.PrimeQA.toast) {
      window.PrimeQA.toast(msg, kind);
    } else {
      /* Fallback so smoke tests without the toast module don't silently fail. */
      console.log('[feedback]', kind, msg);
    }
  }

  async function postFeedback(tcId, body) {
    const r = await fetch(`/api/test-cases/${tcId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* ignore */ }
    return { ok: r.ok, status: r.status, data };
  }

  window.PrimeQA.submitThumbsUp = async function (tcId) {
    const { ok, data } = await postFeedback(tcId, { verdict: 'up' });
    if (!ok) {
      flash('error', 'Could not submit feedback');
      return;
    }
    if (data.throttled) {
      flash('info', "Thanks — we've already noted your feedback today");
      return;
    }
    flash('success', 'Got it — we\u2019ll factor this into the next generation');
  };

  window.PrimeQA.openFeedbackModal = function (tcId) {
    const modal = document.getElementById('tcFeedbackModal');
    if (!modal) {
      console.error('tcFeedbackModal not included on this page');
      return;
    }
    document.getElementById('tcFeedbackModalTcId').value = tcId;
    document.getElementById('tcFeedbackModalText').value = '';
    document.getElementById('tcFeedbackModalError').classList.add('hidden');
    /* Clear previously selected radio */
    modal.querySelectorAll('input[name=reason]').forEach(r => { r.checked = false; });
    modal.classList.remove('hidden');
    /* Focus trap: move focus into the modal */
    setTimeout(() => {
      const first = modal.querySelector('input[type=radio]');
      if (first) first.focus();
    }, 10);
  };

  window.PrimeQA.closeFeedbackModal = function () {
    const modal = document.getElementById('tcFeedbackModal');
    if (modal) modal.classList.add('hidden');
  };

  /* Form submission handler — bind on DOMContentLoaded so it survives
   * HTMX partial replaces on the TC detail page. */
  document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('tcFeedbackModalForm');
    if (!form) return;
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      const tcId = document.getElementById('tcFeedbackModalTcId').value;
      const reason = (form.querySelector('input[name=reason]:checked') || {}).value || null;
      const reasonText = document.getElementById('tcFeedbackModalText').value.trim();
      const err = document.getElementById('tcFeedbackModalError');
      err.classList.add('hidden');

      if (reason === 'other' && !reasonText) {
        err.textContent = "Please describe the issue — details are required for 'Other'.";
        err.classList.remove('hidden');
        return;
      }
      const submitBtn = document.getElementById('tcFeedbackModalSubmit');
      submitBtn.disabled = true;
      submitBtn.textContent = 'Submitting…';
      try {
        const { ok, data } = await postFeedback(tcId, {
          verdict: 'down',
          reason: reason,
          reason_text: reasonText || null,
        });
        if (!ok) {
          const msg = (data && data.error && data.error.message) || 'Could not submit feedback';
          err.textContent = msg;
          err.classList.remove('hidden');
          return;
        }
        window.PrimeQA.closeFeedbackModal();
        if (data.throttled) {
          flash('info', "Thanks — we've already noted your feedback today");
        } else {
          flash('success', 'Got it — the next generation will factor this in');
        }
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit feedback';
      }
    });
  });
})();
