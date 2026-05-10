import { chromium } from "playwright";

const browser = await chromium.launch({
  headless: true,
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});

const page = await browser.newPage({
  viewport: { width: 390, height: 900 },
  isMobile: true,
});
const logs = [];
page.on("console", (msg) => logs.push({ type: "console", level: msg.type(), text: msg.text() }));
page.on("pageerror", (err) => logs.push({ type: "pageerror", text: err.stack || err.message }));
page.on("requestfailed", (req) => logs.push({ type: "requestfailed", url: req.url(), error: req.failure()?.errorText }));

await page.goto("http://127.0.0.1:3008", { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(1500);
const overflow = await page.evaluate(() => ({
  body: document.body.scrollWidth,
  doc: document.documentElement.scrollWidth,
  viewport: window.innerWidth,
  rootText: document.querySelector("#root")?.innerText.slice(0, 500) || "",
}));
await page.screenshot({ path: "debug-mobile.png", fullPage: true });
console.log(JSON.stringify({ overflow, logs }, null, 2));
await browser.close();
