// Celebration pages (the year report): each sheet's entrance plays once, when the sheet first
// scrolls into view. Without this script, or with reduced motion, nothing is hidden or moved
// (frontend/source.css, "Celebration pages").
(function () {
  "use strict";
  if (!("IntersectionObserver" in window)) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  document.documentElement.classList.add("poster-motion");
  document.addEventListener("DOMContentLoaded", function () {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-seen");
          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.25 }
    );
    document.querySelectorAll(".poster-sheet").forEach(function (sheet) {
      observer.observe(sheet);
    });
  });
})();
