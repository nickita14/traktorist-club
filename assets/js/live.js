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
 * - The start form hides the blind timer field while "Кэш" is chosen (a tournament-only field);
 *   without this script it stays visible and the server ignores it for cash.
 * - "Поделиться табло" opens the phone's share sheet with the display link (Web Share API);
 *   without it, the link is copied and a toast says so.
 * - A toast goes away on "×", on Escape while focus is inside it, on a sideways swipe and at the
 *   end of its fade. Each removes the element, so it never catches a tap afterwards. A drag that
 *   starts on a toast never clicks "отменить" or whatever lies under it.
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
    if (event.key !== "Escape") {
      return;
    }
    const box = document.activeElement?.closest(".toast");
    if (box) {
      box.remove(); // the toast first: one Escape closes one thing
    } else if (sheet().childElementCount > 0) {
      closeSheet();
    }
  });

  // The start form: the timer field only for a tournament. Also after an htmx swap (the form
  // comes back with its errors) and on pageshow (a form restored from the back/forward cache).
  function syncKind() {
    for (const form of document.querySelectorAll("[data-kind-form]")) {
      const cash = form.querySelector('input[name="kind"][value="cash"]:checked') !== null;
      for (const field of form.querySelectorAll("[data-tour-only]")) {
        field.hidden = cash;
      }
    }
  }

  document.addEventListener("change", (event) => {
    if (event.target.closest("[data-kind-form]")) {
      syncKind();
    }
  });
  document.addEventListener("htmx:afterSwap", syncKind);
  window.addEventListener("pageshow", syncKind);
  syncKind();

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
    span.className = "toast-text";
    span.textContent = text;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.dataset.toastClose = "";
    close.setAttribute("aria-label", "Закрыть");
    close.textContent = "×";
    box.append(span, close);
    document.getElementById("toast").replaceChildren(box);
  }

  // Dismissing a toast removes it (the #toast container stays for the next hx-swap-oob).
  const SLOP = 10; // px: a smaller move is still a tap
  const SWIPE = 96; // px, or 35% of the toast's width when that is less
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let drag = null; // the gesture that started on a toast: pointer, start, how it moved
  let swallowClick = false; // true only between a drag's pointerup and the end of that task

  function dismissToast(box, direction) {
    if (!direction || reducedMotion.matches) {
      box.remove();
      return;
    }
    box.classList.add("toast-leaving");
    box.addEventListener("transitionend", () => box.remove(), { once: true });
    setTimeout(() => box.remove(), 400); // no transitionend when nothing moved
    box.style.transform = `translateX(${direction * window.innerWidth}px)`;
  }

  document.addEventListener("pointerdown", (event) => {
    swallowClick = false;
    drag = null;
    const box = event.target.closest(".toast");
    if (box && event.isPrimary && event.button === 0) {
      drag = {
        box,
        id: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        dx: 0,
        moved: false,
        horizontal: false,
      };
    }
  });

  document.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) {
      return;
    }
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved) {
      if (Math.hypot(dx, dy) < SLOP) {
        return;
      }
      drag.moved = true;
      drag.horizontal = Math.abs(dx) > Math.abs(dy);
      if (drag.horizontal) {
        drag.box.setPointerCapture(event.pointerId);
        drag.box.classList.add("toast-dragging");
      }
    }
    if (drag.horizontal) {
      drag.dx = dx;
      drag.box.style.transform = `translateX(${dx}px)`;
    }
  });

  function endDrag(event) {
    if (event.type === "pointercancel") {
      swallowClick = false; // the browser took the gesture (a scroll): no click follows
    }
    if (!drag || event.pointerId !== drag.id) {
      return;
    }
    const { box, moved, horizontal, dx } = drag;
    drag = null;
    if (!moved) {
      return; // a tap: its click goes through ("отменить", "×")
    }
    box.classList.remove("toast-dragging");
    if (event.type === "pointercancel") {
      box.style.transform = "";
      return;
    }
    // Only the click this pointerup may produce, dispatched in the same task.
    swallowClick = true;
    setTimeout(() => {
      swallowClick = false;
    }, 0);
    if (horizontal && Math.abs(dx) >= Math.min(SWIPE, box.offsetWidth * 0.35)) {
      dismissToast(box, Math.sign(dx));
    } else {
      box.style.transform = "";
    }
  }

  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);

  window.addEventListener(
    "click",
    (event) => {
      if (swallowClick) {
        swallowClick = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const close = event.target.closest("[data-toast-close]");
      if (close) {
        close.closest(".toast")?.remove();
      }
    },
    true,
  );

  document.addEventListener("animationend", (event) => {
    if (event.animationName === "toast-life" && event.target.matches(".toast")) {
      event.target.remove();
    }
  });

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
