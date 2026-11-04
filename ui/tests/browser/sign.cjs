/* The Sign screen in a real browser: make a key, have production trust it (by writing the
   fixture's trust file), sign, see the signature stored; then forget the key and bring it
   back from the backup file. Requires Playwright and Chrome; sign_server.py must be running. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8104';
const dir = process.env.SIGN_TEST_DIR || '/tmp/aiab-sign-browser-test';

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox']});
  try {
    const context = await browser.newContext({acceptDownloads: true});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + '/login');
    await page.locator('#username').fill('signer');
    await page.locator('#password').fill('browser-test-password');
    await page.getByRole('button', {name: 'Log in', exact: true}).click();
    await page.goto(base + '/a/bio/sign');
    const box = page.locator('.browser-key[data-party="lab"]');
    await box.locator('[data-state="none"]').waitFor({state: 'visible'});
    const digest = await box.getAttribute('data-digest');
    assert.match(digest, /^sha256:[0-9a-f]{64}$/);

    // Make a key: the backup downloads, the key appears, production does not trust it yet.
    await box.locator('#make-password-lab').fill('backup-pw');
    await box.locator('#make-again-lab').fill('backup-pw');
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      box.getByRole('button', {name: 'Make a key in this browser', exact: true}).click(),
    ]);
    const backupPath = path.join(dir, 'backup.json');
    await download.saveAs(backupPath);
    const backup = JSON.parse(fs.readFileSync(backupPath, 'utf8'));
    assert.equal(backup.format, 'aiab-signing-key/v1');
    await box.locator('[data-state="ready"]').waitFor({state: 'visible'});
    const publicKey = (await box.locator('.browser-public-key').textContent()).trim();
    assert.equal(publicKey, backup.public_key);
    assert.match(publicKey, /^[A-Za-z0-9+/]{43}=$/);
    assert(await box.locator('.browser-sign').isDisabled());
    assert(await box.locator('.browser-key-submit').isVisible());

    // Production trusts it (the fixture's trust file stands in for GitHub), so Sign works.
    const trustPath = path.join(dir, 'trust.json');
    const trust = JSON.parse(fs.readFileSync(trustPath, 'utf8'));
    trust.lab_key = publicKey;
    fs.writeFileSync(trustPath, JSON.stringify(trust, null, 2) + '\n');
    await page.reload();
    await box.locator('[data-state="ready"]').waitFor({state: 'visible'});
    assert(await box.locator('.browser-sign').isEnabled());
    await box.locator('.browser-sign').click();
    const dialog = page.locator('#confirm[open]');
    await dialog.waitFor({state: 'visible'});
    assert.match(await dialog.locator('#confirm-text').textContent(), new RegExp(digest));
    await Promise.all([
      page.waitForURL('**/a/bio/sign?**'),
      dialog.getByRole('button', {name: 'Continue', exact: true}).click(),
    ]);
    assert.match(await page.locator('.form-message.success').textContent(), /lab signature verified and stored/);
    const stored = fs.readFileSync(path.join(dir, 'agreements/bio/signatures/lab.sig'), 'utf8').trim();
    assert.match(stored, /^[A-Za-z0-9+/]{86}==$/);
    const labCard = page.locator('.card', {has: page.locator('form[action="/a/bio/sign/remove"]')});
    assert.match(await labCard.locator('.badge').textContent(), /signed this version/);

    // Re-sign: the key block is back. Forget the key, import the backup, same public key.
    await page.getByRole('button', {name: 'Re-sign', exact: true}).click();
    await Promise.all([
      page.waitForURL('**/a/bio/sign?**'),
      page.locator('#confirm').getByRole('button', {name: 'Continue', exact: true}).click(),
    ]);
    await box.locator('[data-state="ready"]').waitFor({state: 'visible'});
    await box.locator('.browser-forget').click();
    await page.locator('#confirm[open]').waitFor({state: 'visible'});
    await page.locator('#confirm').getByRole('button', {name: 'Continue', exact: true}).click();
    await box.locator('[data-state="none"]').waitFor({state: 'visible'});
    await box.locator('details', {hasText: 'Already have a backup file'}).locator('summary').click();
    await box.locator('#import-file-lab').setInputFiles(backupPath);
    await box.locator('#import-password-lab').fill('wrong');
    await box.getByRole('button', {name: 'Import into this browser', exact: true}).click();
    await page.locator('.browser-key-note', {hasText: 'wrong password'}).waitFor({state: 'visible'});
    await box.locator('#import-file-lab').setInputFiles(backupPath);
    await box.locator('#import-password-lab').fill('backup-pw');
    await box.getByRole('button', {name: 'Import into this browser', exact: true}).click();
    await box.locator('[data-state="ready"]').waitFor({state: 'visible'});
    assert.equal((await box.locator('.browser-public-key').textContent()).trim(), publicKey);
    assert.deepEqual(errors, []);
    console.log('browser signing: ok', {publicKey, digest});
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
