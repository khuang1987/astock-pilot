import { chromium } from "playwright";

const browser = await chromium.launch({
  headless: true,
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});

const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const logs = [];

page.on("console", (msg) => logs.push({ type: "console", level: msg.type(), text: msg.text() }));
page.on("pageerror", (err) => logs.push({ type: "pageerror", text: err.stack || err.message }));
page.on("requestfailed", (req) => logs.push({ type: "requestfailed", url: req.url(), error: req.failure()?.errorText }));
page.on("response", (res) => {
  if (res.status() >= 400) logs.push({ type: "response", status: res.status(), url: res.url() });
});

await page.goto("http://127.0.0.1:3008", { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(3000);

const title = await page.title();
const rootText = await page.locator("#root").innerText().catch((err) => `ROOT_ERROR: ${err.message}`);
const rootHtmlLength = await page.locator("#root").evaluate((el) => el.innerHTML.length).catch(() => -1);
await page.screenshot({ path: "debug-page.png", fullPage: true });

console.log(JSON.stringify({ title, rootText: rootText.slice(0, 1000), rootHtmlLength, logs }, null, 2));

await browser.close();
