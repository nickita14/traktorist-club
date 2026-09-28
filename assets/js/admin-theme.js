/*
 * Keep the Unfold admin in light mode. The project styles only the light theme.
 *
 * UNFOLD["THEME"] = "light" only sets Unfold's *default*: the Alpine state in
 * unfold/js/app.js (`theme()`, persisted to localStorage "adminTheme") still
 * switches <html> to `dark` when a stored "dark" or "auto" (with a dark OS)
 * exists, or when Ctrl/Cmd+E toggles it. Here the class binding always
 * returns "light" and the stored value is reset.
 *
 * Loaded through UNFOLD["SCRIPTS"]: it runs before Alpine (deferred) starts,
 * and "alpine:init" fires after app.js has defined theme().
 * Covered by src/club/tests/test_admin_browser.py.
 */
document.addEventListener("alpine:init", () => {
  const unfoldTheme = window.theme;
  if (typeof unfoldTheme !== "function") {
    return;
  }
  window.theme = (...args) => {
    const state = unfoldTheme(...args);
    state.adminTheme = "light";
    state.themeBindings["x-bind:class"] = () => "light";
    return state;
  };
  try {
    localStorage.setItem("adminTheme", JSON.stringify("light"));
  } catch {
    // Storage blocked: the binding above still forces light.
  }
});
