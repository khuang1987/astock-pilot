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
await page.getByRole("button", { name: /模拟交易/ }).click();
await page.getByRole("button", { name: "执行今日自动模拟" }).click();
await page.waitForTimeout(1000);
await page.screenshot({ path: "debug-sim.png", fullPage: true });
console.log(JSON.stringify({ text: (await page.locator("#root").innerText()).slice(0, 1200), logs }, null, 2));

await browser.close();
