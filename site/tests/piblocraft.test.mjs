import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "piblocraft/index.html"), "utf8");

function load() {
  const ctx = {};
  vm.runInNewContext(readFileSync(join(root, "piblocraft/demo-core.js"), "utf8"), { window: ctx });
  return ctx.PibloCraftDemo;
}
const runUntilStill = demo => { let n = 0; while (demo.tick()) if (++n > 50) throw new Error("runaway"); };

test("demo: a task runs, stops for approval, and approve finishes with a result and handoff", () => {
  const { createDemo } = load();
  const demo = createDemo();
  demo.start("draft");
  runUntilStill(demo);
  let s = demo.state;
  assert.equal(s.task.stage, "needs");
  assert.match(s.approval.want, /Schedule the post/);
  assert.ok(s.approval.change && s.approval.then);
  assert.equal(s.agents.filter(a => a.state === "needs").length, 1);
  assert.equal(demo.tick(), false, "nothing happens while it waits for you");

  assert.equal(demo.decide("approve"), true);
  runUntilStill(demo);
  s = demo.state;
  assert.equal(s.task.stage, "done");
  assert.equal(s.task.done, s.task.total);
  assert.equal(s.approval, null);
  assert.ok(s.result.length >= 2);
  for (const k of ["requested", "happened", "decided", "remains", "resume"]) assert.ok(s.handoff[k], k);
  assert.match(s.handoff.decided, /^Approved/);
  assert.ok(!s.handoff.happened.some(t => /Waiting for you/.test(t)));
  assert.ok(!s.agents.some(a => a.doing === "Waiting on your decision"), "helpers are marked finished");
});

test("demo: deny stops the task and changes nothing", () => {
  const demo = load().createDemo();
  demo.start("fix");
  runUntilStill(demo);
  assert.equal(demo.decide("deny"), true);
  const s = demo.state;
  assert.equal(s.task.stage, "stopped");
  assert.match(s.result[0], /Stopped before anything changed/);
  assert.match(s.handoff.decided, /^Denied/);
  assert.equal(demo.tick(), false);
  assert.equal(demo.decide("approve"), false, "a stopped task cannot be approved later");
});

test("demo: ask for changes revises once, asks again, then can be approved", () => {
  const demo = load().createDemo();
  demo.start("research");
  runUntilStill(demo);
  assert.equal(demo.decide("changes"), true);
  assert.equal(demo.state.task.stage, "working");
  runUntilStill(demo);
  assert.equal(demo.state.task.stage, "needs", "asks again after the change");
  assert.equal(demo.decide("changes"), false, "only one round of changes");
  demo.decide("approve");
  runUntilStill(demo);
  assert.equal(demo.state.task.stage, "done");
  assert.equal(demo.state.task.done, demo.state.task.total);
  assert.match(demo.state.handoff.decided, /after one change/);
});

test("demo: typed tasks map to a scenario and keep the visitor's wording", () => {
  const { createDemo, pick } = load();
  assert.equal(pick("compare prices of my rivals and research them"), "research");
  assert.equal(pick("write a newsletter"), "draft");
  assert.equal(pick("something unusual"), "plan");
  const demo = createDemo();
  demo.start("Write a newsletter for May");
  assert.equal(demo.state.task.title, "Write a newsletter for May");
  assert.match(demo.state.activity[0].text, /simulated as/);
});

test("demo: offline agents are never given work and more than four agents exist", () => {
  const demo = load().createDemo();
  assert.ok(demo.state.agents.length > 4);
  demo.start("review"); runUntilStill(demo); demo.decide("approve"); runUntilStill(demo);
  const byId = Object.fromEntries(demo.state.agents.map(a => [a.id, a.state]));
  assert.equal(byId.gemini, "unavailable");
  assert.equal(byId.kimi, "paused");
});

test("page: honest labels, no invented quota numbers, software separate from the £5 reservation", () => {
  assert.match(html, /Your ideas\. Your agents\. <span>One place\.<\/span>/);
  assert.match(html, /Explore the demo/);
  assert.match(html, /How it works/);
  assert.match(html, /id="mission"/);
  assert.match(html, /Simulated agents/);
  assert.match(html, /Everything here is simulated/);
  assert.match(html, /Tasks and agents are illustrative/);
  assert.match(html, /separate from the &pound;5 reservation/);
  assert.doesNotMatch(html, /usage left|% left|revolutionis|supercharge|unlock/i);
  for (const id of ["fix", "research", "plan", "draft", "organise", "review"]) assert.match(html, new RegExp(`data-task="${id}"`));
  const js = readFileSync(join(root, "piblocraft/mission.js"), "utf8") + readFileSync(join(root, "piblocraft/demo-core.js"), "utf8");
  assert.doesNotMatch(js, /fetch\(|XMLHttpRequest|sendBeacon|innerHTML/, "demo never sends data or injects HTML");
});
