// PrimeQA.toast(message, kind)
//   kind ∈ {"success","error","warning","info"}
// Also listens for Flask flashes on window load and HTMX trigger events:
//   - response header `HX-Trigger: toast` with JSON body
//     {"message":"...", "kind":"success"}
(function () {
  const COLORS = {
    success: "bg-green-50 text-green-800 border-green-200",
    error:   "bg-red-50 text-red-800 border-red-200",
    warning: "bg-yellow-50 text-yellow-800 border-yellow-200",
    info:    "bg-blue-50 text-blue-800 border-blue-200",
  };

  function ensureStack() {
    let stack = document.getElementById("toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "toast-stack";
      stack.className = "fixed top-4 right-4 z-[60] flex flex-col space-y-2 w-80 max-w-full";
      stack.setAttribute("aria-live", "polite");
      stack.setAttribute("aria-atomic", "true");
      document.body.appendChild(stack);
    }
    return stack;
  }

  function toast(message, kind) {
    if (!message) return;
    kind = kind || "info";
    const stack = ensureStack();
    const el = document.createElement("div");
    el.className =
      "flex items-start justify-between rounded-lg px-4 py-3 text-sm shadow border " +
      (COLORS[kind] || COLORS.info);
    el.setAttribute("role", "status");
    el.innerHTML =
      '<div class="flex-1 pr-3 break-words">' +
      (message + "").replace(/</g, "&lt;") +
      "</div>" +
      '<button aria-label="Dismiss" class="text-gray-400 hover:text-gray-600">&times;</button>';
    el.querySelector("button").addEventListener("click", () => el.remove());
    stack.appendChild(el);
    setTimeout(() => el.remove(), kind === "error" ? 8000 : 4500);
  }

  // Render Flask flashes that base.html stashed on window.__primeqaFlash
  document.addEventListener("DOMContentLoaded", function () {
    if (Array.isArray(window.__primeqaFlash)) {
      window.__primeqaFlash.forEach(([cat, msg]) => {
        const kind = cat === "error" || cat === "warning" || cat === "success"
                     ? cat : "info";
        toast(msg, kind);
      });
    }
  });

  // HTMX integration: listen for `toast` custom event, parse JSON detail
  document.body.addEventListener("toast", function (evt) {
    const d = evt.detail || {};
    toast(d.message || d.value || "", d.kind || "info");
  });

  // Convenience for API fetches: parse {"error":{"code","message"}} envelopes
  window.PrimeQA = window.PrimeQA || {};
  window.PrimeQA.toast = toast;
  window.PrimeQA.showErrorFromResponse = async function (resp) {
    try {
      const body = await resp.clone().json();
      const msg = (body && body.error && body.error.message) || resp.statusText;
      toast(msg, "error");
    } catch (_) {
      toast(resp.statusText || "Request failed", "error");
    }
  };
})();
