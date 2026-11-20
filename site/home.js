// Preview pages only: fades sections in as they scroll into view. Loaded in <head> so the
// "js" class is set before first paint; without JS (or with reduced motion) everything is visible.
document.documentElement.classList.add("js");
addEventListener("DOMContentLoaded", () => {
  const els = document.querySelectorAll("[data-reveal]");
  if (!("IntersectionObserver" in window) || matchMedia("(prefers-reduced-motion: reduce)").matches) {
    els.forEach(el => el.classList.add("in"));
    return;
  }
  const io = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) { entry.target.classList.add("in"); io.unobserve(entry.target); }
  }), { rootMargin: "0px 0px -8% 0px" });
  els.forEach(el => io.observe(el));
});

// The compact feature list is tap-to-open on phones; on wider screens every row is simply open.
addEventListener("DOMContentLoaded", () => {
  const wide = matchMedia("(min-width: 720px)");
  const sync = () => document.querySelectorAll("[data-open-wide] details").forEach(d => { d.open = wide.matches; });
  sync(); wide.addEventListener("change", sync);
});

// Life tabs: without JS every panel is visible and the tablist stays hidden (see bright.css).
addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-tabs]").forEach(root => {
    const tabs = [...root.querySelectorAll('[role="tab"]')];
    const panels = tabs.map(t => document.getElementById(t.getAttribute("aria-controls")));
    const select = (i, focus) => {
      tabs.forEach((t, j) => {
        const on = i === j;
        t.setAttribute("aria-selected", on);
        t.tabIndex = on ? 0 : -1;
        if (panels[j]) panels[j].hidden = !on;
      });
      if (focus) tabs[i].focus();
    };
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => select(i));
      t.addEventListener("keydown", e => {
        const to = { ArrowRight: (i + 1) % tabs.length, ArrowLeft: (i + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 }[e.key];
        if (to === undefined) return;
        e.preventDefault();
        select(to, true);
      });
    });
    select(Math.max(0, tabs.findIndex(t => t.getAttribute("aria-selected") === "true")));
  });
});
