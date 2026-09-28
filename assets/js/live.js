/*
 * Live game screens: the few things htmx attributes cannot do under a CSP without eval.
 *
 * - A failed request (no network, timeout, server error) shows a red banner with a retry. The
 *   retry sends the same request again, with the same idempotency key, so an action that did
 *   reach the server is not recorded twice.
 * - The board's periodic refresh is skipped while the banner is up, a sheet is open or an input
 *   has focus, so it never replaces what the organizer is typing.
 * - Quick amount buttons fill an input; the bottom sheet closes on its backdrop, "×" or Escape.
 * - The blind timer on the board counts its seconds down from what the server rendered and
 *   refreshes the board when the level runs out.
 * - "Поделиться табло" opens the phone's share sheet with the display link (Web Share API);
 *   without it, the link is copied and a toast says so.
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

  // The timer strip: remaining seconds as rendered, counted down from when it appeared.
  function tickTimer() {
    const strip = document.querySelector("[data-timer]");
    if (!strip || strip.hasAttribute("data-stopped")) {
      return;
    }
    strip.shownAt ??= performance.now();
    const remaining = Number(strip.dataset.remaining) * 1000 - (performance.now() - strip.shownAt);
    strip.querySelector("[data-clock]").textContent = window.BlindClock.format(remaining);
    const progress = strip.querySelector("[data-progress]");
    progress.value = Math.min(progress.max, progress.max - Math.max(remaining, 0) / 1000);
    if (remaining <= 0 && !strip.ended) {
      strip.ended = true;
      const board = document.getElementById("board");
      if (board) {
        htmx.trigger(board, "timer-end");
      }
    }
  }

  if (window.BlindClock) {
    setInterval(tickTimer, 500);
  }

  // The same toast as the server's (see live/_toast.html); CSS fades it out.
  function toast(text, warning = false) {
    const box = document.createElement("div");
    box.className = warning ? "toast toast-warn" : "toast";
    box.setAttribute("role", "status");
    const span = document.createElement("span");
    span.textContent = text;
    box.append(span);
    document.getElementById("toast").replaceChildren(box);
  }

  async function share(button) {
    const url = button.dataset.share;
    const data = { title: button.dataset.shareTitle, url };
    if (navigator.share && (!navigator.canShare || navigator.canShare(data))) {
      try {
        await navigator.share(data);
        return;
      } catch (error) {
        if (error.name === "AbortError") {
          return; // the organizer closed the share sheet
        }
      }
    }
    try {
      await navigator.clipboard.writeText(url);
      toast("Ссылка на табло скопирована.");
    } catch {
      const input = document.getElementById("display-link");
      input?.select(); // no clipboard either: leave it selected for a manual copy
      toast("Скопируйте ссылку из поля «Табло».", true);
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-share]");
    if (button) {
      share(button);
    }
  });
})();
