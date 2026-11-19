/*
 * The blind timer display (табло).
 *
 * - Counts seconds in the browser with window.BlindClock and resyncs with the server every
 *   data-sync-seconds. The server's clock wins: each sync measures the offset between the
 *   device and the server (the server's "now" against the middle of the request), and the
 *   display counts on a monotonic clock (performance.now) shifted by it.
 * - A failed sync keeps the local clock running, through the levels it already has, and shows a
 *   small "нет связи" mark until a sync succeeds again.
 * - Organizer controls (only when data-act-url is set): buttons and the space bar. Every press
 *   has its own idempotency key; a press that got no answer is sent again with the same key.
 * - A short beep a minute before a level ends and at the level change (Web Audio, after the
 *   first tap: browsers block sound until a gesture), and the Screen Wake Lock where supported.
 * - Theme nights (live.themes): each sync passes the last action seen (data-event-cursor, the
 *   latest one when the page was rendered, so nothing is replayed) and gets the table events
 *   since and the theme. Events play one after another in the overlay, each for its seconds; a
 *   winner with 0 seconds stays, until a sync shows more than one player in again (an undo) or
 *   the game ends (the display stops, the winner stays). A theme sound plays only once sound is
 *   on; without it the overlay still shows. During a break with the theme's break event on, the
 *   break screen rotates the theme's tips.
 * - The theme preview (data-preview): no cursor; the buttons play the server's sample events.
 */
(() => {
  "use strict";

  const body = document.body;
  const clockApi = window.BlindClock;
  const find = (name) => document.querySelector(`[data-${name}]`);
  const WARN_AT = 60 * 1000;

  let state = null;
  let offset = 0; // server epoch ms minus local epoch ms
  let rendered = null; // what the last frame showed, to spot changes
  let busy = false;

  const preview = "preview" in body.dataset;
  const TIP_MS = Number(body.dataset.tipSeconds || 12) * 1000;
  let cursor = body.dataset.eventCursor || "0";
  let theme = null;
  let samples = {};
  let scene = ""; // preview only: "break" shows the sample break
  const queue = [];
  let showing = null; // { event, persistent, timer }
  let breakScreen = false;
  let tipIndex = 0;
  let tipShownAt = 0;

  const localNow = () => performance.timeOrigin + performance.now();
  const serverNow = () => localNow() + offset;

  function show(name, visible) {
    const element = find(name);
    if (element) {
      element.hidden = !visible;
    }
  }

  function text(name, value) {
    const element = find(name);
    if (element && element.textContent !== value) {
      element.textContent = value;
    }
  }

  const blinds = (row) => `${clockApi.money(row.small)} / ${clockApi.money(row.big)}`;

  // Drawing one frame.

  function render() {
    if (!state) {
      return;
    }
    const now = serverNow();
    const clock = clockApi.clockAt(state.levels, state.position, state.started, state.paused, now);
    const row = clock.current;

    text("clock", clockApi.format(clock.remaining));
    const progress = find("progress");
    progress.max = row.duration / 1000;
    progress.value = Math.min((row.duration - clock.remaining) / 1000, row.duration / 1000);
    body.classList.toggle("tablo-is-paused", clock.paused);
    body.classList.toggle("tablo-is-break", row.isBreak);
    show("paused-stamp", clock.paused);
    show("finished", clock.finished);

    text(
      "level-line",
      row.isBreak
        ? `${row.label} · ${row.minutes} мин`
        : `Уровень ${row.number} · из ${clock.levelCount} · ${row.minutes} мин`,
    );
    show("blinds", !row.isBreak);
    show("break", row.isBreak);
    const next = clock.nextLevel;
    if (row.isBreak) {
      text("break-label", row.label);
      text("break-clock", clockApi.format(clock.remaining));
      show("break-next", Boolean(next));
      text("break-next-value", next ? blinds(next) : "");
    } else {
      text("blinds-now", blinds(row));
      show("ante-now", row.ante > 0);
      text("ante-now-value", clockApi.money(row.ante));
      show("next", Boolean(next));
      text("blinds-next", next ? blinds(next) : "");
      show("ante-next", Boolean(next && next.ante > 0));
      text("ante-next-value", next ? clockApi.money(next.ante) : "");
    }

    // Right column. No break left: no "до перерыва" at all.
    const pause = clock.nextBreak;
    show("until-break-block", Boolean(pause));
    if (pause) {
      text("until-break", clockApi.format(clock.untilBreak));
      const before = clock.levelBefore(pause);
      const parts = [];
      if (before) {
        parts.push(`после ${before.number}-го уровня`);
      }
      if (pause.addon) {
        parts.push("аддон");
      }
      text("until-break-note", parts.join(" · "));
    }
    const open = state.stage === "rebuys";
    text("rebuys", open ? "Открыты" : "Закрыты");
    text(
      "rebuys-note",
      open && clock.rebuysUntil ? `до конца ${clock.rebuysUntil.number}-го уровня` : "",
    );
    text("players", `${state.players} / ${state.total}`);
    text("bank", clockApi.money(state.bank));
    const toggle = document.querySelector('[data-act="toggle"]');
    if (toggle) {
      toggle.textContent = clock.paused ? "Продолжить" : "Пауза";
    }

    sounds(clock);
    breakFrame(clock, Boolean(rendered));
    rendered = { position: row.position, remaining: clock.remaining, finished: clock.finished };
  }

  // Theme nights.

  const overlay = find("overlay");
  const config = (kind) => theme?.events?.[kind] || null;

  // "{name} · {place} место" with the values; "{{" and "}}" are the braces themselves.
  function substitute(template, event) {
    const values = {
      name: event.name ?? "",
      place: event.place == null ? "" : String(event.place),
      amount: event.amount == null ? "" : clockApi.money(event.amount),
      count: event.count == null ? "" : String(event.count),
    };
    return (template || "").replace(/\{\{|\}\}|\{(\w+)\}/g, (match, key) => {
      if (match === "{{") {
        return "{";
      }
      if (match === "}}") {
        return "}";
      }
      return values[key] ?? "";
    });
  }

  // Text into an element, its figures in PT Mono (as the |figures filter does on the site).
  function figures(name, value) {
    const element = find(name);
    if (!element) {
      return;
    }
    const parts = value.split(/(\d(?:[\d\u00a0]*\d)?)/).filter(Boolean);
    element.replaceChildren(
      ...parts.map((part) => {
        if (!/^\d/.test(part)) {
          return document.createTextNode(part);
        }
        const span = document.createElement("span");
        span.className = "tablo-overlay-num";
        span.textContent = part;
        return span;
      }),
    );
    element.hidden = value === "";
  }

  function setStyle(style) {
    for (const name of [...body.classList]) {
      if (name.startsWith("tablo-overlay-")) {
        body.classList.remove(name);
      }
    }
    body.classList.toggle("tablo-has-overlay", Boolean(style));
    if (style) {
      body.classList.add(`tablo-overlay-${style}`);
    }
  }

  function hideOverlay() {
    if (showing) {
      clearTimeout(showing.timer);
    }
    showing = null;
    overlay.hidden = true;
    overlay.classList.remove("is-entering");
    setStyle("");
  }

  function showEvent(event) {
    const shown = config(event.kind);
    if (!shown) {
      return;
    }
    figures("overlay-title", substitute(shown.title, event));
    figures("overlay-caption", substitute(shown.caption, event));
    figures("overlay-note", event.note || "");
    setStyle(shown.style);
    overlay.hidden = false;
    overlay.classList.remove("is-entering");
    void overlay.offsetWidth; // restart the entrance
    overlay.classList.add("is-entering");
    playSound(shown.sound);
    const persistent = event.kind === "winner" && !shown.seconds;
    showing = { event, persistent, timer: null };
    if (!persistent) {
      showing.timer = setTimeout(() => {
        hideOverlay();
        playNext();
      }, Math.max(shown.seconds, 1) * 1000);
    }
  }

  // The next queued event, unless one is on screen; a winner that stays makes way for it.
  function playNext() {
    if (showing && !showing.persistent) {
      return;
    }
    const event = queue.shift();
    if (!event) {
      return;
    }
    hideOverlay();
    showEvent(event);
  }

  function enqueue(events) {
    for (const event of events) {
      if (config(event.kind)?.enabled) {
        queue.push(event);
      }
    }
    playNext();
  }

  // Sounds: fetched once per URL (same origin: connect-src 'self'), decoded on first play.
  const soundCache = new Map();

  function loadSound(url) {
    if (!url || soundCache.has(url)) {
      return;
    }
    const bytes = fetch(url, { credentials: "same-origin" })
      .then((response) => (response.ok ? response.arrayBuffer() : null))
      .catch(() => null);
    soundCache.set(url, { bytes, decoded: null });
  }

  async function playSound(url) {
    if (!url || !audio || audio.state !== "running") {
      return;
    }
    loadSound(url);
    const entry = soundCache.get(url);
    try {
      const bytes = await entry.bytes;
      if (!bytes) {
        return;
      }
      if (!entry.decoded) {
        entry.decoded = await audio.decodeAudioData(bytes.slice(0));
      }
      const source = audio.createBufferSource();
      source.buffer = entry.decoded;
      source.connect(audio.destination);
      source.start();
    } catch {
      // A file the browser cannot play: the overlay is enough.
    }
  }

  function setTheme(next) {
    theme = next;
    for (const shown of Object.values(theme.events || {})) {
      loadSound(shown.sound);
    }
  }

  function showTip(tips) {
    const box = find("tip-box");
    box.hidden = tips.length === 0;
    if (!tips.length) {
      return;
    }
    tipIndex %= tips.length;
    text("tip", tips[tipIndex]);
    text("tip-count", `${tipIndex + 1} / ${tips.length}`);
    tipShownAt = localNow();
    const screen = find("break-screen");
    screen.classList.remove("is-entering");
    void screen.offsetWidth;
    screen.classList.add("is-entering");
  }

  function breakFrame(clock, running) {
    const shown = config("break");
    const row = clock.current;
    const on = Boolean(row.isBreak && shown?.enabled);
    const tips = theme?.tips || [];
    if (on !== breakScreen) {
      breakScreen = on;
      show("break-screen", on);
      body.classList.toggle("tablo-has-break-screen", on);
      if (on) {
        tipIndex = 0;
        showTip(tips);
        if (running) {
          playSound(shown.sound);
        }
      }
    }
    if (!on) {
      return;
    }
    text("break-screen-theme", theme.name || "");
    text("break-screen-title", substitute(shown.title, {}) || row.label);
    text("break-screen-clock", clockApi.format(clock.remaining));
    const next = clock.nextLevel;
    text("break-screen-next", next ? `Дальше: уровень ${next.number} · ${blinds(next)}` : "");
    if (tips.length && localNow() - tipShownAt >= TIP_MS) {
      tipIndex += 1;
      showTip(tips);
    }
  }

  // Sound.

  let audio = null;

  function unlockSound() {
    if (!audio) {
      const Context = window.AudioContext || window.webkitAudioContext;
      audio = Context ? new Context() : null;
    }
    if (audio && audio.state === "suspended") {
      audio.resume();
    }
    show("sound-prompt", false);
  }

  function beep(times) {
    if (!audio || audio.state !== "running") {
      return;
    }
    for (let i = 0; i < times; i += 1) {
      const start = audio.currentTime + i * 0.25;
      const tone = audio.createOscillator();
      const gain = audio.createGain();
      tone.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.4, start + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.18);
      tone.connect(gain).connect(audio.destination);
      tone.start(start);
      tone.stop(start + 0.2);
    }
  }

  function sounds(clock) {
    if (!rendered || clock.paused) {
      return;
    }
    const changed = clock.current.position !== rendered.position;
    if (changed || (clock.finished && !rendered.finished)) {
      beep(2);
    } else if (rendered.remaining > WARN_AT && clock.remaining <= WARN_AT) {
      beep(1);
    }
  }

  // Server state.

  function apply(data, sentAt, receivedAt) {
    offset = data.now - (sentAt + receivedAt) / 2;
    state = data;
    if (data.theme) {
      setTheme(data.theme);
    }
    if (data.samples) {
      samples = data.samples;
    }
    if (data.cursor !== undefined) {
      cursor = String(data.cursor);
    }
    // An undone last bust: the game goes on, so the winner leaves the screen.
    if (showing?.persistent && data.players > 1) {
      hideOverlay();
    }
    if (data.events) {
      enqueue(data.events);
    }
    render();
  }

  function stop(message) {
    state = null;
    queue.length = 0;
    if (!showing?.persistent) {
      hideOverlay(); // the winner stays on a finished game
    }
    text("level-line", message);
    text("clock", "--:--");
    show("offline", false);
  }

  async function sync() {
    const sentAt = localNow();
    try {
      const url = new URL(body.dataset.stateUrl, window.location.href);
      if (!preview) {
        url.searchParams.set("after", cursor);
      } else if (scene) {
        url.searchParams.set("scene", scene);
      }
      const response = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (response.status === 404) {
        stop("Табло отключено: турнир завершён или ссылка заменена.");
        return;
      }
      if (!response.ok || !response.headers.get("Content-Type")?.includes("json")) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      apply(data, sentAt, localNow());
      show("offline", false);
    } catch {
      show("offline", true);
    }
  }

  // Organizer controls.

  function newKey() {
    if (crypto.randomUUID) {
      return crypto.randomUUID();
    }
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  async function press(name) {
    if (!state || busy) {
      return;
    }
    busy = true;
    const now = serverNow();
    const clock = clockApi.clockAt(state.levels, state.position, state.started, state.paused, now);
    const action = name === "toggle" ? (clock.paused ? "timer-run" : "timer-pause") : name;
    const form = new FormData();
    form.append("key", newKey()); // the same key on every retry of this press
    form.append("from", clock.current.position);
    const url = body.dataset.actUrl.replace("/NAME/", `/${action}/`);
    text("message", "");
    try {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        const sentAt = localNow();
        try {
          const response = await fetch(url, {
            method: "POST",
            body: form,
            credentials: "same-origin",
            headers: { "X-CSRFToken": body.dataset.csrf, Accept: "application/json" },
          });
          if (!response.ok) {
            text("message", `Не записано (ошибка ${response.status}).`);
            return;
          }
          const data = await response.json();
          apply(data, sentAt, localNow());
          show("offline", false);
          if (data.error) {
            text("message", data.error);
          }
          return;
        } catch {
          show("offline", true);
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      }
      text("message", "Нет связи: действие не записано.");
    } finally {
      busy = false;
    }
  }

  // The theme preview's buttons.
  function previewEvent(kind) {
    const note = (message) => text("preview-note", message);
    if (kind === "break") {
      scene = scene === "break" ? "" : "break";
      if (scene && !config("break")?.enabled) {
        note("Перерыв в этой тематике выключен: табло покажет обычный перерыв.");
      } else {
        note("");
      }
      sync();
      return;
    }
    if (scene) {
      scene = "";
      sync();
    }
    const sample = samples[kind];
    if (!sample) {
      return;
    }
    note(config(kind)?.enabled ? "" : "Это событие в тематике выключено: на табло его не будет.");
    queue.length = 0;
    hideOverlay();
    enqueue([sample]);
  }

  document.addEventListener("click", (event) => {
    unlockSound();
    const button = event.target.closest("[data-act]");
    if (button) {
      button.blur(); // the space bar must not press it again
      press(button.dataset.act);
    }
    const sample = event.target.closest("[data-preview-kind]");
    if (sample) {
      previewEvent(sample.dataset.previewKind);
    }
  });

  document.addEventListener("keydown", (event) => {
    unlockSound();
    if (event.code !== "Space" || !body.dataset.actUrl) {
      return;
    }
    event.preventDefault();
    if (!event.repeat) {
      press("toggle");
    }
  });

  // Keep the screen on.

  let lock = null;

  async function keepAwake() {
    if (!("wakeLock" in navigator) || document.visibilityState !== "visible" || lock) {
      return;
    }
    try {
      lock = await navigator.wakeLock.request("screen");
      lock.addEventListener("release", () => {
        lock = null;
      });
    } catch {
      lock = null; // not allowed right now (battery saver, hidden tab)
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      keepAwake();
      sync();
    }
  });

  keepAwake();
  sync();
  setInterval(render, 250);
  setInterval(sync, Number(body.dataset.syncSeconds || 5) * 1000);
})();
