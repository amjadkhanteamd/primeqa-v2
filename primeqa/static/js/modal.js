/* Modal behaviour — open, close, focus trap, Escape (audit UI #13, 2026-04-19).
 *
 * Public API:
 *   window.PrimeQA.openModal(id)
 *   window.PrimeQA.closeModal(id)
 *   window.PrimeQA.closeAllModals()
 *
 * Any clickable element with [data-open-modal="<id>"] is auto-wired.
 * Any element with [data-modal-close] inside a [role=dialog] closes
 * the containing modal.
 *
 * Does NOT interfere with existing hand-rolled modals that use
 * classList.toggle('hidden') directly. Templates that migrate to
 * `{% call modal_shell(id=...) %}` get:
 *   - role="dialog" + aria-modal + aria-labelledby
 *   - focus trap (Tab wraps within modal)
 *   - first-focusable autofocus on open
 *   - Escape to close
 *   - overlay-click to close
 *   - body scroll lock
 */

(function () {
  'use strict';

  const FOCUSABLE = [
    'a[href]:not([disabled])',
    'button:not([disabled])',
    'textarea:not([disabled])',
    'input:not([disabled]):not([type=hidden])',
    'select:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  const _openStack = [];
  let _lastFocus = null;

  function _focusables(modal) {
    return Array.from(modal.querySelectorAll(FOCUSABLE))
      .filter(el => el.offsetParent !== null);
  }

  function _trap(ev) {
    if (ev.key !== 'Tab' || !_openStack.length) return;
    const top = _openStack[_openStack.length - 1];
    const focs = _focusables(top);
    if (!focs.length) return;
    const first = focs[0], last = focs[focs.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault(); first.focus();
    }
  }

  function _onKey(ev) {
    if (ev.key === 'Escape' && _openStack.length) {
      closeModal(_openStack[_openStack.length - 1]);
    } else if (ev.key === 'Tab') {
      _trap(ev);
    }
  }

  function openModal(idOrEl) {
    const m = typeof idOrEl === 'string' ? document.getElementById(idOrEl) : idOrEl;
    if (!m) return;
    m.classList.remove('hidden');
    _openStack.push(m);
    _lastFocus = document.activeElement;
    document.body.style.overflow = 'hidden';
    setTimeout(() => {
      const focs = _focusables(m);
      if (focs.length) focs[0].focus();
    }, 10);
  }

  function closeModal(idOrEl) {
    const m = typeof idOrEl === 'string' ? document.getElementById(idOrEl) : idOrEl;
    if (!m) return;
    m.classList.add('hidden');
    const idx = _openStack.indexOf(m);
    if (idx >= 0) _openStack.splice(idx, 1);
    if (!_openStack.length) {
      document.body.style.overflow = '';
      if (_lastFocus && document.contains(_lastFocus)) {
        _lastFocus.focus();
      }
    }
  }

  function closeAllModals() {
    [..._openStack].forEach(m => closeModal(m));
  }

  /* Click delegation. */
  document.addEventListener('click', function (ev) {
    const opener = ev.target.closest('[data-open-modal]');
    if (opener) {
      ev.preventDefault();
      openModal(opener.dataset.openModal);
      return;
    }
    const closer = ev.target.closest('[data-modal-close]');
    if (closer) {
      const modal = closer.closest('[role=dialog]');
      if (modal) closeModal(modal);
    }
  });

  document.addEventListener('keydown', _onKey);

  window.PrimeQA = window.PrimeQA || {};
  window.PrimeQA.openModal = openModal;
  window.PrimeQA.closeModal = closeModal;
  window.PrimeQA.closeAllModals = closeAllModals;
})();
