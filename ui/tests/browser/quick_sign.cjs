// Browser check for one-step signing: Sign on the plan page falls back to the Sign screen in
// a fresh browser, and signs in one step once the browser is set up and the box trusts its
// root. Needs the fixture server:
//   PYTHONPATH=ui uv run --project ui python ui/tests/browser/sign_server.py
// Then: PLAYWRIGHT_MODULE=/path/to/node_modules/playwright node ui/tests/browser/quick_sign.cjs
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = 'http://127.0.0.1:8104';
const dir = process.env.SIGN_TEST_DIR || '/tmp/aiab-sign-browser-test';

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({acceptDownloads: true});
  const page = await context.newPage();
  page.on('pageerror', e => console.log('PAGE ERROR', e.message));
  try {
    await page.goto(base + '/login');
    await page.locator('#username').fill('signer');
    await page.locator('#password').fill('browser-test-password');
    await page.getByRole('button', {name: 'Log in', exact: true}).click();

    // 1. Fresh browser: Sign on the plan lands on the Sign screen, told what is missing.
    await page.goto(base + '/a/bio/plan');
    const sign = page.locator('a.quick-sign');
    assert.equal(await sign.textContent(), 'Sign');
    assert.match(await page.locator('body').innerText(), /lab: not signed/i);
    await sign.click();
    await page.waitForURL(/\/a\/bio\/sign\?why=none$/);
    await page.locator('.notice', {hasText: 'Signing is not set up in this browser yet'}).waitFor();
    console.log('1 ok: fresh browser falls back to', page.url());

    // 2. Set up signing there; the key file holds the root public key the box must trust.
    const box = page.locator('.browser-key[data-party="lab"]');
    await box.locator('#make-password-lab').fill('pw-for-test');
    await box.locator('#make-again-lab').fill('pw-for-test');
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      box.getByRole('button', {name: 'Set up signing', exact: true}).click(),
    ]);
    const keyFile = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
    // Decline submitting the root through GitHub (no GitHub here); trust it by hand instead.
    await page.locator('#confirm').getByRole('button', {name: 'Cancel', exact: true}).click();
    const trust = JSON.parse(fs.readFileSync(dir + '/trust.json', 'utf8'));
    trust.lab_key = keyFile.public_key;
    fs.writeFileSync(dir + '/trust.json', JSON.stringify(trust, null, 2));
    console.log('2 ok: set up; root', keyFile.public_key.slice(0, 12) + '… now trusted');

    // 3. Back on the plan, Sign signs in one step after the confirm with the full digest.
    await page.goto(base + '/a/bio/plan');
    const digest = await page.locator('a.quick-sign').getAttribute('data-digest');
    await page.locator('a.quick-sign').click();
    const dialog = page.locator('#confirm');
    await dialog.waitFor({state: 'visible'});
    assert.match(await dialog.innerText(), new RegExp('digest ' + digest));
    await dialog.getByRole('button', {name: 'Continue', exact: true}).click();
    await page.waitForURL(/\/a\/bio\/plan\?msg=/);
    const text = await page.locator('body').innerText();
    assert.match(text, /lab signature verified and stored/);
    assert.match(text, /lab: signed this version/i);
    assert.equal(await page.locator('a.quick-sign').count(), 0);
    await page.getByRole('button', {name: 'Re-sign', exact: true}).waitFor();
    console.log('3 ok: signed from the plan page, returned to', page.url());
    assert.match(await (await page.goto(base + '/agreements')).text(), /You signed this version">Signed/);

    // 4. Re-sign on the plan page removes the signature and returns there.
    await page.goto(base + '/a/bio/plan');
    await page.getByRole('button', {name: 'Re-sign', exact: true}).click();
    await page.locator('#confirm').getByRole('button', {name: 'Continue', exact: true}).click();
    await page.waitForURL(/\/a\/bio\/plan\?msg=/);
    assert.match(await page.locator('body').innerText(), /Your signature is removed/);
    await page.locator('a.quick-sign').waitFor();
    console.log('4 ok: re-sign returns to the plan');

    // 5. Sign from the agreements list, same one step, back to the list.
    await page.goto(base + '/agreements');
    await page.locator('a.quick-sign').click();
    await page.locator('#confirm').getByRole('button', {name: 'Continue', exact: true}).click();
    await page.waitForURL(/\/agreements\?msg=/);
    assert.match(await page.locator('body').innerText(), /lab signature verified and stored/);
    console.log('5 ok: signed from the agreements list');

    // 6. A browser whose key the box stopped trusting falls back with why=stale.
    trust.lab_key = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=';
    fs.writeFileSync(dir + '/trust.json', JSON.stringify(trust, null, 2));
    await page.goto(base + '/a/bio/plan');
    await page.locator('a.quick-sign').click();
    await page.waitForURL(/\/a\/bio\/sign\?why=(stale|waiting)$/);
    console.log('6 ok: untrusted key falls back to', page.url());
    console.log('ALL OK');
  } catch (e) {
    console.log('FAILED at', page.url());
    console.log(e.stack || e);
    await page.screenshot({path: dir + '/quick-sign-failure.png', fullPage: true});
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
