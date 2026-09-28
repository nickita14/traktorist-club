/*
 * The blind clock in the browser: the same computation as src/live/clock.py (clock_at), over the
 * state the server sends. Times are milliseconds since the epoch on the server's clock.
 * src/live/tests/clock_cases.json runs the same cases through both implementations.
 *
 * Exposed as window.BlindClock for tablo.js and live.js.
 */
(() => {
  "use strict";

  const MINUTE = 60 * 1000;

  // Levels numbered 1, 2, ...; breaks (no big blind) get no number.
  function numbered(levels) {
    let number = 0;
    return levels.map((level) => {
      const isBreak = level.big === null || level.big === undefined;
      if (!isBreak) {
        number += 1;
      }
      return { ...level, isBreak, number: isBreak ? null : number, duration: level.minutes * MINUTE };
    });
  }

  function clockAt(levels, position, started, paused, now) {
    const rows = numbered(levels);
    let index = Math.max(
      rows.findIndex((row) => row.position === position),
      0,
    );
    let elapsed = (paused ?? now) - started;
    while (index < rows.length - 1 && elapsed >= rows[index].duration) {
      elapsed -= rows[index].duration;
      index += 1;
    }
    const current = rows[index];
    const finished = elapsed >= current.duration;
    const remaining = Math.max(current.duration - elapsed, 0);
    const after = rows.slice(index + 1);
    const nextLevel = after.find((row) => !row.isBreak) ?? null;
    const nextBreak = after.find((row) => row.isBreak) ?? null;
    let untilBreak = null;
    if (nextBreak) {
      untilBreak = remaining;
      for (const row of after) {
        if (row === nextBreak) {
          break;
        }
        untilBreak += row.duration;
      }
    }
    const levelBefore = (pause) => {
      const earlier = rows.slice(0, rows.indexOf(pause));
      return earlier.reverse().find((row) => !row.isBreak) ?? null;
    };
    const addon = rows.find((row) => row.addon) ?? null;
    return {
      rows,
      index,
      current,
      elapsed,
      remaining,
      paused: paused !== null && paused !== undefined,
      finished,
      levelCount: rows.filter((row) => !row.isBreak).length,
      nextLevel,
      nextBreak,
      untilBreak,
      levelBefore,
      rebuysUntil: addon ? levelBefore(addon) : null,
    };
  }

  // "14:37", or "1:04:37" past an hour; whole seconds rounded up, so 00:00 only at the end.
  function format(ms) {
    const total = Math.max(Math.ceil(ms / 1000), 0);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    const two = (value) => String(value).padStart(2, "0");
    return hours ? `${hours}:${two(minutes)}:${two(seconds)}` : `${two(minutes)}:${two(seconds)}`;
  }

  // Thousands separated by a no-break space, like club.formatting.format_money.
  function money(value) {
    return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  window.BlindClock = { clockAt, format, money };
})();
