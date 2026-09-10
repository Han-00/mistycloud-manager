// 新组件视觉验收：事件时间线 + 可用率卡，深浅两模式。
// 用 file:// 打开 webui，无 Python 桥 → 走 app.js 的演示数据分支（正好有
// DEMO_EVENTS 和 link_uptime 样例，不用伪造任何输入）。
const { chromium } = require("playwright");
const path = require("path");

(async () => {
  // 本机 playwright 缓存版本与包的期望版本常常对不上（要求 1223、装的是
  // 1208/1228）。不 chase 版本号：直接挑缓存里**最新**的 chromium 指过去。
  const fs = require("fs");
  const pwDir = path.join(process.env.LOCALAPPDATA, "ms-playwright");
  const exe = fs.readdirSync(pwDir)
    .filter((d) => /^chromium-\d+$/.test(d))
    .sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]))
    .map((d) => path.join(pwDir, d, "chrome-win64", "chrome.exe"))
    .find((p) => fs.existsSync(p));
  if (!exe) throw new Error("ms-playwright 缓存里没有 chromium，先装一个");
  const browser = await chromium.launch({ executablePath: exe });
  const page = await browser.newPage({ viewport: { width: 1280, height: 830 } });
  const url = "file:///" + path.resolve(__dirname, "..", "webui", "index.html").replace(/\\/g, "/");
  await page.goto(url);
  await page.waitForTimeout(600);            // 等演示数据渲染

  const out = (n) => path.join(__dirname, n);

  // [1] 仪表盘：可用率卡（深色）
  await page.click('.nav-item[data-page="dash"]');
  await page.waitForTimeout(200);
  await page.screenshot({ path: out("v_dash_dark.png") });

  // [2] 日志页：事件 tab（深色）
  await page.click('.nav-item[data-page="logs"]');
  await page.click('.log-tab[data-tab="events"]');
  await page.waitForTimeout(300);
  await page.screenshot({ path: out("v_events_dark.png") });

  // [3] 浅色模式同两屏（模式按钮在设置页，先切过去，再回日志页）
  await page.click('.nav-item[data-page="settings"]');
  await page.click('.mode-btn[data-mode="light"]');
  await page.waitForTimeout(300);
  await page.click('.nav-item[data-page="logs"]');
  await page.click('.log-tab[data-tab="events"]');
  await page.waitForTimeout(300);
  await page.screenshot({ path: out("v_events_light.png") });
  await page.click('.nav-item[data-page="dash"]');
  await page.waitForTimeout(200);
  await page.screenshot({ path: out("v_dash_light.png") });

  // 顺带收集控制台错误与基础断言
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  const info = await page.evaluate(() => ({
    uptimePct: document.getElementById("uptimePct")?.textContent,
    uptimeNote: document.getElementById("uptimeNote")?.textContent,
    barW: document.getElementById("uptimeBar")?.style.width,
    evRows: document.querySelectorAll("#eventPage .ev").length,
    evEmpty: document.getElementById("eventPage")?.innerHTML.slice(0, 60),
  }));
  console.log(JSON.stringify({ ...info, pageErrors: errs }, null, 1));
  await browser.close();
})().catch((e) => { console.error("FAIL:", e); process.exit(1); });
