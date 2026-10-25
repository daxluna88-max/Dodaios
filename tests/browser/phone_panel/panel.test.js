// Browser tests for the phone panel against a real server (see server.py).
// Run: node panel.test.js <base url> <control key>   (needs the `playwright` package)
"use strict";
const { chromium, devices } = require("playwright");

const BASE = process.argv[2];
const KEY = process.argv[3];
const results = [];

async function owner(path, options = {}) {
  const res = await fetch(BASE + path, {
    ...options, headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json", Origin: BASE, ...(options.headers || {}) },
  });
  return res.json();
}

async function pairedPhone(browser, name = "iPhone", extra = {}) {
  const { code } = await owner("/api/phone-admin/pairing", { method: "POST" });
  const ctx = await browser.newContext({ ...devices["iPhone 13"], ...extra });
  const page = await ctx.newPage();
  page.on("dialog", (d) => d.accept());
  page.on("pageerror", (e) => { throw new Error(`page error: ${e.message}`); });
  await page.goto(`${BASE}/m/`);
  await page.fill('input[placeholder^="Codice"]', code);
  await page.fill('input[placeholder="Nome del telefono"]', name);
  await page.click('button:text-is("Collega")');
  await page.waitForSelector("text=Chat A");
  return { ctx, page };
}

/** Hold matching requests until `release()` is called; returns {release, seen}. */
async function hold(page, pattern, answer) {
  const held = [];
  const gate = {};
  gate.seen = () => held.length;
  gate.release = async () => { for (const r of held.splice(0)) await answer(r); };
  await page.route(pattern, (route) => held.push(route));
  return gate;
}

const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const test = async (name, fn) => {
  try { await fn(); results.push([name, true]); console.log(`ok   ${name}`); }
  catch (error) { results.push([name, false]); console.log(`FAIL ${name}: ${error.message}`); }
};
const assert = (cond, message) => { if (!cond) throw new Error(message); };

(async () => {
  const browser = await chromium.launch();

  await test("a poll answered after sign-out never renders or reschedules", async () => {
    const { ctx, page } = await pairedPhone(browser);
    const sessions = await page.evaluate(async () => (await (await fetch("/api/phone/chats")).json()).sessions);
    const a = sessions.find((s) => s.title === "Chat A");
    const real = await page.evaluate(async (sid) => (await fetch(`/api/phone/chats/${sid}`)).json(), a.session_id);
    const gate = await hold(page, `**/api/phone/chats/${a.session_id}`, (route) => json(route, real));
    await page.click("text=Chat A");
    await page.waitForFunction(() => true);
    for (let i = 0; i < 50 && gate.seen() === 0; i++) await page.waitForTimeout(100);
    assert(gate.seen() === 1, "the poll was not in flight");
    await page.click("#logout");
    await page.waitForSelector("text=Collega");
    let polls = 0;
    page.on("request", (r) => { if (/\/api\/phone\/chats\//.test(r.url())) polls++; });
    await gate.release();
    await page.waitForTimeout(2500);
    const text = await page.locator("body").innerText();
    assert(!text.includes("segreto-di-A"), "private events were rendered after sign-out");
    assert(text.includes("Collega"), "the pairing screen was replaced");
    assert(polls === 0, `a poll was rescheduled after sign-out (${polls})`);
    await ctx.close();
  });

  await test("finishing a send in one chat leaves another chat's draft and files alone", async () => {
    const { ctx, page } = await pairedPhone(browser);
    const sessions = await page.evaluate(async () => (await (await fetch("/api/phone/chats")).json()).sessions);
    const id = (t) => sessions.find((s) => s.title === t).session_id;
    const gate = await hold(page, `**/api/phone/chats/${id("Chat A")}/messages`, (route) => json(route, { turn_id: "t" }, 202));
    await page.click("text=Chat A");
    await page.fill("#input", "messaggio-A");
    await page.click("#send");
    for (let i = 0; i < 50 && gate.seen() === 0; i++) await page.waitForTimeout(100);
    assert(gate.seen() === 1, "A's message was not sent");
    await page.click("#back");
    await page.click("text=Chat B");
    await page.fill("#input", "bozza-di-B");
    await page.setInputFiles("#file", { name: "b.txt", mimeType: "text/plain", buffer: Buffer.from("dati di B") });
    await page.waitForSelector('.chip:has-text("b.txt ✓")', { timeout: 15000 });
    await gate.release();
    await page.waitForTimeout(800);
    assert((await page.inputValue("#input")) === "bozza-di-B", "B's draft was cleared by A's send");
    assert(await page.locator('.chip:has-text("b.txt")').count() === 1, "B's staged file was dropped by A's send");
    // And A's own draft was cleared where it belongs.
    await page.click("#back");
    await page.click("text=Chat A");
    assert((await page.inputValue("#input")) === "", "A's sent text is still in its composer");
    await ctx.close();
  });

  await test("an active upload can be cancelled and the server copy is deleted", async () => {
    const { ctx, page } = await pairedPhone(browser);
    await page.click("#tab-files");
    const gate = await hold(page, "**/chunk?offset=*", () => {});
    let deleted = 0;
    page.on("request", (r) => { if (r.method() === "DELETE" && /\/api\/phone\/files\//.test(r.url())) deleted++; });
    await page.setInputFiles("input[type=file] >> nth=0", { name: "grande.bin", mimeType: "application/octet-stream", buffer: Buffer.alloc(2 * 1024 * 1024, 7) });
    await page.waitForSelector('#uploads .row:has-text("grande.bin")');
    for (let i = 0; i < 100 && gate.seen() === 0; i++) await page.waitForTimeout(100);
    assert(gate.seen() === 1, "the upload never reached the chunk step");
    await page.click('#uploads button:text-is("Annulla")');
    await page.waitForFunction(() => !document.querySelector("#uploads .row"));
    await page.waitForTimeout(300);
    assert(deleted === 1, `the server copy was not deleted (${deleted})`);
    const files = await page.evaluate(async () => (await (await fetch("/api/phone/files")).json()).files);
    assert(!files.some((f) => f.name === "grande.bin"), "an abandoned transfer is still listed");
    await ctx.close();
  });

  await test("an upload in flight at sign-out does not carry on under a newly paired device", async () => {
    const { ctx, page } = await pairedPhone(browser);
    await page.click("#tab-files");
    const gate = await hold(page, "**/chunk?offset=*", () => {});
    await page.setInputFiles("input[type=file] >> nth=0", { name: "x.bin", mimeType: "application/octet-stream", buffer: Buffer.alloc(9 * 1024 * 1024, 1) });
    for (let i = 0; i < 100 && gate.seen() === 0; i++) await page.waitForTimeout(100);
    await page.click("#logout");
    await page.waitForSelector("text=Collega");
    const { code } = await owner("/api/phone-admin/pairing", { method: "POST" });
    let chunks = 0;
    page.on("request", (r) => { if (/chunk\?offset=/.test(r.url())) chunks++; });
    await page.fill('input[placeholder^="Codice"]', code);
    await page.click('button:text-is("Collega")');
    await page.waitForSelector("text=Chat A");
    await gate.release();
    await page.waitForTimeout(2000);
    assert(chunks === 0, `the old upload kept sending chunks (${chunks})`);
    await ctx.close();
  });

  await test("the service worker deletes only its own old caches", async () => {
    const ctx = await browser.newContext({ ...devices["iPhone 13"] });
    const page = await ctx.newPage();
    await page.goto(`${BASE}/m/pair.html`);
    await page.evaluate(async () => {
      await caches.open("other-app-v1");
      await caches.open("jarvis-panel-v1");
    });
    await page.goto(`${BASE}/m/`);
    await page.waitForFunction(async () => (await navigator.serviceWorker.ready).active && (await caches.keys()).includes("jarvis-panel-v3"), null, { timeout: 15000 });
    await page.waitForFunction(async () => !(await caches.keys()).includes("jarvis-panel-v1"), null, { timeout: 15000 });
    const keys = await page.evaluate(() => caches.keys());
    assert(keys.includes("other-app-v1"), `another app's cache was deleted: ${keys}`);
    assert(!keys.includes("jarvis-panel-v1"), "the panel's old cache was kept");
    await ctx.close();
  });

  await browser.close();
  const failed = results.filter(([, ok]) => !ok).length;
  console.log(failed ? `${failed} FAILED` : "ALL PASSED");
  process.exit(failed ? 1 : 0);
})().catch((error) => { console.error("HARNESS ERROR", error); process.exit(2); });
