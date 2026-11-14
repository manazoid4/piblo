// Preview pages only: fades sections in as they scroll into view. Loaded in <head> so the
// "js" class is set before first paint; without JS (or with reduced motion) everything is visible.
document.documentElement.classList.add("js");

// The compact feature list is tap-to-open on phones; on wider screens every row is simply open.
addEventListener("DOMContentLoaded", () => {
  const wide = matchMedia("(min-width: 720px)");
  const sync = () => document.querySelectorAll("[data-open-wide] details").forEach(d => { d.open = wide.matches; });
  sync(); wide.addEventListener("change", sync);
});
