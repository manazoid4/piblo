// Run with the installed Playwright skill runner; no site dependency is needed.
const { chromium } = require('playwright');
const { createServer } = require('node:http');
const { readFile } = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const root = path.resolve(process.env.PIBLO_SITE_ROOT || 'site');
  const { default: handler } = await import(pathToFileURL(path.join(root, 'api/waitlist.js')));
  process.env.RESEND_API_KEY = 'local-test-only';
  process.env.RESEND_SEGMENT_ID = 'piblo-test-segment';
  process.env.SIGNUP_NOTIFY_EMAIL = '';
  const contacts = [];
  const server = createServer(async (req, res) => {
    if (req.url === '/api/waitlist') return handler(req, res, async (url, opts) => {
      contacts.push(JSON.parse(opts.body));
      return new Response('{}', {status: 200});
    });
    try {
      const filename = path.join(root, new URL(req.url, 'http://localhost').pathname === '/' ? 'index.html' : decodeURIComponent(req.url.split('?')[0]));
      const types = {'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml','.webp':'image/webp','.woff2':'font/woff2'};
      res.setHeader('content-type', types[path.extname(filename)] || 'application/octet-stream');
      res.end(await readFile(filename));
    } catch { res.statusCode = 404; res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    await page.goto(`http://127.0.0.1:${server.address().port}/?utm_source=reddit`);
    const submit = page.locator('#waitlist-form button[type=submit]');
    await submit.click();
    assert.match(await page.locator('.waitlist-status').innerText(), /tick the box/);
    await page.getByLabel('Email', {exact:true}).fill('bad-address');
    await page.locator('input[name=consent]').check();
    await submit.click();
    assert.equal(await page.getByLabel('Email', {exact:true}).getAttribute('aria-invalid'), 'true');
    assert.equal(contacts.length, 0);
    await page.getByLabel('Email', {exact:true}).fill('browser@example.invalid');
    await submit.click();
    await page.getByText("You're on the list.", {exact:false}).waitFor();
    assert.equal(contacts.length, 1);
    assert.equal(contacts[0].email, 'browser@example.invalid');
    assert.deepEqual(contacts[0].segments, [{id:'piblo-test-segment'}]);
    assert.ok(await page.locator('#share-box').isVisible());
    assert.equal(await page.locator('input[name=source]').inputValue(), 'reddit');
    await page.goto(`http://127.0.0.1:${server.address().port}/?utm_source=unknown`);
    assert.equal(await page.locator('input[name=source]').inputValue(), '');
    console.log('PASS: browser -> real waitlist handler -> isolated provider; consent, invalid email, correct segment, success and referral');
    await page.reload();
    for (const width of [360,390,768,1280]) {
      await page.setViewportSize({width,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth), false);
      await page.locator('.buddy-stage img').scrollIntoViewIfNeeded();
      await page.locator('.buddy-stage img').evaluate(img=>img.decode());
      await page.locator('.founder-photo').scrollIntoViewIfNeeded();
      await page.locator('.founder-photo').evaluate(img=>img.decode());
      await page.evaluate(()=>scrollTo(0,0));
      await page.screenshot({path:path.join(require('node:os').tmpdir(),`piblo-conversion-${width}.png`),fullPage:true});
    }
    console.log('PASS: four responsive widths; screenshots saved to OS temp');
  } finally { await browser.close(); await new Promise(resolve=>server.close(resolve)); }
})().catch(e=>{console.error(e);process.exitCode=1;});
