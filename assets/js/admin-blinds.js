/*
 * Blind structure admin: rows are levels or breaks ("Тип", club.admin.BlindLevelForm).
 *
 * - A level shows blinds and ante; a break shows its label and the add-on toggle. The other
 *   type's inputs are hidden (the column keeps its width) and disabled, so they are not posted.
 * - "Ур." numbers the levels in their current order, skipping breaks and rows marked for
 *   deletion, while rows are dragged, added or change type.
 */
(() => {
  "use strict";

  const LEVEL_FIELDS = ["small_blind", "big_blind", "ante"];
  const BREAK_FIELDS = ["label", "addon_break"];

  const rows = () => [...document.querySelectorAll("tbody.form-group:not(.empty-form)")];
  const kindOf = (row) => row.querySelector("select[data-row-kind]");

  function setShown(row, field, shown) {
    const cell = row.querySelector(`td.field-${field}`);
    if (!cell) {
      return;
    }
    cell.classList.toggle("blinds-off", !shown);
    for (const input of cell.querySelectorAll("input, select")) {
      input.disabled = !shown;
    }
  }

  function applyKind(row) {
    const kind = kindOf(row);
    if (!kind) {
      return;
    }
    const isBreak = kind.value === "break";
    for (const field of LEVEL_FIELDS) {
      setShown(row, field, !isBreak);
    }
    for (const field of BREAK_FIELDS) {
      setShown(row, field, isBreak);
    }
  }

  function renumber() {
    let number = 0;
    for (const row of rows()) {
      const kind = kindOf(row);
      const label = row.querySelector("[data-level-number]");
      if (!kind || !label) {
        continue;
      }
      const deleted = row.querySelector('input[name$="-DELETE"]')?.checked;
      if (deleted) {
        label.textContent = "";
      } else if (kind.value === "break") {
        label.textContent = "перерыв";
      } else {
        number += 1;
        label.textContent = String(number);
      }
    }
  }

  document.addEventListener("change", (event) => {
    const row = event.target.closest("tbody.form-group");
    if (row && event.target.matches("select[data-row-kind]")) {
      applyKind(row);
    }
    if (row) {
      renumber();
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    rows().forEach(applyKind);
    renumber();
    // Dragging moves the row groups; "add another" inserts one.
    const table = document.querySelector("[data-ordering-field]");
    if (table) {
      new MutationObserver(() => {
        rows().forEach(applyKind);
        renumber();
      }).observe(table, { childList: true });
    }
  });
})();
