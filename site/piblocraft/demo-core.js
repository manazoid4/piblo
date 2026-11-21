// PibloCraft demo engine: a pure state machine with no DOM and no network. mission.js draws it.
// Every agent here is simulated. Progress = steps finished out of steps planned (measurable, not invented).
(function (root) {
  "use strict";

  var ROSTER = [
    { id: "claude", name: "Claude", provider: "Anthropic" },
    { id: "codex", name: "Codex", provider: "OpenAI" },
    { id: "hermes", name: "Hermes", provider: "Nous" },
    { id: "local", name: "Local model", provider: "On this PC" },
    { id: "gemini", name: "Gemini", provider: "Google" },
    { id: "kimi", name: "Kimi", provider: "Moonshot" }
  ];
  var OFFLINE = { gemini: "Not connected", kimi: "Paused by you" };

  // Each scenario: steps run in order; the approval sits after step `ask`; `after` runs once approved.
  var SCENARIOS = {
    fix: {
      title: "Fix a website issue", kind: "Build", keys: /\b(fix|bug|broken|error|website|login|issue)\b/i,
      steps: [
        ["codex", "Reproduced it: the contact form shows an error on phones."],
        ["codex", "Found the cause: the email field rejects addresses with a plus sign."],
        ["claude", "Wrote a two-line fix and a test for it."],
        ["local", "Ran the checks on this PC: 24 of 24 pass."]
      ],
      ask: { agent: "codex", want: "Publish the fix to your live website", change: "contact-form.js: 2 lines changed, 1 test added", then: "Visitors can send the form from any phone within a minute." },
      after: [["codex", "Published. Sent a test message from a phone: it arrived."]],
      result: ["Contact form fixed and live", "Checks: 24 of 24 pass", "A test message arrived"],
      remains: "Watch for any form errors tomorrow.", resume: "Open the live contact page and send yourself one message."
    },
    research: {
      title: "Research three competitors", kind: "Research", keys: /\b(research|competitor|compare|market|find out|look into)\b/i,
      steps: [
        ["hermes", "Collected pricing pages for the three competitors."],
        ["claude", "Compared prices, features and reviews side by side."],
        ["local", "Checked every figure against its source link."]
      ],
      ask: { agent: "claude", want: "Save the comparison to your shared Research folder", change: "Adds competitors.md (1 page, 9 sources)", then: "Anyone on your team can open it." },
      after: [["claude", "Saved competitors.md with a one-line summary at the top."]],
      result: ["One-page comparison saved", "9 sources, every figure linked", "Main gap: none of them offers same-day setup"],
      remains: "Decide whether same-day setup is worth offering.", resume: "Open competitors.md and read the summary line."
    },
    plan: {
      title: "Prepare a project plan", kind: "Create & organise", keys: /\b(plan|project|schedule|roadmap|steps|timeline)\b/i,
      steps: [
        ["claude", "Split the project into five stages with owners."],
        ["hermes", "Added dates around the bank holidays."],
        ["local", "Flagged two stages that depend on the same person."]
      ],
      ask: { agent: "claude", want: "Add the five stages to your calendar", change: "Creates 5 calendar events (Mon 1 Dec to Fri 19 Dec)", then: "You get reminders before each stage starts." },
      after: [["claude", "Added 5 events and a note about the clash."]],
      result: ["Five-stage plan with dates", "5 calendar events created", "One clash flagged for you"],
      remains: "Choose who takes stage 3.", resume: "Open the plan and pick an owner for stage 3."
    },
    draft: {
      title: "Draft an announcement", kind: "Create & organise", keys: /\b(draft|write|announce|announcement|post|email|newsletter|blog)\b/i,
      steps: [
        ["claude", "Wrote a 120-word announcement in your usual tone."],
        ["hermes", "Made a shorter version for social media."],
        ["local", "Checked names, dates and links."]
      ],
      ask: { agent: "claude", want: "Schedule the post for 9:00 tomorrow", change: "Queues 1 post on your page. Nothing is sent before 9:00.", then: "It goes out at 9:00 and you get a note when it does." },
      after: [["claude", "Scheduled for 9:00. You can still edit it until then."]],
      result: ["Announcement written (120 words)", "Short version for social media", "Scheduled for 9:00 tomorrow"],
      remains: "Reply to comments after it goes out.", resume: "Check the post's first comments at 10:00."
    },
    organise: {
      title: "Organise a collection of ideas", kind: "Create & organise", keys: /\b(organi[sz]e|ideas|notes|sort|tidy|collect)\b/i,
      steps: [
        ["local", "Read 42 loose notes on this PC. Nothing left your computer."],
        ["claude", "Grouped them into 6 themes and merged 9 duplicates."],
        ["hermes", "Picked the three ideas you mention most often."]
      ],
      ask: { agent: "local", want: "Move the 9 duplicate notes to an Archive folder", change: "Moves 9 files. Nothing is deleted.", then: "Your ideas list drops from 42 notes to 33." },
      after: [["local", "Moved 9 duplicates to Archive. You can move them back at any time."]],
      result: ["6 themes from 42 notes", "9 duplicates archived, none deleted", "Top three ideas picked out"],
      remains: "Pick one of the top three to start on.", resume: "Open the Themes page and choose one idea."
    },
    review: {
      title: "Review a change before publishing", kind: "Build", keys: /\b(review|check|publish|release|approve|proofread)\b/i,
      steps: [
        ["codex", "Read the change: 3 pages updated."],
        ["claude", "Found one broken link and one typo, and fixed both."],
        ["local", "Took screenshots on a phone and a laptop: the layout holds."]
      ],
      ask: { agent: "codex", want: "Publish the 3 updated pages", change: "Pricing, About and FAQ pages, with the two fixes", then: "The new pages are live within a minute." },
      after: [["codex", "Published. All 3 pages load and every link works."]],
      result: ["3 pages reviewed and published", "1 broken link and 1 typo fixed", "Checked on a phone and a laptop"],
      remains: "Nothing urgent.", resume: "Glance at the Pricing page on your phone."
    }
  };

  function pick(text) {
    var t = String(text || "");
    for (var id in SCENARIOS) if (Object.prototype.hasOwnProperty.call(SCENARIOS, id) && SCENARIOS[id].keys.test(t)) return id;
    return "plan";
  }

  function createDemo() {
    var listeners = [];
    var s;
    function agent(id) { for (var i = 0; i < s.agents.length; i++) if (s.agents[i].id === id) return s.agents[i]; }
    function log(who, text) { s.activity.unshift({ n: ++s.seq, agent: who, text: text }); if (s.activity.length > 40) s.activity.pop(); }
    function emit() { listeners.forEach(function (fn) { fn(s); }); }

    function reset() {
      s = { seq: 0, task: null, approval: null, result: null, handoff: null, activity: [], decision: null, revised: false,
        agents: ROSTER.map(function (a) { return { id: a.id, name: a.name, provider: a.provider, state: OFFLINE[a.id] ? (a.id === "kimi" ? "paused" : "unavailable") : "ready", doing: OFFLINE[a.id] || "Free for a task" }; }) };
      log(null, "Ready. Choose a task or type your own.");
      emit();
    }

    function start(input) {
      var known = Object.prototype.hasOwnProperty.call(SCENARIOS, input);
      var id = known ? input : pick(input);
      var sc = SCENARIOS[id];
      var custom = known ? null : String(input).trim().slice(0, 80);
      var queue = sc.steps.slice();
      s.task = { id: id, title: custom || sc.title, kind: sc.kind, stage: "working", done: 0,
        total: sc.steps.length + 1 + sc.after.length, queue: queue, afterQueued: false, asked: false };
      s.approval = null; s.result = null; s.handoff = null; s.decision = null; s.revised = false;
      s.agents.forEach(function (a) { if (a.state !== "unavailable" && a.state !== "paused") { a.state = "ready"; a.doing = "Free for a task"; } });
      var used = {};
      sc.steps.concat(sc.after).forEach(function (st) { used[st[0]] = 1; });
      Object.keys(used).forEach(function (who) { var a = agent(who); a.state = "working"; a.doing = "Waiting for its turn"; });
      log(null, "New task: " + s.task.title + (custom ? " (simulated as “" + sc.title + "”)" : ""));
      emit();
      return id;
    }

    function finishTask() {
      var sc = SCENARIOS[s.task.id];
      s.task.stage = "done";
      var used = {};
      sc.steps.concat(sc.after).forEach(function (st) { used[st[0]] = 1; });
      s.agents.forEach(function (a) { if (used[a.id]) { a.state = "completed"; a.doing = "Finished its part"; } });
      s.result = sc.result.slice();
      s.handoff = handoff("Approved" + (s.revised ? " after one change" : ""), sc.remains, sc.resume);
      log(null, "Done. Handoff saved so you can pick this up later.");
    }

    function handoff(decided, remains, resume) {
      var sc = SCENARIOS[s.task.id];
      var happened = s.activity.filter(function (e) { return e.agent && !/Waiting for you\.$/.test(e.text); }).slice().reverse().map(function (e) { return e.text; });
      return { requested: s.task.title, happened: happened, decided: decided + ": " + sc.ask.want.toLowerCase() + ".", remains: remains, resume: resume };
    }

    // One unit of work. Returns false when nothing is left to do.
    function tick() {
      var t = s.task;
      if (!t || t.stage === "done" || t.stage === "stopped" || t.stage === "needs") return false;
      var sc = SCENARIOS[t.id];
      if (!t.queue.length && !t.asked) {
        t.asked = true; t.stage = "needs"; t.done++;
        var who = agent(sc.ask.agent);
        s.agents.forEach(function (a) { if (a.state === "working") { a.state = "ready"; a.doing = "Waiting on your decision"; } });
        who.state = "needs"; who.doing = "Asking: " + sc.ask.want.toLowerCase();
        s.approval = { agent: who.name, want: sc.ask.want, change: sc.ask.change, then: sc.ask.then };
        log(who.name, "Wants to " + sc.ask.want.toLowerCase() + ". Waiting for you.");
        emit(); return true;
      }
      if (!t.queue.length) { finishTask(); emit(); return false; }
      var st = t.queue.shift();
      var a = agent(st[0]);
      s.agents.forEach(function (x) { if (x.state === "working" && x !== a && x.doing !== "Waiting for its turn") x.doing = "Waiting for its turn"; });
      a.state = "working"; a.doing = st[1];
      if (!st.revision) t.done++;
      log(a.name, st[1]);
      if (!t.queue.length && t.asked) { finishTask(); }
      emit(); return true;
    }

    function decide(choice) {
      var t = s.task;
      if (!t || t.stage !== "needs") return false;
      var sc = SCENARIOS[t.id];
      var who = agent(sc.ask.agent);
      s.approval = null;
      if (choice === "approve") {
        s.decision = "approved"; t.stage = "working";
        t.queue = sc.after.slice();
        who.state = "working"; who.doing = "Carrying out what you approved";
        log("You", "Approved: " + sc.ask.want.toLowerCase() + ".");
      } else if (choice === "deny") {
        s.decision = "denied"; t.stage = "stopped";
        s.agents.forEach(function (a) { if (a.state !== "unavailable" && a.state !== "paused") { a.state = "ready"; a.doing = "Free for a task"; } });
        log("You", "Denied. Nothing was changed.");
        s.result = ["Stopped before anything changed", "Work so far is kept in the handoff"];
        s.handoff = handoff("Denied", "Decide whether to try again with different instructions.", "Open this task and choose “Ask for changes” instead.");
      } else if (choice === "changes" && !s.revised) {
        s.decision = "changes"; s.revised = true; t.stage = "working"; t.asked = false;
        t.queue = [[sc.ask.agent, "Made the change you asked for and checked it again."]];
        t.queue[0].revision = true;
        t.done--; // the approval will be asked again
        who.state = "working"; who.doing = "Making your change";
        log("You", "Asked for changes before approving.");
      } else return false;
      emit(); return true;
    }

    reset();
    return {
      get state() { return s; }, start: start, tick: tick, decide: decide, reset: reset,
      subscribe: function (fn) { listeners.push(fn); fn(s); }
    };
  }

  root.PibloCraftDemo = { createDemo: createDemo, SCENARIOS: SCENARIOS, ROSTER: ROSTER, pick: pick };
})(typeof window !== "undefined" ? window : globalThis);
