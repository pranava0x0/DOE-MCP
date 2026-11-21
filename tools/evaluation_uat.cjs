'use strict';
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const base = (process.env.UAT_URL || 'http://127.0.0.1:4192').replace(/\/$/, '');
const output = path.resolve(process.env.UAT_OUTPUT_DIR || path.join(root, 'uat-screenshots'));
fs.mkdirSync(output, {recursive: true});
const report = {kind: 'evaluation-matrix-browser-uat', base, at: new Date().toISOString(),
  limitations: ['Local static pages, scripted Chromium and fixed viewports; not physical-device or human testing.',
                'No model, reviewer, publisher or provider is queried. Example token counts are simulated.'],
  journeys: [], errors: [], blockedExternal: []};
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.UAT_BROWSER_EXECUTABLE_PATH || undefined});
  report.browser = browser.version();
  try {
    for (const viewport of [{width: 375, height: 812}, {width: 768, height: 1024}, {width: 1280, height: 800}]) {
      const context = await browser.newContext({viewport, reducedMotion: 'reduce', acceptDownloads: true});
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== new URL(base).origin) {
          report.blockedExternal.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', error => report.errors.push(error.message));
      page.on('response', response => {
        if (response.status() >= 400) report.errors.push(`${response.status()}: ${response.url()}`);
      });
      const screenshot = async state => page.screenshot({path: path.join(output,
        `2026-11-21_evaluation-${viewport.width}-${state}.png`), fullPage: false});
      await page.goto(`${base}/guide.html#evaluate-agent-behavior`);
      await page.getByRole('heading', {name: 'Evaluate agent behavior', exact: true}).waitFor();
      await screenshot('guide');
      await page.getByRole('link', {name: 'Compare existing reports offline', exact: true}).click();
      await page.getByRole('heading', {name: 'Compare evaluation reports offline', exact: true}).waitFor();
      assert((await page.locator('main').innerText()).includes('It does not invoke a'));
      await screenshot('contract');
      await page.getByRole('link', {name: 'Inspect the deterministic driver demonstration', exact: true}).click();
      await page.getByRole('heading', {name: 'Evaluation matrix demonstration', exact: true}).waitFor();
      assert.equal(await page.getByText('Unknown: no reviews', {exact: true}).count(), 4);
      assert.equal(await page.getByText('Unknown: no prices', {exact: true}).count(), 4);
      assert((await page.locator('main').innerText()).includes('No language model, reviewer or provider was called'));
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'page overflow');
      if (viewport.width === 375) {
        assert(await page.locator('table').evaluate(table => table.scrollWidth <= table.clientWidth + 1), 'phone table overflow');
      }
      await screenshot('example');
      await page.locator('table').scrollIntoViewIfNeeded();
      await screenshot('table');
      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('link', {name: 'Download the complete comparison bundle', exact: true}).click();
      const download = await downloadPromise;
      assert.equal(download.suggestedFilename(), 'evaluation-matrix.zip');
      const downloaded = fs.readFileSync(await download.path());
      assert.equal(sha(downloaded), sha(fs.readFileSync(path.join(root, 'docs/data/evaluation-matrix/bundle.zip'))));
      const csvPromise = page.waitForEvent('download');
      await page.getByRole('link', {name: 'Matrix CSV', exact: true}).click();
      const csv = await csvPromise;
      assert.equal(sha(fs.readFileSync(await csv.path())), sha(fs.readFileSync(path.join(root, 'docs/data/evaluation-matrix/matrix.csv'))));
      await page.goto(`${base}/evaluation-example.html`);
      await page.keyboard.press('Tab');
      assert.equal(await page.locator(':focus').innerText(), 'Skip to content');
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(':focus').getAttribute('id'), 'content');
      await screenshot('keyboard');
      report.journeys.push({viewport, guideToMatrixToExample: true, zipVerified: true, csvVerified: true,
                            keyboardSkipLink: true, pageOverflow: false});
      await context.close();
    }
    assert.equal(report.errors.length, 0);
    assert.equal(report.blockedExternal.length, 0);
    console.log(JSON.stringify({browser: report.browser, journeys: report.journeys.length, errors: report.errors.length}));
  } catch (error) {
    report.failure = error.stack;
    throw error;
  } finally {
    fs.writeFileSync(path.join(output, 'evaluation-matrix.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
