import { launchChrome } from "../../dealroom/test/chrome-launch.mjs";

const disconnected = new Promise(resolve => {
  process.stdin.resume();
  process.stdin.once("end", resolve);
  process.once("SIGTERM", resolve);
  process.once("SIGINT", resolve);
});
const browser = await launchChrome(process.argv[2]);
try {
  process.stdout.write(`${JSON.stringify({ pageWsUrl: browser.pageWsUrl })}\n`);
  await disconnected;
} finally {
  await browser.close();
  process.stdin.destroy();
}
