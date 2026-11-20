/*
 * Theme admin, event rows: the "прослушать" player follows the row's sound select, so the
 * organizer hears a sound before saving. Each option carries its URL (live.admin.SoundSelect);
 * a sound just added through the "+" popup has none until the theme is saved, so the player
 * hides. Delegated, so rows added with "Добавить" work too.
 */
(() => {
  "use strict";

  document.addEventListener("change", (event) => {
    const select = event.target;
    if (!(select instanceof HTMLSelectElement) || !select.name.endsWith("-sound")) {
      return;
    }
    const row = select.closest("tr, .form-group");
    const player = row?.querySelector("audio[data-sound-player]");
    if (!player) {
      return;
    }
    const url = select.selectedOptions[0]?.dataset.soundUrl || "";
    player.pause();
    if (url) {
      player.src = url;
    } else {
      player.removeAttribute("src");
    }
    player.load();
    player.hidden = !url;
  });
})();
