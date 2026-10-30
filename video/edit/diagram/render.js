const { chromium } = require('playwright');
const EXE = process.env.HOME + '/Library/Caches/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-mac-arm64/chrome-headless-shell';
(async () => { const b = await chromium.launch({ executablePath: EXE }); const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  await p.goto('file://' + __dirname + '/' + (process.argv[2] || 'architecture.html')); await p.waitForTimeout(300);
  await p.screenshot({ path: process.argv[3] || 'architecture.png' }); await b.close(); })();
