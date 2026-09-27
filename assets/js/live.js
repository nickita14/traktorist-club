/*
 * Live game screens: the few things htmx attributes cannot do under a CSP without eval.
 *
 * - A failed request (no network, timeout, server error) shows a red banner with a retry. The
 *   retry sends the same request again, with the same idempotency key, so an action that did
 *   reach the server is not recorded twice.
 * - The board's periodic refresh is skipped while the banner is up, a sheet is open or an input
 *   has focus, so it never replaces what the organizer is typing.
 * - Quick amount buttons fill an input; the bottom sheet closes on its backdrop, "×" or Escape.
 */
(() => {
  "use strict";

  let failed = null;

  const banner = () => document.getElementById("conn-error");
  const sheet = () => document.getElementById("sheet");

  function showError(detail, text) {
    const config = detail.requestConfig;
    failed = config
      ? {
          verb: config.verb,
          path: config.path,
          source: config.elt,
          target: config.target,
          values: Object.fromEntries(config.formData || []),
        }
      : null;
    banner().querySelector("[data-conn-text]").textContent = text;
    banner().hidden = false;
  }

  const NO_ANSWER =
    "Нет связи с сервером. Нажмите «Повторить»: дважды действие не запишется.";
  document.addEventListener("htmx:sendError", (event) => showError(event.detail, NO_ANSWER));
  document.addEventListener("htmx:timeout", (event) => showError(event.detail, NO_ANSWER));
  document.addEventListener("htmx:responseError", (event) => {
    const status = event.detail.xhr.status;
    const text =
      status === 403
        ? "Нет прав на это действие. Войдите как организатор."
        : `Ошибка сервера (${status}). Действие не записано.`;
    showError(event.detail, text);
  });

  document.addEventListener("htmx:afterRequest", (event) => {
    if (event.detail.successful) {
      banner().hidden = true;
      failed = null;
    }
  });

  function retry() {
    banner().hidden = true;
    if (!failed || !failed.source.isConnected) {
      window.location.reload();
      return;
    }
    const { verb, path, source, target, values } = failed;
    failed = null;
    htmx.ajax(verb, path, { source, target, values });
  }

  function typing() {
    const active = document.activeElement;
    return Boolean(active && active.matches("input, textarea, select"));
  }

  document.addEventListener("htmx:beforeRequest", (event) => {
    if (!event.detail.elt.hasAttribute("data-poll")) {
      return;
    }
    if (!banner().hidden || sheet().childElementCount > 0 || typing()) {
      event.preventDefault();
    }
  });

  function closeSheet() {
    sheet().replaceChildren();
  }

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (target.closest("[data-retry]")) {
      retry();
    } else if (target.closest("[data-close-sheet]")) {
      closeSheet();
    } else if (target.closest("[data-fill]")) {
      const button = target.closest("[data-fill]");
      const input = document.querySelector(button.dataset.fill);
      if (input) {
        input.value = button.dataset.value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sheet().childElementCount > 0) {
      closeSheet();
    }
  });
})();
