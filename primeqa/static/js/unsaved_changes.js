// Warn the user if they try to leave a form with unsaved changes.
// Opt-in via <form data-unsaved-guard>…</form>.
(function () {
  const FORMS = new WeakSet();
  let dirty = false;

  function onBeforeUnload(e) {
    if (!dirty) return;
    e.preventDefault();
    e.returnValue = "";
    return "";
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form[data-unsaved-guard]").forEach(attach);
    // Expose programmatically so edit pages can flag dirty from HTMX too
    window.PrimeQA = window.PrimeQA || {};
    window.PrimeQA.markDirty = () => { dirty = true; };
    window.PrimeQA.markClean = () => { dirty = false; };
  });

  function attach(form) {
    if (FORMS.has(form)) return;
    FORMS.add(form);
    form.addEventListener("input", () => { dirty = true; });
    form.addEventListener("change", () => { dirty = true; });
    form.addEventListener("submit", () => { dirty = false; });
    window.addEventListener("beforeunload", onBeforeUnload);
  }
})();
