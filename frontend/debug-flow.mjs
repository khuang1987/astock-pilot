import { chromium } from "playwright";

const browser = await chromium.launch({
  headless: true,
  executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});

const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const logs = [];
page.on("console", (msg) => logs.push({ type: "console", level: msg.type(), text: msg.text() }));
page.on("pageerror", (err) => logs.push({ type: "pageerror", text: err.stack || err.message }));

await page.goto("http://127.0.0.1:3008", { waitUntil: "networkidle", timeout: 30000 });
await page.getByText("中兴通讯").first().click();
await page.waitForTimeout(1000);
const detailText = (await page.locator("#root").innerText()).slice(0, 600);
await page.getByRole("button", { name: /策略/ }).click();
await page.waitForTimeout(1000);
const strategyText = (await page.locator("#root").innerText()).slice(0, 800);
await page.screenshot({ path: "debug-flow.png", fullPage: true });
console.log(JSON.stringify({ detailText, strategyText, logs }, null, 2));

await browser.close();
