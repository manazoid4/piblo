// PibloCraft page: draws the simulated workspace from demo-core.js. Nothing here talks to a server.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var root = $("mc");
  if (!root || !window.PibloCraftDemo) return;
  var demo = window.PibloCraftDemo.createDemo();
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var TICK = reduce ? 450 : 1300;
  var LABEL = { working: "Working", ready: "Ready", needs: "Needs you", paused: "Paused", completed: "Completed", unavailable: "Unavailable",
    done: "Completed", stopped: "Stopped" };
  var STAGE = { working: "Working", needs: "Needs you", done: "Completed", stopped: "Stopped" };
  var showAll = false, timer = null, lastStage = null;

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function fill(list, items, make) { list.textContent = ""; items.forEach(function (x) { list.appendChild(make(x)); }); }

  function drawTask(s) {
    var box = $("mc-task"), t = s.task;
    box.textContent = "";
    if (!t) { box.appendChild(el("p", "ws-empty", "No task yet. Choose one above to see the agents get to work.")); return; }
    var head = el("div", "ws-task-head");
    head.appendChild(el("span", "st st-" + t.stage, STAGE[t.stage]));
    head.appendChild(el("small", "ws-kind", t.kind));
    box.appendChild(head);
    box.appendChild(el("h3", "ws-task-title", t.title));
    var bar = el("div", "ws-progress"); bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", "Steps finished"); bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", String(t.total)); bar.setAttribute("aria-valuenow", String(t.done));
    var fillEl = el("i"); fillEl.style.width = Math.round(100 * t.done / t.total) + "%"; bar.appendChild(fillEl);
    box.appendChild(bar);
    var next = t.stage === "needs" ? "Next: your decision below" : t.stage === "done" ? "Next: open the handoff to pick up later"
      : t.stage === "stopped" ? "Next: try again or ask for something different" : "Next: agents keep going, nothing needs you yet";
    if (t.stage !== lastStage) { lastStage = t.stage; $("mc-live").textContent = t.title + ": " + STAGE[t.stage] + ". " + next; }
    var meta = el("p", "ws-task-meta"); meta.appendChild(el("b", null, "Step " + t.done + " of " + t.total)); meta.appendChild(document.createTextNode(" · " + next));
    box.appendChild(meta);
  }

  function drawAgents(s) {
    var order = { needs: 0, working: 1, completed: 2, ready: 3, paused: 4, unavailable: 5 };
    var list = s.agents.slice().sort(function (a, b) { return order[a.state] - order[b.state]; });
    var visible = showAll ? list : list.slice(0, 4);
    fill($("mc-agents"), visible, function (a) {
      var li = el("li", "ag ag-" + a.state);
      var top = el("div", "ag-top"); top.appendChild(el("b", null, a.name)); top.appendChild(el("small", null, a.provider));
      li.appendChild(top);
      var row = el("div", "ag-row"); row.appendChild(el("span", "st st-" + a.state, LABEL[a.state])); row.appendChild(el("span", "ag-doing", a.doing));
      li.appendChild(row);
      return li;
    });
    var more = $("mc-more");
    more.hidden = list.length <= 4;
    more.textContent = showAll ? "Show fewer" : "Show all " + list.length + " agents";
    more.setAttribute("aria-expanded", String(showAll));
    var c = { working: 0, needs: 0 };
    s.agents.forEach(function (a) { if (c[a.state] != null) c[a.state]++; });
    $("mc-counts").textContent = c.working + " working · " + c.needs + " needs you";
  }

  function draw(s) {
    drawTask(s);
    drawAgents(s);
    var ap = $("mc-approval");
    ap.hidden = !s.approval;
    if (s.approval) {
      $("ap-agent").textContent = s.approval.agent;
      $("ap-want").textContent = s.approval.want.charAt(0).toLowerCase() + s.approval.want.slice(1);
      $("ap-change").textContent = s.approval.change;
      $("ap-then").textContent = s.approval.then;
      $("ap-changes").hidden = s.revised;
    }
    var res = $("mc-result");
    res.hidden = !s.result;
    if (s.result) {
      res.className = "ws-result" + (s.decision === "denied" ? " is-stopped" : "");
      $("res-title").textContent = s.decision === "denied" ? "Stopped. Nothing was changed." : "Done. Here’s what you got.";
      fill($("res-list"), s.result, function (x) { return el("li", null, x); });
    }
    var ho = $("mc-handoff");
    var hadHandoff = !ho.hidden;
    ho.hidden = !s.handoff;
    if (s.handoff) {
      $("ho-requested").textContent = s.handoff.requested;
      fill($("ho-happened"), s.handoff.happened, function (x) { return el("li", null, x); });
      $("ho-decided").textContent = s.handoff.decided;
      $("ho-remains").textContent = s.handoff.remains;
      $("ho-resume").textContent = s.handoff.resume;
      if (!hadHandoff) ho.open = window.innerWidth >= 760;
    }
    fill($("mc-feed"), s.activity.slice(0, 8), function (e) {
      var li = el("li");
      if (e.agent) li.appendChild(el("b", null, e.agent + " "));
      li.appendChild(document.createTextNode(e.text));
      return li;
    });
  }

  function run() {
    clearInterval(timer);
    timer = setInterval(function () { if (!demo.tick()) clearInterval(timer); }, TICK);
  }
  function start(task) {
    demo.start(task); run();
  }

  demo.subscribe(draw);
  // Play one example as soon as the demo scrolls into view, so it never sits empty. It still stops for your decision.
  if ("IntersectionObserver" in window) {
    var seen = new IntersectionObserver(function (entries) {
      if (!entries[0].isIntersecting) return;
      seen.disconnect();
      if (!demo.state.task) start("research");
    }, { threshold: 0.35 });
    seen.observe(root);
  }
  $("mc-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var input = $("mc-input"), t = input.value.trim();
    if (!t) { input.focus(); return; }
    start(t); input.value = "";
  });
  $("mc-chips").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-task]"); if (b) start(b.dataset.task);
  });
  $("mc-approval").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-decide]");
    if (b && demo.decide(b.dataset.decide)) { run(); $("mc-task").focus(); }
  });
  $("mc-more").addEventListener("click", function () { showAll = !showAll; drawAgents(demo.state); });
  $("mc-reset").addEventListener("click", function () { clearInterval(timer); demo.reset(); });
  document.addEventListener("click", function (e) {
    var b = e.target.closest("button[data-try]"); if (!b) return;
    root.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    start(b.dataset.try);
  });
})();
