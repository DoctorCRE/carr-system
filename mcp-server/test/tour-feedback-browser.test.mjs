import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { launchChrome } from "../../dealroom/test/chrome-launch.mjs";

const projection = "10000000-0000-4000-8000-000000000001";
const tour = "20000000-0000-4000-8000-000000000001";
const comment = "A long synthetic comment about parking, frontage and access. ".repeat(6);
const chrome = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"].filter(Boolean).find(existsSync);
class DevTools {
  constructor(url) {
    this.serial = 0; this.pending = new Map(); this.socket = new WebSocket(url);
    this.ready = new Promise((resolve, reject) => { this.socket.addEventListener("open", resolve, {once:true}); this.socket.addEventListener("error", reject, {once:true}); });
    this.socket.addEventListener("message", event => { const result = JSON.parse(String(event.data)); const waiter = this.pending.get(result.id); if (!waiter) return; this.pending.delete(result.id); if (result.error) waiter.reject(new Error(JSON.stringify(result.error))); else waiter.resolve(result.result); });
  }
  async call(method, params = {}) { await this.ready; const id = ++this.serial; const promise = new Promise((resolve,reject)=>this.pending.set(id,{resolve,reject})); this.socket.send(JSON.stringify({id,method,params})); return promise; }
  async evaluate(expression) { const value = await this.call("Runtime.evaluate", {expression,returnByValue:true,awaitPromise:true}); if(value.exceptionDetails) throw new Error(JSON.stringify(value.exceptionDetails)); return value.result.value; }
}
async function browserFixture(t) {
  if (!chrome) { t.skip("Chrome is unavailable"); return; }
  const server = createServer(async (request, response) => {
    try {
      const path = new URL(request.url,"http://127.0.0.1").pathname;
      if (path.startsWith("/api/")) {
        const data = path.endsWith("library") ? {tours:[{id:tour,name:"Synthetic Tour"}]} : path.endsWith("detail") ? {id:tour,name:"Synthetic Tour",projection_id:projection,stops:[]} : {feedback:{projection_id:projection,items:[{property_ref:`property:public:${"a".repeat(32)}`,route_label:"A",shortlisted:true,comments:Array.from({length:4},()=>({comment,created_at:"2026-10-01T12:00:00Z"}))}]}};
        response.setHeader("content-type","application/json");response.end(JSON.stringify({data}));return;
      }
      const asset = path === "/" ? "index.html" : path.slice("/tours/".length);
      if (!["index.html","app.js","app.css"].includes(asset)) {response.writeHead(404).end();return;}
      response.setHeader("content-type",asset.endsWith("js")?"text/javascript":asset.endsWith("css")?"text/css":"text/html");
      response.end(await readFile(new URL(`../../dealroom/tours/${asset}`,import.meta.url)));
    } catch {response.writeHead(500).end();}
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);}));
  const browser = await launchChrome(chrome); t.after(()=>browser.close());
  const cdp = new DevTools(browser.pageWsUrl); t.after(()=>cdp.socket.close());
  await cdp.call("Page.navigate",{url:`http://127.0.0.1:${server.address().port}/`});
  for(let i=0;i<200;i++){if(await cdp.evaluate('!!document.querySelector(".tour-button")'))break;await new Promise(resolve=>setTimeout(resolve,10));}
  await cdp.evaluate('document.querySelector(".tour-button").click()');
  for(let i=0;i<200;i++){if(await cdp.evaluate('document.querySelectorAll("#feedback-list p").length===4'))break;await new Promise(resolve=>setTimeout(resolve,10));}
  assert.equal(await cdp.evaluate('document.querySelectorAll("#feedback-list p").length'),4);
  return cdp;
}

test("broker comments stay readable in a vertical stack at phone and desktop widths", {timeout:90000}, async t => {
  const cdp = await browserFixture(t); if(!cdp)return;
  for(const width of [375,1280]){
    await cdp.call("Emulation.setDeviceMetricsOverride",{width,height:900,deviceScaleFactor:1,mobile:width===375});
    const boxes=await cdp.evaluate('JSON.stringify([...document.querySelectorAll("#feedback-list strong,#feedback-list p")].map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};}))');
    const [answer,...comments]=JSON.parse(boxes);
    assert.ok(comments.every(c=>c.width>=200),`comments use readable width at ${width}px: ${boxes}`);
    assert.ok(answer.bottom<=comments[0].y,"property answer appears above comments");
    for(let i=1;i<comments.length;i++)assert.ok(comments[i].y>=comments[i-1].bottom,"comments stack vertically");
    assert.ok(comments.every(c=>c.height<700),"comments do not form word-wide towers");
  }
});

test("each share scope has an accessible name and keyboard toggle within a named group", {timeout:90000}, async t => {
  const cdp = await browserFixture(t); if(!cdp)return;
  const tree=await cdp.call("Accessibility.getFullAXTree");
  const checkboxes=tree.nodes.filter(n=>n.role?.value==="checkbox");
  assert.deepEqual(checkboxes.map(n=>n.name?.value),["View packet","View map","Shortlist","Comment"]);
  assert.ok(tree.nodes.some(n=>n.role?.value==="group"&&n.name?.value==="Scopes"));
  await cdp.evaluate('document.querySelector(\'input[name="scope"]\').focus()');
  for(const scope of ["view_packet","view_map","shortlist","comment"]){
    assert.equal(await cdp.evaluate('document.activeElement.value'),scope);
    const before=await cdp.evaluate('document.activeElement.checked');
    await cdp.call("Input.dispatchKeyEvent",{type:"keyDown",key:" ",code:"Space",windowsVirtualKeyCode:32});
    await cdp.call("Input.dispatchKeyEvent",{type:"keyUp",key:" ",code:"Space",windowsVirtualKeyCode:32});
    assert.equal(await cdp.evaluate('document.activeElement.checked'),!before);
    await cdp.call("Input.dispatchKeyEvent",{type:"keyDown",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
    await cdp.call("Input.dispatchKeyEvent",{type:"keyUp",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
  }
});
