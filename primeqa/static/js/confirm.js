// Confirmation modal — opens whenever a button with `data-confirm` is clicked.
// Supports typed-confirmation ("type DELETE") and form-submit handoff.
//
// Also exposes PrimeQA.confirm(opts) for JS-driven flows.
(function () {
  const modal = () => document.getElementById("confirm-modal");

  function setVariant(submit, variant) {
    submit.classList.remove(...submit.dataset.variantDefault.split(" "));
    submit.classList.remove(...submit.dataset.variantDanger.split(" "));
    const v = variant === "danger" ? submit.dataset.variantDanger
                                   : submit.dataset.variantDefault;
    submit.classList.add(...v.split(" "));
  }

  function openModal(opts) {
    const m = modal();
    if (!m) return;
    m.querySelector("[data-confirm-title-slot]").textContent = opts.title || "Confirm";
    m.querySelector("[data-confirm-message-slot]").textContent = opts.message || "Are you sure?";

    const submit = m.querySelector("[data-confirm-submit]");
    submit.textContent = opts.submitLabel || "Confirm";
    setVariant(submit, opts.variant || "default");

    const typeWrap = m.querySelector("[data-confirm-type-wrap]");
    const typeInput = m.querySelector("[data-confirm-type-input]");
    const typeExpected = m.querySelector("[data-confirm-type-expected]");
    if (opts.typeTo) {
      typeWrap.classList.remove("hidden");
      typeExpected.textContent = opts.typeTo;
      typeInput.value = "";
      submit.disabled = true;
      submit.classList.add("opacity-50", "cursor-not-allowed");
      typeInput.oninput = () => {
        const ok = typeInput.value === opts.typeTo;
        submit.disabled = !ok;
        submit.classList.toggle("opacity-50", !ok);
        submit.classList.toggle("cursor-not-allowed", !ok);
      };
    } else {
      typeWrap.classList.add("hidden");
      submit.disabled = false;
      submit.classList.remove("opacity-50", "cursor-not-allowed");
    }

    submit.onclick = () => {
      closeModal();
      if (typeof opts.onConfirm === "function") {
        opts.onConfirm();
      } else if (opts.formSelector) {
        const form = document.querySelector(opts.formSelector);
        if (form) form.submit();
      }
    };

    m.classList.remove("hidden");
    // Focus trap + keyboard
    const focusable = m.querySelectorAll("button, input");
    if (focusable.length) focusable[0].focus();
    document.addEventListener("keydown", onKey);
  }

  function closeModal() {
    const m = modal();
    if (!m) return;
    m.classList.add("hidden");
    document.removeEventListener("keydown", onKey);
  }

  function onKey(e) {
    if (e.key === "Escape") closeModal();
  }

  // Delegate clicks on [data-confirm] and [data-confirm-close]
  document.addEventListener("click", function (e) {
    const trigger = e.target.closest("[data-confirm]");
    if (trigger) {
      e.preventDefault();
      openModal({
        title: trigger.dataset.confirmTitle || "Confirm",
        message: trigger.dataset.confirmMessage || "Are you sure?",
        variant: trigger.dataset.confirmVariant || "default",
        submitLabel: trigger.dataset.confirmSubmit || "Confirm",
        typeTo: trigger.dataset.confirmTypeTo || null,
        formSelector: trigger.dataset.confirmForm || null,
        onConfirm: null,
      });
      return;
    }
    if (e.target.closest("[data-confirm-close]")) {
      closeModal();
    }
  });

  window.PrimeQA = window.PrimeQA || {};
  window.PrimeQA.confirm = openModal;
  window.PrimeQA.closeConfirm = closeModal;
})();
