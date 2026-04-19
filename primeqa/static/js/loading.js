/* Async-action loading helper (audit fix #15, 2026-04-19).
 *
 * Auto-disables any <form> submit button + any <button data-async-action>
 * while a click / submit is in flight, then re-enables on
 * resolve/reject. Prevents double-click dup submits + gives the user a
 * visible "working on it" signal.
 *
 * How it works:
 *   - <form> submits: we listen for submit in the CAPTURE phase, disable
 *     the inner submit button, add `aria-busy="true"`, show a spinner
 *     span if present. Re-enable after 10s as a safety net (in case
 *     the response redirect doesn't land).
 *   - <button data-async-action>: we wrap the button's onclick so that
 *     after it's invoked, the button disables until the next tick +
 *     any returned Promise resolves.
 *
 * Opt-out: `<form data-no-loading>` or `<button data-no-loading>` skips
 * the wrapping. Useful for idempotent actions (filter forms) where
 * double-submit isn't harmful.
 */

(function () {
  'use strict';

  const BUSY_CLASSES = ['opacity-60', 'cursor-not-allowed'];
  const SAFETY_UNLOCK_MS = 10_000;

  function lock(el) {
    if (!el || el.disabled) return;
    el.disabled = true;
    el.setAttribute('aria-busy', 'true');
    BUSY_CLASSES.forEach(c => el.classList.add(c));
    // Optional spinner: if there's a [data-spinner] sibling inside,
    // reveal it.
    const spinner = el.querySelector('[data-spinner]');
    if (spinner) spinner.classList.remove('hidden');
    // Safety unlock — if the redirect never lands (network death etc.),
    // don't strand the user with a dead button.
    setTimeout(() => unlock(el), SAFETY_UNLOCK_MS);
  }

  function unlock(el) {
    if (!el) return;
    el.disabled = false;
    el.removeAttribute('aria-busy');
    BUSY_CLASSES.forEach(c => el.classList.remove(c));
    const spinner = el.querySelector('[data-spinner]');
    if (spinner) spinner.classList.add('hidden');
  }

  /* Form submit listener. Capture phase so we fire before any onsubmit
   * handler rejects (and we only lock if submission proceeds). */
  document.addEventListener('submit', function (ev) {
    const form = ev.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.hasAttribute('data-no-loading')) return;
    // Skip if preventDefault was already called (custom handler)
    if (ev.defaultPrevented) return;
    const submitBtn = form.querySelector(
      'button[type=submit]:not([data-no-loading])'
    );
    if (submitBtn) lock(submitBtn);
  }, true);

  /* Button-triggered async actions (outside forms). */
  document.addEventListener('click', function (ev) {
    const btn = ev.target.closest('button[data-async-action]');
    if (!btn) return;
    if (btn.hasAttribute('data-no-loading')) return;
    lock(btn);
    // If the click handler returned a Promise, unlock on settle.
    // Otherwise use the safety timer.
    queueMicrotask(() => {
      const pending = btn._pqa_pending;
      if (pending && typeof pending.finally === 'function') {
        pending.finally(() => unlock(btn));
      }
    });
  });

  /* Public API: primeQA code calling fetch() can mark its button busy
   * by stashing the promise on btn._pqa_pending before returning. */
  window.PrimeQA = window.PrimeQA || {};
  window.PrimeQA.lockButton = lock;
  window.PrimeQA.unlockButton = unlock;
})();
