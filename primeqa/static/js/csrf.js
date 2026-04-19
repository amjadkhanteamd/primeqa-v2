/* Auto-inject the CSRF token into fetch() calls for state-changing
 * methods. Double-submit cookie pattern: read `csrf_token` cookie,
 * echo it via `X-CSRF-Token` header.
 *
 * Include in base.html once so every page picks it up. Must load
 * BEFORE any module that uses fetch() (e.g. tc_library.js, confirm.js).
 *
 * Leaves cross-origin fetches alone — only same-origin state-changers
 * get the header. That's the intent: our own server requires the
 * header for non-GET; third-party APIs don't.
 */

(function () {
  'use strict';

  function readCookie(name) {
    const m = document.cookie.match('(?:^|;)\\s*' + name + '=([^;]+)');
    return m ? decodeURIComponent(m[1]) : null;
  }

  const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
  const origFetch = window.fetch.bind(window);

  window.fetch = function (input, init) {
    init = init || {};
    const method = (init.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();

    if (!UNSAFE.has(method)) return origFetch(input, init);

    const url = typeof input === 'string' ? input : input.url;
    const isSameOrigin = !url.match(/^https?:/) || url.startsWith(window.location.origin);
    if (!isSameOrigin) return origFetch(input, init);

    const token = readCookie('csrf_token');
    if (!token) return origFetch(input, init);

    const headers = new Headers(init.headers || {});
    if (!headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', token);
    }
    init.headers = headers;

    return origFetch(input, init);
  };
})();
