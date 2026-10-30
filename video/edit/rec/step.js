// node step.js '<js actions>' shot.png : run Playwright actions on the live window's page, then screenshot + text.
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.connectOverCDP('http://127.0.0.1:9333');
  const page = b.contexts()[0].pages().slice(-1)[0];
  const fn = new Function('page', `return (async () => { ${process.argv[2]} })()`);
  try { await fn(page); } catch (e) { console.log('ERR', e.message.split('\n')[0]); }
  await page.waitForTimeout(1500);
  console.log('URL', page.url());
  await page.screenshot({ path: process.argv[3] || 'step.png' });
  console.log((await page.evaluate(() => document.body.innerText).catch(() => '')).slice(0, 2500));
})().then(() => process.exit(0));
