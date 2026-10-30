// Records the Data Maker clip: each page for a few seconds with a caption bar, in a 1600x900 window.
//   node datamaker.js login <url>        opens a visible window; sign in, then close it (the profile keeps the session)
//   node datamaker.js record urls.json   urls.json: [{"url": ..., "caption": ..., "seconds": 5, "scroll": 0}, ...]
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const PROFILE = path.join(__dirname, 'profile');
const OUT = path.join(__dirname, 'video');
const EXE = process.env.HOME + '/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
const [, , mode, arg] = process.argv;
const CAPTION = (text) => `(() => { let b = document.getElementById('eir-cap'); if (!b) { b = document.createElement('div'); b.id = 'eir-cap';
  Object.assign(b.style, {position:'fixed', left:0, right:0, top:0, height:'48px', lineHeight:'48px', padding:'0 28px', background:'#112034',
  color:'#fff', font:'26px Helvetica, Arial, sans-serif', zIndex:2147483647, pointerEvents:'none', whiteSpace:'nowrap'}); document.body.appendChild(b);
  document.body.style.paddingTop = '48px'; } b.textContent = ${JSON.stringify(text)}; })()`;
(async () => {
  if (mode === 'login') {
    const ctx = await chromium.launchPersistentContext(PROFILE, { headless: false, executablePath: EXE, viewport: { width: 1600, height: 900 } });
    const page = ctx.pages()[0] || await ctx.newPage();
    await page.goto(arg);
    console.log('Sign in in the window, open the Data Maker project, then close the window.');
    await new Promise((r) => ctx.on('close', r));
    return;
  }
  const pages = JSON.parse(fs.readFileSync(arg, 'utf8'));
  fs.mkdirSync(OUT, { recursive: true });
  const ctx = await chromium.launchPersistentContext(PROFILE, { headless: false, executablePath: EXE, viewport: { width: 1600, height: 900 },
    recordVideo: { dir: OUT, size: { width: 1600, height: 900 } } });
  const page = ctx.pages()[0] || await ctx.newPage();
  const marks = [];
  const t0 = Date.now();
  for (const p of pages) {
    await page.goto(p.url, { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
    await page.waitForTimeout(p.settle || 3000);
    await page.evaluate(CAPTION(p.caption)).catch(() => {});
    marks.push({ caption: p.caption, at: (Date.now() - t0) / 1000 });
    if (p.scroll) { await page.mouse.move(800, 500); await page.mouse.wheel(0, p.scroll); }
    await page.waitForTimeout((p.seconds || 5) * 1000);
    marks[marks.length - 1].end = (Date.now() - t0) / 1000;
    await page.screenshot({ path: path.join(OUT, `page${marks.length}.png`) });
  }
  const video = page.video();
  await ctx.close();
  fs.writeFileSync(path.join(OUT, 'marks.json'), JSON.stringify({ video: await video.path(), marks }, null, 1));
  console.log(JSON.stringify(marks));
})();
