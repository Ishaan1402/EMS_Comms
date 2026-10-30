// Keeps one visible recording window open with a debugging port, so later scripts can drive it (connectOverCDP).
const { chromium } = require('playwright');
const path = require('path');
const EXE = process.env.HOME + '/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(__dirname, 'profile'), {
    headless: false, executablePath: EXE, viewport: { width: 1600, height: 900 }, args: ['--remote-debugging-port=9333'] });
  const page = ctx.pages()[0] || await ctx.newPage();
  if (process.argv[2]) await page.goto(process.argv[2]).catch(() => {});
  console.log('READY');
  await new Promise((r) => ctx.on('close', r));
})();
