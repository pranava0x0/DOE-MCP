'use strict';
// Local automated evidence, not a declaration of complete WCAG conformance.
const {chromium} = require('playwright');
const axePath = require.resolve('axe-core/axe.min.js');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const docs = path.join(root, 'docs');
const base = (process.env.UAT_URL || 'http://127.0.0.1:4192').replace(/\/$/, '');
const origin = new URL(base);
assert(['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname), 'accessibility UAT is loopback-only');
const output = path.resolve(process.env.UAT_OUTPUT_DIR || path.join(root, 'uat-screenshots/accessibility'));
fs.mkdirSync(path.join(output, 'axe'), {recursive: true});
const tags = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];
const report = {kind: 'automated-accessibility-evidence', at: new Date().toISOString(), base,
  axeVersion: require('axe-core/package.json').version,
  playwrightVersion: require('playwright/package.json').version,
  standardTags: tags, disabledRules: [], pages: [], demoStates: [], checks: [], failures: [],
  errors: [], blockedExternal: [], limitations: [
    'Automated axe checks and Chromium accessibility-tree assertions do not establish full WCAG conformance.',
    'No actual VoiceOver, NVDA, screen-reader speech, physical-device or human usability test is performed.',
    '640 and 320 CSS-pixel widths are reflow equivalents of 1280 pixels at 200% and 400% zoom, not native browser magnification.',
    'Axe incomplete results are retained for manual review; they are not treated as passes or silently suppressed.',
    'Only local generated pages and their active demo states are tested; external resources are blocked.'
  ]};
const safe = value => value.replace(/[^a-zA-Z0-9_-]/g, '-');
const frame = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function check(page, name, action) {
  try {
    const detail = await action();
    report.checks.push({name, url: page.url(), passed: true, detail});
    return detail;
  } catch (error) {
    report.checks.push({name, url: page.url(), passed: false});
    report.failures.push({kind: 'assertion', name, url: page.url(), message: error.message, stack: error.stack});
    await page.screenshot({path: path.join(output, safe(name) + '-failure.png')}).catch(() => {});
    console.error(`FAIL ${name}: ${error.message}`);
    return null;
  }
}
async function contextFor(browser, viewport, javaScriptEnabled = true) {
  const context = await browser.newContext({viewport, javaScriptEnabled, reducedMotion: 'reduce', serviceWorkers: 'block'});
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin !== origin.origin) {
      report.blockedExternal.push(route.request().url());
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => report.errors.push({url: page.url(), message: error.message}));
  page.on('response', response => {
    if (response.status() >= 400) report.errors.push({url: response.url(), status: response.status()});
  });
  return {context, page};
}
async function scan(page, id) {
  if (!await page.evaluate(() => Boolean(window.axe))) await page.addScriptTag({path: axePath});
  const result = await page.evaluate(async runTags => axe.run(document, {
    runOnly: {type: 'tag', values: runTags}, resultTypes: ['violations', 'incomplete']
  }), tags);
  fs.writeFileSync(path.join(output, 'axe', id + '.json'), JSON.stringify(result, null, 2));
  const summary = {id, url: page.url(), viewport: page.viewportSize(), rulesPassed: result.passes.length,
    violations: result.violations.map(rule => ({id: rule.id, impact: rule.impact, help: rule.help,
      helpUrl: rule.helpUrl, nodes: rule.nodes.map(node => ({target: node.target, html: node.html,
        failureSummary: node.failureSummary}))})),
    incomplete: result.incomplete.map(rule => ({id: rule.id, impact: rule.impact,
      help: rule.help, nodeCount: rule.nodes.length,
      nodes: rule.nodes.map(node => ({target: node.target, html: node.html, failureSummary: node.failureSummary}))})), result: 'axe/' + id + '.json'};
  if (summary.violations.length) {
    report.failures.push({kind: 'axe', id, url: page.url(), violations: summary.violations});
    await page.screenshot({path: path.join(output, id + '-axe-failure.png')});
    console.error(`FAIL axe ${id}: ${summary.violations.map(rule => rule.id).join(', ')}`);
  }
  if (summary.incomplete.length) console.warn(`MANUAL REVIEW ${id}: ${summary.incomplete.map(rule => rule.id).join(', ')}`);
  return summary;
}
async function reflow(page, id) {
  return check(page, id + '-reflow', async () => {
    const result = await page.evaluate(() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      textOutsideViewport: [...document.querySelectorAll('h1,h2,h3,p,summary,label')].filter(node => {
        if (!node.checkVisibility({checkVisibilityCSS: true})) return false;
        // Data tables may require two-dimensional layout; their surrounding prose may not.
        if (node.closest('table,pre')) return false;
        const rect = node.getBoundingClientRect();
        return rect.width && (rect.left < -1 || rect.right > innerWidth + 1);
      }).slice(0, 15).map(node => ({tag: node.tagName, id: node.id, text: node.textContent.trim().slice(0, 80)}))}));
    assert(result.scrollWidth <= result.width + 1, `page scrollWidth ${result.scrollWidth} > ${result.width}`);
    assert.equal(result.textOutsideViewport.length, 0, JSON.stringify(result.textOutsideViewport));
    return result;
  });
}
async function axNode(page, selector) {
  const client = await page.context().newCDPSession(page);
  try {
    await client.send('DOM.enable');
    await client.send('Accessibility.enable');
    const {root: document} = await client.send('DOM.getDocument');
    const {nodeId} = await client.send('DOM.querySelector', {nodeId: document.nodeId, selector});
    assert(nodeId, 'missing AX target ' + selector);
    const {nodes} = await client.send('Accessibility.getPartialAXTree', {nodeId, fetchRelatives: false});
    return nodes[0];
  } finally { await client.detach(); }
}
async function landmarks(page, id, width) {
  return check(page, id + '-accessibility-tree', async () => {
    const client = await page.context().newCDPSession(page);
    const {nodes} = await client.send('Accessibility.getFullAXTree');
    await client.detach();
    const available = nodes.filter(node => !node.ignored);
    assert.equal(available.filter(node => node.role?.value === 'main').length, 1, 'one exposed main landmark');
    assert(available.some(node => node.role?.value === 'heading' && node.properties?.some(p => p.name === 'level' && p.value.value === 1)), 'exposed level-one heading');
    const navigation = await axNode(page, '[data-location-nav] nav');
    if (width >= 1200) {
      assert(!navigation.ignored && navigation.role?.value === 'navigation' && navigation.name?.value === 'On this page', 'desktop page navigation is named and exposed');
      return {main: 1, navigationRole: navigation.role.value, navigationName: navigation.name.value};
    }
    const summary = await axNode(page, '[data-location-nav] summary');
    assert(!summary.ignored && summary.name?.value, 'location disclosure has an accessible name');
    assert(['DisclosureTriangle', 'button'].includes(summary.role?.value), 'native disclosure/button role');
    const expanded = summary.properties?.find(property => property.name === 'expanded')?.value.value;
    assert.equal(expanded, await page.locator('[data-location-nav] details').evaluate(node => node.open), 'AX expanded state matches details');
    return {main: 1, disclosureRole: summary.role.value, disclosureName: summary.name.value, expanded};
  });
}
async function skipLink(page, id, width) {
  return check(page, id + '-keyboard-skip', async () => {
    await page.keyboard.press('Tab');
    const skip = await page.locator(':focus').evaluate(node => ({text: node.textContent, href: node.getAttribute('href')}));
    assert(/skip/i.test(skip.text) && skip.href?.startsWith('#'), 'first Tab reaches a skip link');
    const focus = await page.locator(':focus').evaluate(node => {
      const style = getComputedStyle(node);
      return {outlineStyle: style.outlineStyle, outlineWidth: parseFloat(style.outlineWidth), boxShadow: style.boxShadow};
    });
    assert(focus.outlineStyle !== 'none' && focus.outlineWidth > 0 || focus.boxShadow !== 'none', 'keyboard skip-link focus indicator is visible');
    let tabSteps = 0;
    while (tabSteps < 100 && !await page.locator(':focus').evaluate(node => Boolean(node.closest('[data-location-nav]')))) {
      await page.keyboard.press('Tab');
      tabSteps++;
    }
    assert(tabSteps < 100, 'section navigation is reachable through the normal Tab sequence');
    if (width < 1200) {
      assert.equal(await page.locator(':focus').evaluate(node => node.tagName), 'SUMMARY', 'closed mobile disclosure is keyboard reachable');
      await page.keyboard.press('Enter');
      await page.keyboard.press('Tab');
      assert(await page.locator(':focus').evaluate(node => Boolean(node.closest('[data-location-links]'))), 'Tab reaches section anchors after disclosure opens');
      await page.keyboard.press('Escape');
    }
    await page.locator('a[href=' + JSON.stringify(skip.href) + ']').first().focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator(':focus').getAttribute('id'), skip.href.slice(1), 'skip link moves focus to its target');
    return {...focus, tabStepsToNavigation: tabSteps};
  });
}
async function currentState(page) {
  return page.evaluate(() => {
    const root = document.querySelector('[data-location-nav]');
    const links = [...root.querySelectorAll('[data-location-links] a')];
    const current = links.filter(link => link.getAttribute('aria-current') === 'location');
    const label = root.querySelector('[data-current-location]');
    return {links: links.map(link => ({text: link.textContent.trim(), href: link.getAttribute('href')})),
      current: current.map(link => link.getAttribute('href')), label: label?.textContent.trim(),
      live: label?.closest('[aria-live]:not([aria-live="off"]),[role="status"],[role="alert"],[role="log"]') !== null};
  });
}
async function selectSection(page, index) {
  const menu = page.locator('[data-location-nav] details');
  if (!await menu.evaluate(node => node.open)) {
    await page.locator('[data-location-nav] summary').focus();
    await page.keyboard.press('Enter');
  }
  const link = page.locator('[data-location-links] a').nth(index);
  const href = await link.getAttribute('href');
  await link.focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(hash => location.hash === hash, href);
  await frame(page);
  const target = page.locator('[id=' + JSON.stringify(decodeURIComponent(href.slice(1))) + ']');
  assert(await target.isVisible(), 'selected section is visible');
  assert.equal(await page.locator(':focus').getAttribute('id'), decodeURIComponent(href.slice(1)), 'section activation focuses target');
  const ancestorsOpen = await target.evaluate(node => {
    for (let ancestor = node.parentElement; ancestor; ancestor = ancestor.parentElement) {
      if (ancestor.tagName === 'DETAILS' && !ancestor.open) return false;
    }
    return true;
  });
  assert(ancestorsOpen, 'all details ancestors were revealed');
  const position = await target.evaluate(node => {
    const rect = node.getBoundingClientRect();
    const point = document.elementFromPoint(Math.min(innerWidth - 1, Math.max(0, rect.left + 4)), Math.max(0, rect.top + 4));
    return {top: rect.top, bottom: rect.bottom, viewport: innerHeight, exposed: point === node || node.contains(point)};
  });
  assert(position.top >= -1 && position.top < position.viewport && position.exposed, 'focused target is not covered by sticky controls');
  return href;
}
async function navigation(page, id, width) {
  return check(page, id + '-location-navigation', async () => {
    const state = await currentState(page);
    assert(state.links.length > 0 && state.current.length === 1, 'exactly one current location');
    assert(state.label && !state.live, 'current-place text exists without automatic live-region speech');
    const first = await selectSection(page, 0);
    const last = await selectSection(page, state.links.length - 1);
    await page.waitForFunction(hash => document.querySelector(`[data-location-links] a[href="${hash}"]`)?.getAttribute('aria-current') === 'location', last);
    if (first !== last) {
      await page.goBack();
      await page.waitForFunction(hash => location.hash === hash, first);
      await page.waitForFunction(hash => document.querySelector(`[data-location-links] a[href="${hash}"]`)?.getAttribute('aria-current') === 'location', first);
      await page.goForward();
      await page.waitForFunction(hash => location.hash === hash, last);
    }
    if (width < 1200) {
      const summary = page.locator('[data-location-nav] summary');
      await summary.focus();
      await page.keyboard.press('Enter');
      assert(await page.locator('[data-location-nav] details').evaluate(node => node.open), 'mobile menu opens with Enter');
      await page.keyboard.press('Escape');
      assert(!await page.locator('[data-location-nav] details').evaluate(node => node.open), 'Escape closes mobile menu');
      assert(await summary.evaluate(node => node === document.activeElement), 'Escape returns focus to summary');
    } else {
      const sidebar = await page.locator('[data-location-nav]').boundingBox();
      assert(sidebar && sidebar.y >= -1 && sidebar.y + sidebar.height <= 901, 'desktop section sidebar remains in viewport after section navigation');
    }
    // Passive scroll must never steal focus, mutate URL, or announce a live region.
    const before = await page.evaluate(() => { window.__auditFocusedElement = document.activeElement; return location.hash; });
    await page.locator('[id=' + JSON.stringify(decodeURIComponent(first.slice(1))) + ']').evaluate(node => node.scrollIntoView({block: 'start'}));
    await frame(page);
    const after = await page.evaluate(() => ({hash: location.hash, sameFocus: document.activeElement === window.__auditFocusedElement}));
    assert.equal(after.hash, before, 'passive scroll preserves URL');
    assert(after.sameFocus, 'passive scroll preserves the exact focused element');
    await page.waitForFunction(hash => document.querySelector(`[data-location-links] a[href="${hash}"]`)?.getAttribute('aria-current') === 'location', first);
    assert.deepEqual((await currentState(page)).current, [first], 'passive scroll updates exactly one current marker');
    await page.evaluate(() => { delete window.__auditFocusedElement; });
    return {sectionLinks: state.links.length, history: first !== last, passiveScroll: true, nativeDisclosure: true};
  });
}
async function resizeFocus(page, id) {
  return check(page, id + '-responsive-focus', async () => {
    await page.bringToFront();
    await page.setViewportSize({width: 320, height: 900});
    await frame(page);
    await page.locator('[data-location-nav] summary').focus();
    await page.setViewportSize({width: 1280, height: 900});
    await frame(page);
    await page.waitForFunction(() => document.activeElement.checkVisibility({checkVisibilityCSS: true}) && Boolean(document.activeElement.closest('[data-location-links]')));
    assert(await page.evaluate(() => document.activeElement.checkVisibility({checkVisibilityCSS: true})), 'focused mobile disclosure transfers focus to visible desktop navigation');
    await page.setViewportSize({width: 320, height: 900});
    await frame(page);
    await page.waitForFunction(() => document.activeElement.checkVisibility({checkVisibilityCSS: true}) && Boolean(document.activeElement.closest('[data-location-nav]')));
    assert(await page.evaluate(() => document.activeElement.checkVisibility({checkVisibilityCSS: true})), 'desktop navigation focus remains on a visible control after mobile resize');
    await page.setViewportSize({width: 1280, height: 900});
    await frame(page);
    return {widths: [320, 1280, 320, 1280], focusedControlVisible: true};
  });
}
async function modifiedLink(page, id) {
  return check(page, id + '-modified-section-link', async () => {
    const link = page.locator('[data-location-links] a').first();
    const hash = await link.getAttribute('href');
    const original = page.url();
    const destination = new URL(hash, original).href;
    const popupPromise = page.context().waitForEvent('page');
    await link.click({modifiers: [process.platform === 'darwin' ? 'Meta' : 'Control']});
    const popup = await popupPromise;
    try {
      // A new tab can initially report a loaded about:blank document. Wait for the
      // real anchor destination before checking readiness or fragment semantics.
      await popup.waitForURL(destination, {waitUntil: 'domcontentloaded'});
      await popup.waitForLoadState('load');
      assert.equal(popup.url(), destination, 'modified anchor opens its exact native destination in another tab');
      assert.equal(new URL(popup.url()).hash, hash, 'modified anchor opens its native fragment in another tab');
      assert.equal(page.url(), original, 'modified anchor preserves original page location');
      return {hash, destination, nativeNewTab: true};
    } finally { await popup.close(); await page.bringToFront(); }
  });
}
async function collapsedSection(page, id) {
  return check(page, id + '-collapsed-section', async () => {
    const index = await page.locator('[data-location-links] a').evaluateAll(links => links.findIndex(link => {
      const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
      return target?.parentElement.closest('details:not(.location-menu)');
    }));
    assert(index >= 0, 'homepage exposes a subsection inside native details');
    const link = page.locator('[data-location-links] a').nth(index);
    const href = await link.getAttribute('href');
    const target = page.locator('[id=' + JSON.stringify(decodeURIComponent(href.slice(1))) + ']');
    await target.evaluate(node => { node.parentElement.closest('details').open = false; });
    await selectSection(page, index);
    const summary = target.locator('xpath=ancestor::details[1]').locator(':scope > summary');
    await summary.focus();
    const hash = new URL(page.url()).hash;
    await page.keyboard.press('Space');
    await frame(page);
    assert(await summary.evaluate(node => !node.parentElement.open && node === document.activeElement), 'collapsing current section keeps focus on its visible summary');
    assert.equal(new URL(page.url()).hash, hash, 'collapse does not change history');
    const state = await currentState(page);
    assert.equal(state.current.length, 1);
    const currentId = decodeURIComponent(state.current[0].slice(1));
    assert(await page.locator('[id=' + JSON.stringify(currentId) + ']').isVisible(), 'current location selects a visible section after collapse');
    assert.notEqual(state.current[0], href, 'hidden subsection is no longer current');
    return {subsection: href, current: state.current[0]};
  });
}
async function deepDemoLinks(page, width) {
  await page.goto(base + '/demos.html');
  const targets = await page.locator('article').evaluateAll(articles => [
    articles.map(article => article.querySelector('[id$="-step-1"]')?.id).find(Boolean),
    [...articles].reverse().map(article => article.querySelector('[id$="-worksheet"]')?.id).find(Boolean)
  ].filter(Boolean));
  assert.equal(targets.length, 2, 'demo fixtures provide a step and worksheet deep link');
  for (const targetId of targets) {
    await check(page, 'demo-deep-link-' + targetId + '-' + width, async () => {
      await page.goto(base + '/demos.html#' + targetId);
      await frame(page);
      const target = page.locator('[id=' + JSON.stringify(targetId) + ']');
      const owner = await target.evaluate(node => node.closest('article').id);
      assert(await target.isVisible(), 'initial deep link reveals nested result');
      assert.equal(await page.locator('#workflow').inputValue(), owner, 'initial deep link synchronizes picker');
      assert.equal(await page.locator('article:visible').getAttribute('id'), owner);
      const state = await currentState(page);
      assert.deepEqual(state.current, ['#' + targetId], 'deep link updates current location');
      return {targetId, owner};
    });
  }
  await check(page, 'demo-picker-history-' + width, async () => {
    const options = await page.locator('#workflow option').evaluateAll(nodes => nodes.map(node => node.value));
    await page.locator('#workflow').selectOption(options[0]);
    await page.locator('#workflow').selectOption(options[1]);
    await page.goBack();
    await page.waitForFunction(value => document.querySelector('#workflow').value === value, options[0]);
    assert.equal(await page.locator('article:visible').getAttribute('id'), options[0]);
    await page.goForward();
    await page.waitForFunction(value => document.querySelector('#workflow').value === value, options[1]);
    assert.equal(await page.locator('article:visible').getAttribute('id'), options[1]);
    return {back: options[0], forward: options[1]};
  });
}
async function demoSemantics(page, id) {
  return check(page, id + '-active-panel-tree', async () => {
    assert.equal(await page.locator('article:visible').count(), 1, 'one visible demo article');
    const selected = await page.locator('#workflow').inputValue();
    const active = await axNode(page, 'article#' + selected);
    assert(!active.ignored && active.role?.value === 'article', 'active article exposed to accessibility tree');
    const client = await page.context().newCDPSession(page);
    let exposedArticles;
    try {
      const {nodes} = await client.send('Accessibility.getFullAXTree');
      exposedArticles = nodes.filter(node => !node.ignored && node.role?.value === 'article').length;
    } finally { await client.detach(); }
    assert.equal(exposedArticles, 1, 'all inactive articles are absent from the accessibility tree');
    const hiddenId = await page.locator('article:not(:visible)').first().getAttribute('id');
    const hidden = await axNode(page, 'article#' + hiddenId);
    assert(hidden.ignored, 'inactive article omitted from accessibility tree');
    const hiddenCannotFocus = await page.evaluate(() => {
      const before = document.activeElement;
      for (const article of document.querySelectorAll('article')) {
        if (!article.checkVisibility({checkVisibilityCSS: true})) article.querySelector('a,button,summary,select,input')?.focus();
      }
      return document.activeElement === before;
    });
    assert(hiddenCannotFocus, 'inactive panel controls cannot receive focus');
    return {selected, activeRole: active.role.value, hiddenIgnored: hidden.ignored, exposedArticles};
  });
}
async function noJavaScript(browser, files) {
  const {context, page} = await contextFor(browser, {width: 320, height: 800}, false);
  for (const file of files) {
    await page.goto(base + '/' + file);
    await check(page, 'no-js-' + safe(file), async () => {
      const menu = page.locator('[data-location-nav] details');
      assert(await menu.evaluate(node => node.open), 'native section disclosure is open without JavaScript');
      const links = page.locator('[data-location-links] a');
      assert(await links.count() > 0, 'native section anchors available');
      const destinations = [];
      for (const index of [0, await links.count() - 1]) {
        const link = links.nth(index);
        const href = await link.getAttribute('href');
        assert(href.startsWith('#'), 'section uses a real fragment anchor');
        destinations.push(href);
        await link.click();
        assert.equal(new URL(page.url()).hash, href);
        assert(await page.locator('[id=' + JSON.stringify(decodeURIComponent(href.slice(1))) + ']').isVisible(), 'native anchor reveals target');
      }
      if (destinations[0] !== destinations[1]) {
        await page.goBack();
        assert.equal(new URL(page.url()).hash, destinations[0], 'native Back restores first section without JavaScript');
        assert(await page.locator('[id=' + JSON.stringify(decodeURIComponent(destinations[0].slice(1))) + ']').isVisible());
        await page.goForward();
        assert.equal(new URL(page.url()).hash, destinations[1], 'native Forward restores last section without JavaScript');
        assert(await page.locator('[id=' + JSON.stringify(decodeURIComponent(destinations[1].slice(1))) + ']').isVisible());
      }
      const summary = page.locator('[data-location-nav] summary');
      await summary.focus();
      await page.keyboard.press('Space');
      assert(!await menu.evaluate(node => node.open), 'native disclosure closes with keyboard without script');
      await page.keyboard.press('Space');
      assert(await menu.evaluate(node => node.open), 'native disclosure reopens with keyboard without script');
      return {sectionLinks: await links.count(), scriptEnabled: false, nativeHashHistory: destinations[0] !== destinations[1]};
    });
  }
  await context.close();
}
(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.UAT_BROWSER_EXECUTABLE_PATH || undefined});
  report.browser = browser.version();
  const files = fs.readdirSync(docs).filter(file => file.endsWith('.html')).sort();
  report.generatedPages = files;
  report.planned = {pageViewportStates: files.length * 2, reflowWidths: [1280, 640, 320], noJavaScriptPages: files.length};
  try {
    for (const file of files) {
      assert(/<aside[^>]*\bdata-location-nav/.test(fs.readFileSync(path.join(docs, file), 'utf8')), file + ': missing generated location navigation; rebuild the entire site before UAT');
    }
    for (const width of [1280, 320]) {
      const {context, page} = await contextFor(browser, {width, height: 900});
      for (const file of files) {
        await page.goto(base + '/' + file);
        await frame(page);
        const id = safe(file) + '-' + width;
        report.pages.push(await scan(page, id));
        await reflow(page, id);
        await landmarks(page, id, width);
        await skipLink(page, id, width);
        await navigation(page, id, width);
        if (file === 'index.html') {
          await collapsedSection(page, id);
          if (width >= 1200) {
            await modifiedLink(page, id);
            await resizeFocus(page, id);
          }
        }
      }
      await page.goto(base + '/demos.html');
      const cases = await page.locator('#workflow option').evaluateAll(options => options.map(option => option.value));
      assert.equal(new Set(cases).size, cases.length, 'demo options have unique values');
      if (report.demoCases) assert.deepEqual(cases, report.demoCases, 'demo inventory stays stable across viewports');
      report.demoCases = cases;
      report.planned.activeDemoStates = cases.length * 2;
      for (const value of cases) {
        await page.locator('#workflow').selectOption(value);
        await frame(page);
        const id = 'demo-' + safe(value) + '-' + width;
        await check(page, id + '-section-navigation', async () => {
          const state = await currentState(page);
          assert.equal(state.current.length, 1, 'one current demo location');
          assert(state.label && !state.live, 'demo location has no automatic live speech');
          for (const link of state.links) {
            const owner = await page.locator('[id=' + JSON.stringify(decodeURIComponent(link.href.slice(1))) + ']').evaluate(node => node.closest('article')?.id);
            assert.equal(owner, value, 'sidebar target belongs to selected demo');
          }
          const target = await selectSection(page, state.links.length - 1);
          assert.equal(await page.locator('#workflow').inputValue(), value);
          await page.waitForFunction(hash => document.querySelector(`[data-location-links] a[href="${hash}"]`)?.getAttribute('aria-current') === 'location', target);
          assert.equal(await page.locator('[data-location-nav] details').evaluate(node => node.open), width >= 1200, 'section choice keeps desktop sidebar open and closes mobile disclosure');
          return {sectionLinks: state.links.length, selected: value};
        });
        // Audit the entire active article at rest; sticky controls at a deep scroll position
        // can obscure axe's contrast sampling. Obstruction was asserted at the target above.
        await page.evaluate(() => scrollTo(0, 0));
        await frame(page);
        report.demoStates.push(await scan(page, id));
        await reflow(page, id);
        await demoSemantics(page, id);
      }
      await deepDemoLinks(page, width);
      await context.close();
    }
    // 640 CSS px is the 200%-of-1280 reflow equivalent; all pages also ran at 320 (400%).
    const {context, page} = await contextFor(browser, {width: 640, height: 900});
    for (const file of files) {
      await page.goto(base + '/' + file);
      await frame(page);
      await reflow(page, safe(file) + '-640');
    }
    await context.close();
    await noJavaScript(browser, files);
    report.executionComplete = true;
  } catch (error) {
    report.failure = error.stack;
    console.error(error.stack);
  } finally {
    report.manualReviewItems = [...report.pages, ...report.demoStates].flatMap(state =>
      state.incomplete.map(rule => ({state: state.id, ...rule})));
    report.summary = {executionComplete: Boolean(report.executionComplete), pages: files.length,
      pageViewportStates: report.pages.length, axeScans: report.pages.length + report.demoStates.length,
      activeDemoStates: report.demoStates.length, assertions: report.checks.length,
      failures: report.failures.length, browserErrors: report.errors.length,
      manualReviewItems: report.manualReviewItems.length,
      manualReviewNodes: report.manualReviewItems.reduce((total, item) => total + item.nodeCount, 0),
      blockedExternal: report.blockedExternal.length};
    fs.writeFileSync(path.join(output, 'accessibility.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report.summary));
    await browser.close();
  }
  if (report.failure || report.failures.length || report.errors.length || report.blockedExternal.length) process.exitCode = 1;
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
