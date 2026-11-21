'use strict';
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const docs = path.join(root, 'docs');
const base = (process.env.UAT_URL || 'http://127.0.0.1:4192').replace(/\/$/, '');
const output = path.resolve(process.env.UAT_OUTPUT_DIR || path.join(root, 'uat-screenshots'));
const rounds = Number(process.env.UAT_STABILITY_ROUNDS || 3);
assert(Number.isInteger(rounds) && rounds >= 2 && rounds <= 10, 'UAT_STABILITY_ROUNDS must be 2–10');
fs.mkdirSync(output, {recursive: true});
const report = {
  kind: 'scripted-browser-benchmark', base, at: new Date().toISOString(),
  limitations: [
    'Scripted desktop Chromium at fixed viewport sizes; no human participant or physical-device study.',
    'Clicks are trusted browser click events. Wheel actions are actual issued wheel events, not inferred swipes.',
    'Scroll pixels include browser focus and anchor movement; events and explicit wheel actions are separate.',
    'Document navigations include the initial load; history-only events are separate. Native select changes are scripted stability transitions.',
    'Keyboard actions count explicit scripted text-entry operations, not individual keystrokes.',
    'Useful-ready timings are local, cold-context diagnostics with 4x CPU slowdown, without network shaping.',
    'External resources are blocked and listed. Gzip sizes are calculated from local artifacts, not measured transfer sizes.',
    'DOM checkpoints compare the same selected panel. Chromium heap measurements are approximate and do not establish a leak.'
  ], journeys: [], stability: [], performance: [], errors: [], blockedExternal: [], payloads: [], hrefs: null
};
const requested = new Set();
function artifact(url) {
  const parsed = new URL(url);
  if (parsed.origin !== new URL(base).origin) return null;
  const relative = decodeURIComponent(parsed.pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(docs, relative);
  assert(file.startsWith(docs + path.sep), 'internal path escapes docs');
  return file;
}
async function instrument(context, metrics) {
  await context.exposeBinding('__auditEvent', (_, event) => {
    if (event.type === 'document') metrics.navigations++;
    if (event.type === 'click') metrics.clicks++;
    if (event.type === 'scroll') {
      metrics.scrollEvents++;
      metrics.scrollPixelsY += Math.abs(event.dy);
      metrics.scrollPixelsX += Math.abs(event.dx);
    }
    if (event.type === 'longtask') metrics.longTasks.push(event.ms);
  });
  await context.addInitScript(() => {
    if (/^https?:$/.test(location.protocol)) window.__auditEvent({type: 'document'});
    let x = scrollX, y = scrollY;
    document.addEventListener('click', event => {
      if (event.isTrusted) window.__auditEvent({type: 'click'});
    }, true);
    addEventListener('scroll', () => {
      window.__auditEvent({type: 'scroll', dx: scrollX - x, dy: scrollY - y});
      x = scrollX; y = scrollY;
    }, {passive: true});
    if (PerformanceObserver.supportedEntryTypes.includes('longtask')) {
      new PerformanceObserver(list => {
        for (const entry of list.getEntries()) window.__auditEvent({type: 'longtask', ms: entry.duration});
      }).observe({type: 'longtask', buffered: true});
    }
  });
}
function metrics() {
  return {clicks: 0, wheelActions: 0, keyboardActions: 0, scrollEvents: 0,
    scrollPixelsY: 0, scrollPixelsX: 0, navigations: 0, navigationEvents: 0,
    get sameDocumentTransitions() { return Math.max(0, this.navigationEvents - this.navigations); },
    longTasks: [], actions: []};
}
async function pageFor(browser, viewport, counters) {
  const context = await browser.newContext({viewport, reducedMotion: 'reduce', serviceWorkers: 'block'});
  await instrument(context, counters);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(base).origin) {
      report.blockedExternal.push(url.href);
      return route.abort('blockedbyclient');
    }
    requested.add(url.href);
    return route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => report.errors.push({type: 'pageerror', url: page.url(), message: error.message}));
  page.on('console', message => {
    if (message.type() === 'error' && !message.text().includes('ERR_BLOCKED_BY_CLIENT'))
      report.errors.push({type: 'console', url: page.url(), message: message.text()});
  });
  page.on('response', response => {
    if (response.status() >= 400 && new URL(response.url()).origin === new URL(base).origin)
      report.errors.push({type: 'http', url: response.url(), status: response.status()});
  });
  page.on('requestfailed', request => {
    if (new URL(request.url()).origin === new URL(base).origin)
      report.errors.push({type: 'requestfailed', url: request.url(), message: request.failure().errorText});
  });
  page.on('framenavigated', frame => {
    if (frame === page.mainFrame() && frame.url() !== 'about:blank') counters.navigationEvents++;
  });
  return {page, context};
}
async function settled(page) {
  await page.evaluate(() => new Promise((resolve, reject) => {
    let previous = scrollY, stable = 0;
    const started = performance.now();
    function frame() {
      stable = previous === scrollY ? stable + 1 : 0;
      previous = scrollY;
      if (stable >= 3) resolve();
      else if (performance.now() - started > 3000) reject(Error('scroll position did not settle'));
      else requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }));
}
async function click(page, locator, counters, label) {
  await locator.waitFor({state: 'visible'});
  for (let attempt = 0; attempt < 60; attempt++) {
    const point = await locator.evaluate(element => {
      // Wrapped inline links have several actual hit rectangles; their union
      // can put its centre on whitespace or a neighbouring link.
      for (const rect of element.getClientRects()) {
        const left = Math.max(4, rect.left), right = Math.min(innerWidth - 4, rect.right);
        const top = Math.max(4, rect.top), bottom = Math.min(innerHeight - 4, rect.bottom);
        for (const y of [(top + bottom) / 2, top + 6, bottom - 6]) {
          const x = (left + right) / 2;
          if (right > left && bottom > top && element.contains(document.elementFromPoint(x, y))) return {x, y};
        }
      }
      return null;
    });
    if (point) {
      counters.actions.push({type: 'click', label, x: point.x, y: point.y});
      await page.mouse.click(point.x, point.y);
      return;
    }
    const before = await page.evaluate(() => scrollY);
    const box = await locator.boundingBox();
    assert(box, 'missing target: ' + label);
    const delta = (box.y < page.viewportSize().height / 2 ? -1 : 1) * Math.round(page.viewportSize().height * 0.7);
    counters.wheelActions++;
    counters.actions.push({type: 'wheel', label: 'reveal ' + label, deltaY: delta});
    await page.mouse.move(page.viewportSize().width / 2, page.viewportSize().height / 2);
    await page.mouse.wheel(0, delta);
    await settled(page);
    assert.notEqual(await page.evaluate(() => scrollY), before, 'cannot reveal target by scrolling: ' + label);
  }
  throw Error('scroll action limit reached: ' + label);
}
async function readyDemo(page, id) {
  await page.waitForFunction(id => {
    const panel = document.getElementById(id);
    return document.getElementById('workflow')?.value === id && panel && !panel.hidden
      && panel.querySelector('.answer')?.textContent.trim()
      && [...document.querySelectorAll('article')].filter(article => !article.hidden).length === 1;
  }, id);
  await settled(page);
  const size = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
  assert(size.scroll <= size.width + 1, 'whole-page horizontal overflow');
}
async function jump(page, label, expected, counters) {
  await click(page, page.getByRole('button', {name: label, exact: true}), counters, label);
  await page.waitForFunction(id => document.activeElement.id === id, expected);
  await settled(page);
  const state = await page.evaluate(() => ({top: document.activeElement.getBoundingClientRect().top,
    toolbar: document.getElementById('picker').getBoundingClientRect().bottom, height: innerHeight}));
  assert(state.top >= state.toolbar - 1 && state.top < state.height, 'jump destination hidden by controls');
}
async function download(page, locator, counters, label) {
  const [file] = await Promise.all([page.waitForEvent('download'), click(page, locator, counters, label)]);
  assert.equal(await file.failure(), null, 'download failed');
  const bytes = fs.readFileSync(await file.path());
  assert(bytes.length > 0, 'empty download');
  requested.add(new URL(await locator.getAttribute('href'), page.url()).href);
  return {name: file.suggestedFilename(), bytes};
}
async function shortcutJourney(browser, viewport, task) {
  const counters = metrics();
  const {page, context} = await pageFor(browser, viewport, counters);
  try {
    await page.goto(base + '/index.html', {waitUntil: 'domcontentloaded'});
    const href = {install: '#start-install', coverage: '#registry-sources', guide: 'guide.html'}[task];
    await click(page, page.locator('.atlas-actions a[href="' + href + '"]'), counters, task + ' shortcut');
    if (task === 'install') {
      await page.locator('#start-install pre').waitFor({state: 'visible'});
      assert.match(await page.locator('#start-install').innerText(), /pipx? install \./);
      const target = await page.evaluate(() => ({top: document.getElementById('start-install').getBoundingClientRect().top,
        navBottom: document.querySelector('nav.toc').getBoundingClientRect().bottom, height: innerHeight}));
      assert(target.top >= target.navBottom - 1 && target.top < target.height,
        'Install destination is hidden by sticky navigation');
    } else if (task === 'coverage') {
      await click(page, page.locator('#q'), counters, 'filter source coverage');
      await page.keyboard.type('eia');
      counters.keyboardActions++;
      counters.actions.push({type: 'type', label: 'filter source coverage', text: 'eia'});
      await page.locator('#reg').waitFor({state: 'visible'});
      assert(await page.locator('#reg tr:not([hidden])').count() > 0, 'source filter has no result');
      assert.match(await page.locator('#reg tr:not([hidden])').allInnerTexts().then(rows => rows.join(' ')), /eia-api-v2/);
      if (viewport.width <= 600) {
        const fits = await page.locator('.registry-table').evaluate(table =>
          table.scrollWidth <= table.clientWidth + 1 && [...table.querySelectorAll('td')].every(cell =>
            cell.scrollWidth <= cell.clientWidth + 1));
        assert(fits, 'mobile source coverage needs horizontal table movement');
      }
    } else {
      await page.locator('main h1').waitFor({state: 'visible'});
      assert.match(await page.locator('main').innerText(), /recorded workflow/i);
    }
    await settled(page);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'shortcut page overflow');
    await page.screenshot({path: path.join(output, 'journey-' + viewport.width + '-' + task + '.png')});
    report.journeys.push({viewport, task, endpoint: page.url(), ...counters});
  } finally { await context.close(); }
}
async function journey(browser, viewport, task) {
  const counters = metrics();
  const {page, context} = await pageFor(browser, viewport, counters);
  try {
    await page.goto(base + '/index.html', {waitUntil: 'domcontentloaded'});
    const search = page.locator('#demo-query');
    await click(page, search, counters, 'find a walkthrough');
    await page.keyboard.type(task.query);
    counters.keyboardActions++;
    counters.actions.push({type: 'type', label: 'filter walkthroughs', text: task.query});
    await page.waitForFunction(id => !document.querySelector('a.demo-link[href="demos.html#' + id + '"]').hidden, task.id);
    await click(page, page.locator('a.demo-link[href="demos.html#' + task.id + '"]'), counters, task.id + ' walkthrough');
    await readyDemo(page, task.id);
    const panel = page.locator('article#' + task.id);
    if (task.id === 'microbiome') {
      await jump(page, '2. Samples', 'microbiome-step-2', counters);
      await click(page, panel.getByText('Record sources', {exact: true}).last(), counters, 'sample record sources');
      assert(await panel.locator('.step').last().locator('.evidence').isVisible(), 'sample evidence did not open');
      await jump(page, 'Overview', task.id, counters);
      const file = await download(page, panel.getByText('Download JSON evidence', {exact: true}), counters, 'JSON evidence');
      assert.equal(JSON.parse(file.bytes).case, task.id);
    } else if (task.id === 'bibliography') {
      await jump(page, 'Worksheet', task.id + '-worksheet', counters);
      assert(await panel.locator('.worksheet tbody tr').count() > 0, 'worksheet has no rows');
      const file = await download(page, panel.getByText('Download worksheet CSV', {exact: true}), counters, 'worksheet CSV');
      assert.equal(file.name, task.id + '-worksheet.csv');
      assert(file.bytes.equals(fs.readFileSync(path.join(docs, 'data/demos', file.name))), 'download differs from published CSV');
    } else {
      const temperatures = await download(page, panel.getByText('Download temperatures CSV', {exact: true}), counters, 'temperature CSV');
      const metadata = await download(page, panel.getByText('Download Croissant metadata', {exact: true}), counters, 'Croissant metadata');
      const resource = JSON.parse(metadata.bytes).distribution[0];
      assert.equal(resource.contentUrl, temperatures.name);
      assert.equal(resource.sha256, require('node:crypto').createHash('sha256').update(temperatures.bytes).digest('hex'));
    }
    await settled(page);
    await page.screenshot({path: path.join(output, 'journey-' + viewport.width + '-' + task.id + '.png')});
    report.journeys.push({viewport, task: task.id, endpoint: page.url(), ...counters});
  } finally { await context.close(); }
}
async function stability(browser, viewport) {
  const counters = metrics();
  const {page, context} = await pageFor(browser, viewport, counters);
  try {
    await page.goto(base + '/demos.html#microbiome', {waitUntil: 'domcontentloaded'});
    await readyDemo(page, 'microbiome');
    const ids = await page.locator('#workflow option').evaluateAll(options => options.map(option => option.value));
    const cdp = await context.newCDPSession(page);
    await cdp.send('Performance.enable');
    async function checkpoint(round) {
      await page.locator('#workflow').selectOption('microbiome');
      await readyDemo(page, 'microbiome');
      const state = await page.evaluate(() => ({domNodes: document.querySelectorAll('*').length, historyLength: history.length}));
      const values = (await cdp.send('Performance.getMetrics')).metrics;
      return {round, ...state, heapBytes: values.find(metric => metric.name === 'JSHeapUsedSize')?.value ?? null};
    }
    const checkpoints = [await checkpoint(0)];
    for (let round = 1; round <= rounds; round++) {
      for (const id of ids) {
        await page.locator('#workflow').selectOption(id);
        await readyDemo(page, id);
      }
      const state = await checkpoint(round);
      assert.equal(state.domNodes, checkpoints[0].domNodes, 'DOM grew after returning to the same panel');
      assert.equal(state.historyLength, checkpoints[0].historyLength, 'demo changes grew browser history');
      checkpoints.push(state);
      await page.screenshot({path: path.join(output, 'stability-' + viewport.width + '-' + round + '.png')});
    }
    report.stability.push({viewport, rounds, demosPerRound: ids.length,
      scriptedSelectChanges: rounds * ids.length + rounds + 1, checkpoints, ...counters});
  } finally { await context.close(); }
}
async function performance(browser) {
  for (const route of ['index.html', 'demos.html#microbiome']) {
    const counters = metrics();
    const {page, context} = await pageFor(browser, {width: 375, height: 812}, counters);
    try {
      const cdp = await context.newCDPSession(page);
      await cdp.send('Network.enable');
      await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
      await cdp.send('Emulation.setCPUThrottlingRate', {rate: 4});
      await page.goto(base + '/' + route, {waitUntil: 'domcontentloaded'});
      if (route.startsWith('demos')) await readyDemo(page, 'microbiome');
      else {
        await page.waitForFunction(() => !document.getElementById('demo-search').hidden
          && document.querySelectorAll('a.demo-link').length > 0);
        await settled(page);
      }
      const timing = await page.evaluate(() => {
        const navigation = performance.getEntriesByType('navigation')[0];
        return {usefulReadyMs: performance.now(), domContentLoadedMs: navigation.domContentLoadedEventEnd,
          loadMs: navigation.loadEventEnd || null, decodedDocumentBytes: navigation.decodedBodySize};
      });
      const interactionStarted = await page.evaluate(() => performance.now());
      if (route.startsWith('demos')) await jump(page, '2. Samples', 'microbiome-step-2', counters);
      else {
        await click(page, page.locator('#demo-query'), counters, 'ready search');
        await page.keyboard.type('microbiome');
        await page.waitForFunction(() => document.querySelectorAll('a.demo-link:not([hidden])').length === 1);
      }
      await settled(page);
      report.performance.push({route, cpuSlowdown: 4, cacheDisabled: true, ...timing,
        scriptedInteractionMs: await page.evaluate(started => performance.now() - started, interactionStarted),
        longTasks: counters.longTasks,
        usefulContract: route.startsWith('demos') ? 'one selected demo with an answer; Samples jump operates' : 'search visible; cards rendered; filter operates'});
      await page.screenshot({path: path.join(output, 'cpu4x-' + route.split('.')[0] + '.png')});
    } finally { await context.close(); }
  }
}
async function applicationJourney(browser, viewport, task) {
  const counters = metrics();
  const {page, context} = await pageFor(browser, viewport, counters);
  try {
    await page.goto(base + '/index.html', {waitUntil: 'domcontentloaded'});
    await click(page, page.locator('.atlas-actions a[href="applications.html"]'), counters, 'applications');
    await click(page, page.locator('nav[aria-label="On this page"] a[href="#' + task.anchor + '"]'), counters, task.id);
    const heading = page.locator('#' + task.anchor);
    await heading.waitFor({state: 'visible'});
    assert.match(await page.locator('main').innerText(), /recorded publisher responses or clearly labelled synthetic simulations/);
    const link = page.locator('a[href="data/apps/' + task.id + '/bundle.zip"]');
    const file = await download(page, link, counters, 'complete application bundle');
    assert(file.bytes.equals(fs.readFileSync(path.join(docs, 'data/apps', task.id, 'bundle.zip'))), 'application bundle download differs');
    if (task.id === 'gcam-comparison') {
      assert.match(await page.locator('main').innerText(), /Southeast Asia/);
      assert.match(await page.locator('main').innerText(), /No arithmetic or unit conversion is performed/);
      for (const filename of ['plan.json', 'receipts.json', 'receipts.csv', 'comparisons.json']) {
        const receiptFile = await download(page, page.locator('a[href="data/apps/' + task.id + '/' + filename + '"]'), counters, filename);
        assert(receiptFile.bytes.equals(fs.readFileSync(path.join(docs, 'data/apps', task.id, filename))), 'GCAM receipt download differs: ' + filename);
        if (filename.endsWith('.json')) JSON.parse(receiptFile.bytes.toString('utf8'));
      }
      const receipts = JSON.parse(fs.readFileSync(path.join(docs, 'data/apps', task.id, 'receipts.json'), 'utf8'));
      assert(receipts.every(receipt => receipt.file_url && receipt.evidence_ref && receipt.row_number && receipt.publisher_columns.length), 'GCAM receipts lack source identity');
      assert.equal(receipts.find(receipt => receipt.id === 'indonesia-geothermal').region_mapping.join_value, 'Southeast Asia');
    }
    if (task.id === 'forecast-panel') {
      assert.match(await page.locator('main').innerText(), /Synthetic simulation. Fictitious regions/);
      for (const filename of ['plan.json', 'actuals.csv', 'forecasts.csv', 'included-targets.csv', 'row-exclusions.csv', 'metrics.csv', 'cohorts.json']) {
        const imported = await download(page, page.locator('a[href="data/apps/forecast-panel/' + filename + '"]'), counters, filename);
        assert(imported.bytes.equals(fs.readFileSync(path.join(docs, 'data/apps/forecast-panel', filename))), 'forecast panel input/receipt differs: ' + filename);
      }
      const panel = JSON.parse(fs.readFileSync(path.join(docs, 'data/apps/forecast-panel/report.json'), 'utf8'));
      assert.equal(panel.mode, 'simulation');
      assert.equal(panel.fixtures.length, 0);
      assert.equal(panel.steps.length, 0);
      for (const lead of panel.plan.lead_seconds) {
        const cohorts = panel.cohorts.filter(row => row.lead_seconds === lead);
        assert.deepEqual(cohorts[0].included_targets, cohorts[1].included_targets);
      }
    }
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'application page overflow');
    await page.screenshot({path: path.join(output, 'application-' + viewport.width + '-' + task.id + '.png')});
    report.journeys.push({viewport, task: task.id, endpoint: page.url(), ...counters});
  } finally { await context.close(); }
}
async function evaluationJourney(browser, viewport) {
  const counters = metrics();
  const {page, context} = await pageFor(browser, viewport, counters);
  try {
    await page.goto(base + '/index.html', {waitUntil: 'domcontentloaded'});
    await click(page, page.locator('.atlas-actions a[href="evaluation-example.html"]'), counters, 'evaluation lab');
    await page.getByRole('heading', {name: 'Evaluation matrix demonstration', exact: true}).waitFor();
    assert.equal(await page.getByText('Unknown: no reviews', {exact: true}).count(), 4);
    assert.equal(await page.getByText('Unknown: no prices', {exact: true}).count(), 4);
    const file = await download(page, page.getByRole('link', {name: 'Download the complete comparison bundle', exact: true}), counters, 'evaluation bundle');
    assert(file.bytes.equals(fs.readFileSync(path.join(docs, 'data/evaluation-matrix/bundle.zip'))), 'evaluation bundle differs');
    await settled(page);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'evaluation page overflow');
    await page.screenshot({path: path.join(output, 'journey-' + viewport.width + '-evaluation-matrix.png')});
    report.journeys.push({viewport, task: 'evaluation-matrix', endpoint: page.url(), ...counters});
  } finally { await context.close(); }
}
async function hrefTargets(browser) {
  const {page, context} = await pageFor(browser, {width: 1280, height: 800}, metrics());
  const destinations = new Map();
  let checked = 0, external = 0;
  try {
    for (const filename of fs.readdirSync(docs).filter(name => name.endsWith('.html')).sort()) {
      await page.goto(base + '/' + filename, {waitUntil: 'domcontentloaded'});
      const links = await page.locator('a[href]').evaluateAll(anchors => anchors.map(anchor => anchor.href));
      for (const href of links) {
        const url = new URL(href);
        if (url.origin !== new URL(base).origin) { external++; continue; }
        checked++;
        const file = artifact(href);
        assert(fs.existsSync(file) && fs.statSync(file).isFile(), 'missing href target: ' + url.pathname);
        const fragment = decodeURIComponent(url.hash.slice(1));
        if (fragment && file.endsWith('.html')) {
          if (!destinations.has(file)) destinations.set(file, new Set());
          destinations.get(file).add(fragment);
        }
      }
    }
    for (const [file, fragments] of destinations) {
      const missing = await page.evaluate(({html, fragments}) => {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        return fragments.filter(id => !doc.getElementById(id) && ![...doc.querySelectorAll('a[name]')].some(anchor => anchor.name === id));
      }, {html: fs.readFileSync(file, 'utf8'), fragments: [...fragments]});
      assert.deepEqual(missing, [], 'missing fragments in ' + path.relative(docs, file));
    }
    report.hrefs = {localTargetsChecked: checked, fragmentDocumentsChecked: destinations.size, externalLinksNotProbed: external};
  } finally { await context.close(); }
}
(async () => {
  const launch = {headless: true};
  if (process.env.UAT_BROWSER_EXECUTABLE_PATH) launch.executablePath = process.env.UAT_BROWSER_EXECUTABLE_PATH;
  const browser = await chromium.launch(launch);
  report.browser = browser.version();
  report.playwright = require('playwright/package.json').version;
  try {
    for (const viewport of [{width: 375, height: 812}, {width: 768, height: 1024}, {width: 1280, height: 800}]) {
      for (const task of [{id: 'microbiome', query: 'microbiome'}, {id: 'bibliography', query: 'bibliography'}, {id: 'daymet-calendar', query: 'Daymet'}])
        await journey(browser, viewport, task);
      for (const task of ['install', 'coverage', 'guide']) await shortcutJourney(browser, viewport, task);
      for (const task of [
        {id: 'forecast-benchmark', anchor: 'grid-forecast-benchmark-lab'},
        {id: 'weather-package', anchor: 'weather-package-builder'},
        {id: 'docket-tracker', anchor: 'doe-docket-notice-tracker'},
        {id: 'gcam-comparison', anchor: 'gcam-comparison-receipts'},
        {id: 'research-linkage', anchor: 'research-artifact-linkage-explorer'},
        {id: 'forecast-panel', anchor: 'forecast-panel-with-common-cohorts'}])
        await applicationJourney(browser, viewport, task);
      await evaluationJourney(browser, viewport);
      await stability(browser, viewport);
    }
    await performance(browser);
    await hrefTargets(browser);
    assert.equal(report.errors.length, 0, 'browser or first-party request errors; see report');
  } catch (error) {
    report.failure = error.message;
    process.exitCode = 1;
  } finally {
    await browser.close();
    for (const url of requested) {
      const file = artifact(url);
      if (file && fs.existsSync(file) && fs.statSync(file).isFile()) {
        const bytes = fs.readFileSync(file);
        report.payloads.push({path: path.relative(docs, file), rawBytes: bytes.length, calculatedGzipBytes: zlib.gzipSync(bytes, {level: 9}).length});
      }
    }
    report.payloads = [...new Map(report.payloads.map(item => [item.path, item])).values()];
    report.blockedExternal = [...new Set(report.blockedExternal)];
    fs.writeFileSync(path.join(output, 'journeys.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
