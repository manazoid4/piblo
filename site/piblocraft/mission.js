// PibloCraft page: before/after slider + a simulated mission control. Nothing here talks to a server.
(function () {
  "use strict";
  var compare = document.getElementById("pc-compare");
  var range = document.getElementById("compare-range");
  if (compare && range) {
    var set = function () { compare.style.setProperty("--pos", range.value + "%"); };
    range.addEventListener("input", set); set();
  }

  var grid = document.getElementById("mc-grid");
  if (!grid) return;
  var feed = document.getElementById("mc-feed");
  var form = document.getElementById("mc-form");
  var input = document.getElementById("mc-input");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var TICK = reduce ? 120 : 400;
  var done = 0;
  var queue = [];

  // left = usage left (%), cost = usage per task. Claude 1 is nearly out so the auto-switch shows up early.
  var agents = [
    { name: "Claude 1", kind: "Claude", left: 14, cost: 12 },
    { name: "Claude 2", kind: "Claude", left: 82, cost: 12 },
    { name: "Codex", kind: "OpenAI", left: 64, cost: 10 },
    { name: "Hermes", kind: "Nous", left: 90, cost: 8 },
    { name: "Ollama", kind: "This PC", left: 100, cost: 0 }
  ];
  agents.forEach(function (a) {
    a.state = "ready"; a.task = ""; a.progress = 0;
    var el = document.createElement("article");
    el.className = "mc-tile";
    el.innerHTML = '<h4><span></span><small></small></h4><span class="mc-pill"></span><p class="mc-task"></p><div class="mc-bar"><i></i></div><span class="mc-left"></span><div class="mc-act" hidden><button type="button" class="yes">Allow once</button><button type="button" class="no">Deny</button></div>';
    el.querySelector("h4 span").textContent = a.name;
    el.querySelector("h4 small").textContent = a.kind;
    el.querySelector(".yes").addEventListener("click", function () { decide(a, true); });
    el.querySelector(".no").addEventListener("click", function () { decide(a, false); });
    a.el = el; grid.appendChild(el);
  });

  var LABEL = { ready: "Ready", work: "Working", done: "Done", need: "Needs you", limit: "Limit reached" };
  function draw(a) {
    a.el.className = "mc-tile is-" + a.state;
    a.el.querySelector(".mc-pill").textContent = LABEL[a.state];
    a.el.querySelector(".mc-task").textContent = a.task || (a.state === "limit" ? "Back after the reset" : "Free for a task");
    a.el.querySelector(".mc-bar i").style.width = a.progress + "%";
    a.el.querySelector(".mc-left").textContent = a.cost ? a.left + "% usage left" : "Free, unlimited";
    a.el.querySelector(".mc-act").hidden = a.state !== "need";
    var c = { work: 0, need: 0 };
    agents.forEach(function (x) { if (c[x.state] !== undefined) c[x.state]++; });
    document.getElementById("mc-working").textContent = c.work;
    document.getElementById("mc-need").textContent = c.need;
    document.getElementById("mc-done").textContent = done;
  }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function log(html) {
    var li = document.createElement("li"); li.innerHTML = html;
    feed.insertBefore(li, feed.firstChild);
    while (feed.children.length > 30) feed.removeChild(feed.lastChild);
  }

  // best = free with the most usage left; the free local model is the last resort
  function best(skip) {
    var free = agents.filter(function (a) { return a !== skip && (a.state === "ready" || a.state === "done") && (a.cost === 0 || a.left >= a.cost); });
    free.sort(function (x, y) { return (x.cost === 0) - (y.cost === 0) || y.left - x.left; });
    return free[0];
  }

  function assign(task, risky, from) {
    var a = best(from);
    if (!a) { log("Everyone is busy. <b>" + esc(task) + "</b> waits in the queue."); queue.push({ task: task, risky: risky }); return; }
    a.state = "work"; a.task = task; a.progress = 0; a.risky = risky;
    log(from ? "<b>" + esc(from.name) + "</b> hit its limit, so <b>" + esc(a.name) + "</b> carries on: " + esc(task)
             : "<b>" + esc(a.name) + "</b> picked up: " + esc(task));
    draw(a);
  }

  function decide(a, ok) {
    if (a.state !== "need") return;
    if (ok) { a.state = "work"; a.risky = false; log("You allowed <b>" + esc(a.name) + "</b> once. Carrying on."); }
    else { a.state = "ready"; a.task = ""; a.progress = 0; log("You denied it. <b>" + esc(a.name) + "</b> stopped; nothing was pushed."); }
    draw(a);
  }

  setInterval(function () {
    agents.forEach(function (a) {
      if (a.state !== "work") return;
      a.progress = Math.min(100, a.progress + (a.cost ? 4 : 3));
      if (a.risky && a.progress >= 45) { a.state = "need"; log("<b>" + esc(a.name) + "</b> wants to push to main. Allow once or deny?"); draw(a); return; }
      if (a.cost && a.progress >= 50 && a.left < a.cost * 1.5) {   // running dry mid-task: hand over
        var t = a.task, r = a.risky; a.left = 0; a.state = "limit"; a.task = ""; a.progress = 0; draw(a); assign(t, r, a); return;
      }
      if (a.progress >= 100) {
        a.left = Math.max(0, a.left - a.cost); done++;
        log("<b>" + esc(a.name) + "</b> finished: " + esc(a.task) + ". Report saved.");
        a.state = a.cost && a.left < a.cost ? "limit" : "done"; a.task = ""; a.progress = 100;
        draw(a);
        var next = queue.shift(); if (next) assign(next.task, next.risky);
        return;
      }
      draw(a);
    });
  }, TICK);

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var t = input.value.trim(); if (!t) return;
    assign(t, /\b(push|deploy|delete|merge)\b/i.test(t)); input.value = "";
  });
  document.getElementById("mc-chips").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-task]"); if (!b) return;
    assign(b.dataset.task, b.dataset.risky === "1");
  });

  agents.forEach(draw);
  log("Mission control is ready. Give your team a task.");
  // Claude 1 starts mid-task and nearly out of usage, so the hand-over plays without a click
  var c1 = agents[0]; c1.state = "work"; c1.task = "Refactor auth"; c1.progress = 30; draw(c1);
  log("<b>Claude 1</b> is on: Refactor auth");
})();
