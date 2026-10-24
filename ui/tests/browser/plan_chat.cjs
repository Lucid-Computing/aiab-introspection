/* Requires Playwright and Chrome; see ui/README.md for the fixture and command. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const base = 'http://127.0.0.1:8103';

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args:['--no-sandbox']});
  try {
    for (const width of [1280, 390]) {
      const context = await browser.newContext({viewport:{width, height:900}});
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      const id = `review-${width}-${Date.now()}`;
      await page.goto(base + '/login');
      await page.locator('#username').fill('author');
      await page.locator('#password').fill('browser-test-password');
      await page.getByRole('button', {name:'Log in', exact:true}).click();
      await page.goto(base + '/agreements/new');
      await page.locator('#plan_id').fill(id);
      await page.locator('#problem').fill('Detect credential theft');
      await page.getByRole('button', {name:'Create with AI', exact:true}).click();
      await page.locator('#confirm').getByRole('button', {name:'Continue', exact:true}).click();
      await page.waitForURL(`**/a/${id}/plan?**`);
      const input = page.locator('#chat-input');
      const users = page.locator('.chat-message[data-state]');
      const starters = page.locator('.chat-starters');
      const endpoint = `**/a/${id}/plan/chat`;
      const send = async text => {
        await input.fill(text);
        await input.press(width === 1280 ? 'Control+Enter' : 'Meta+Enter');
        await page.waitForFunction(() => !document.getElementById('chat-input').disabled);
      };
      // Seeded, typed and even whitespace-only composer content must never be overwritten.
      assert(await starters.isHidden());
      for (const draft of ['A draft I have not sent', ' ']) {
        await input.fill(draft);
        assert(await starters.isHidden());
        await page.locator('[data-chat-starter]').first().evaluate(button => button.click());
        assert.equal(await input.inputValue(), draft);
      }
      await input.fill('');
      assert(await starters.isVisible());
      assert(!(await page.locator('#plan-chat').innerText()).includes('bill for'));
      await page.locator('[data-chat-starter]').first().click();
      assert((await input.inputValue()).includes('credential theft'));
      assert(await starters.isHidden());
      await input.press('Enter');
      assert((await input.inputValue()).endsWith('\n'));
      assert.equal(await users.count(), 0); // Plain Enter never submits.
      await page.locator('#chat-send').click();
      await page.locator('#chat-thinking').waitFor({state:'visible'});
      assert.equal(await users.count(), 1); // User message is immediate.
      assert(await page.locator('#chat-stop').isVisible());
      await page.locator('#chat-proposal').waitFor({state:'visible'});
      await page.waitForFunction(() => !document.getElementById('chat-input').disabled);
      assert.equal(await input.evaluate(el => document.activeElement === el), true);
      assert.equal(await page.locator('#chat-file-count').innerText(), '2 changed files');
      assert((await page.locator('#chat-file-list').innerText()).includes('plan.json'));
      assert((await page.locator('#chat-file-list').innerText()).includes('prompts/classifier_v1.txt'));
      assert((await page.locator('#chat-validation').innerText()).includes('Behavioral evaluation has not run'));
      assert.equal(await page.locator('.chat-file[open]').count(), 1);
      await page.locator('.chat-file summary').nth(1).click();
      assert.equal(await page.locator('.chat-file[open]').count(), 2);
      assert(await page.locator('.diff-added').count() > 0);
      assert(await page.locator('.diff-removed').count() > 0);
      assert.equal(await page.locator('#chat-files script').count(), 0); // Untrusted diff is text.

      // Revision works without applying or discarding, and carries both changed files.
      await page.locator('#chat-revise').click();
      await input.press('Control+Enter');
      await page.waitForFunction(() => !document.getElementById('chat-input').disabled);
      assert((await page.locator('#chat-files').innerText()).includes('0.95'));
      assert.equal(await page.locator('#chat-file-count').innerText(), '2 changed files');
      assert((await page.locator('#plan-details').innerText()).includes('0.7'));
      assert.equal(await users.count(), 2);

      // New chat and navigation require confirmation while there is unsaved context.
      let resetPrompt = false;
      page.once('dialog', async dialog => { resetPrompt = true; await dialog.dismiss(); });
      await page.locator('#chat-reset').click();
      assert(resetPrompt);
      assert.equal(await users.count(), 2);
      const navigationPrompt = page.waitForEvent('dialog');
      const navigation = page.goto(base + '/agreements').catch(() => null);
      const dialog = await navigationPrompt;
      assert.equal(dialog.type(), 'beforeunload');
      await dialog.dismiss();
      await navigation;
      assert(page.url().includes(`/a/${id}/plan`));

      // Invalid generated files remain visible and can be repaired in the next turn.
      await send('Make an invalid proposal');
      assert(await page.locator('#chat-apply').isDisabled());
      assert((await page.locator('#chat-files').textContent()).includes('import os'));
      await page.locator('#chat-revise').click();
      assert((await input.inputValue()).includes('Repair'));
      await input.press('Control+Enter');
      await page.waitForFunction(() => !document.getElementById('chat-input').disabled);
      assert(await page.locator('#chat-apply').isEnabled());
      assert(!(await page.locator('#chat-files').textContent()).includes('import os'));
      assert.equal(await page.locator('#chat-file-count').innerText(), '2 changed files');

      await page.screenshot({path:`/tmp/aiab-plan-chat-round1-${width}.png`, fullPage:true});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.locator('#chat-discard').click();
      await send('State check');
      assert((await page.locator('#chat-messages').innerText()).includes('Proposal discarded'));
      assert((await page.locator('.chat-message').last().innerText()).includes('"has_candidate": false'));

      // Cancellation preserves retryable input, and retry reuses its visible message.
      const beforeStop = await users.count();
      await input.fill('slow request');
      await page.locator('#chat-send').click();
      await page.locator('#chat-thinking').waitFor({state:'visible'});
      await page.locator('#chat-stop').click();
      await page.waitForFunction(() => !document.getElementById('chat-input').disabled);
      assert((await page.locator('#chat-status').innerText()).includes('bill for it'));
      assert((await users.last().evaluate(el => getComputedStyle(el, '::after').content)).includes('No reply received'));
      assert.equal(await input.inputValue(), 'slow request');
      assert.equal(await users.count(), beforeStop + 1);
      await send('Retry as a normal request');
      assert.equal(await users.count(), beforeStop + 1);
      assert(!(await page.locator('#plan-chat').innerText()).includes('bill for'));

      // Expired sessions preserve message and candidate and expose a sign-in link.
      const beforeExpiry = await users.count();
      await page.route(endpoint, route => route.fulfill({status:401, contentType:'application/json', body:'{"error":"expired"}'}));
      await send('Please revise this again');
      assert((await page.locator('#chat-status').innerText()).includes('session expired'));
      assert((await users.last().evaluate(el => getComputedStyle(el, '::after').content)).includes('No reply received'));
      assert.equal(await input.inputValue(), 'Please revise this again');
      assert.equal(await page.locator('#chat-session a').getAttribute('target'), '_blank');
      assert(await page.locator('#chat-session a').isVisible());
      assert(await page.evaluate(() => Object.values(sessionStorage).includes('Please revise this again')));
      await page.unroute(endpoint);
      await input.press('Control+Enter');
      await page.waitForFunction(() => !document.getElementById('chat-input').disabled);
      assert.equal(await users.count(), beforeExpiry + 1);
      assert(await page.locator('#chat-session').isHidden());

      // Applying keeps the conversation and refreshes the authoritative plan details.
      const beforeApply = await users.count();
      await page.locator('#chat-apply').click();
      await page.waitForFunction(() => !document.getElementById('chat-input').disabled);
      assert.equal(await users.count(), beforeApply);
      assert((await page.locator('#chat-messages').innerText()).includes('Proposal applied to the draft'));
      assert((await page.locator('#plan-details').innerText()).includes('0.85'));
      assert(await page.locator('#chat-proposal').isHidden());
      await send('State check');
      assert((await page.locator('.chat-message').last().innerText()).includes('Proposal applied'));
      assert((await page.locator('.chat-message').last().innerText()).includes('"has_candidate": false'));
      page.once('dialog', dialog => dialog.accept());
      await page.locator('#chat-reset').click();
      assert.equal(await users.count(), 0);
      assert.equal(await input.inputValue(), '');
      assert(await starters.isVisible());
      assert.deepEqual(errors, []);
      await page.goto(base + '/agreements'); // No warning after clearing the conversation.
      await context.close();
      console.log(`PASS ${width}px: iteration, repair, review, immediate feedback, shortcuts, retry, cancel, navigation, expiry, apply state`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
