const { chromium } = require("@playwright/test");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const base = process.argv[2] || "http://127.0.0.1:8189";
const scratch = path.resolve(process.argv[3] || "comfyui-qa");
const root = path.resolve(__dirname, "..");
fs.mkdirSync(scratch, {recursive: true});
const reportFile = path.join(scratch, "fixture_report.json");
let browser;
(async () => {
  browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({viewport: {width:1500,height:960},locale:"en-US"});
  const page = await context.newPage();
  const before = fs.existsSync(reportFile) ? JSON.parse(fs.readFileSync(reportFile)) : {charged:0};
  const chargesBefore = before.charged;
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await context.route("https://mcp.cormak.app/mcp/authorize?**", async route => {
    const url = new URL(route.request().url());
    assert.equal(url.searchParams.get("code_challenge_method"),"S256");
    assert.equal(url.searchParams.get("scope"),"account:read studio3d:use studio3d:read");
    const callback = new URL(url.searchParams.get("redirect_uri"));
    callback.searchParams.set("state",url.searchParams.get("state"));
    callback.searchParams.set("code","cmcp_c_"+"c".repeat(43));
    await route.fulfill({status:302,headers:{"location": callback.href}});
  });
  await context.route("https://app.cormak.app/mcp?**", route =>
    route.fulfill({contentType:"text/html",body:"<p>Test approval only. Return to ComfyUI and requeue.</p>"}));
  await page.goto(base,{waitUntil:"networkidle"});
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);
  await page.evaluate(async () => {
    const {app} = await import("/scripts/app.js");
    app.extensionManager.registerSidebarTab; // available official API
    window.__qa_app = app;
  });
  const sidebar = page.locator('[data-testid="sidebar-tab-cormak"]');
  await page.screenshot({path:path.join(scratch, 'initial.png')});
  const entry = page.getByRole("button",{name:"Cormak account and AI credit cost"});
  if(await sidebar.count()) await sidebar.click();
  else if(await entry.count()) await entry.click();
  else await page.locator('[data-sidebar-tab-id="cormak"],[title="Cormak account and AI credit cost"],[title="Cormak"]').first().click();

  await page.getByRole("button",{name:"Connect Cormak",exact:true}).waitFor();
  const [popup] = await Promise.all([
    page.waitForEvent("popup"),
    page.getByRole("button",{name:"Connect Cormak",exact:true}).click()
  ]);
  await page.getByText("130 AI credits · test",{exact:true}).waitFor({timeout:15000});
  await popup.close();
  await page.getByRole("button",{name:"Check credit cost",exact:true}).click();
  await page.getByText(/basic: up to 25 AI credits/).waitFor();

  const load = async(name) => {
    const data=JSON.parse(fs.readFileSync(path.join(root,"workflows",name+".json")));
    await page.evaluate(async data => {
      const {app} = await import("/scripts/app.js");
      await app.loadGraphData(data);
      for(const node of app.graph._nodes){
        const cap=node.widgets?.find(x => x.name==="credit_cap");
        if(cap) cap.value=25;
      }
      app.canvas.setDirty(true,true);
    },data);
    await page.evaluate(() => {
      const app = window.__qa_app;
      app.extensionManager.toast.removeAll?.();
      app.canvas.ds.scale = .7;
      app.canvas.ds.offset = [600,145];
      app.canvas.setDirty(true,true);
    });
    await page.waitForTimeout(1200);
  };
  const queue = async () => {
    const response = page.waitForResponse(r=>r.request().method()==="POST" && new URL(r.url()).pathname.endsWith("/prompt"));
    await page.evaluate(async () => {
      const {app} = await import("/scripts/app.js");
      await app.queuePrompt(0,1);
    });
    const queued = await response;
    const result = await queued.json();
    assert(queued.ok(), JSON.stringify(result));
    return result;
  };
  const history = async id => {
    for(let i=0;i<150;i++){
      const response=await context.request.get(base+"/history/"+id);
      const result=(await response.json())[id];
      if(result?.status?.completed || result?.status?.status_str==="error") return result;
      await page.waitForTimeout(200);
    }
    throw new Error("Timed out waiting for "+id);
  };

  await load("text-to-3d");
  const attempt=await queue();
  const pending=await history(attempt.prompt_id);
  console.log("Approval required; verifying same-request retry.");
  assert.equal(pending.status.status_str,"error");
  await page.getByRole("link",{name:/Approve job /}).waitFor();
  const [approval] = await Promise.all([
    page.waitForEvent("popup"),
    page.getByRole("link",{name:/Approve job /}).click()
  ]);
  await approval.close();
  const textRun=await queue();
  const textResult=await history(textRun.prompt_id);
  fs.writeFileSync(path.join(scratch, 'text-history.json'),JSON.stringify(textResult,null,2));
  assert.equal(textResult.status.status_str,"success",JSON.stringify(textResult.status));
  await page.evaluate(() => window.__qa_app.extensionManager.toast.removeAll?.());
  await page.waitForTimeout(2000);
  for (const close of await page.locator('.p-toast-close-button').all()) await close.click();

  await page.screenshot({path:path.join(scratch, 'text-to-3d.png')});
  const repeats=await queue();
  const repeated=await history(repeats.prompt_id);
  assert.equal(repeated.status.status_str,"success");
  assert.equal(JSON.parse(fs.readFileSync(reportFile)).charged,chargesBefore+1);
  await load("image-to-3d");
  const imageRun=await queue();
  const imageResult=await history(imageRun.prompt_id);
  fs.writeFileSync(path.join(scratch, 'image-history.json'),JSON.stringify(imageResult,null,2));
  assert.equal(imageResult.status.status_str,"success",JSON.stringify(imageResult.status));
  await page.evaluate(() => window.__qa_app.extensionManager.toast.removeAll?.());
  await page.waitForTimeout(2000);
  for (const close of await page.locator('.p-toast-close-button').all()) await close.click();

  await page.screenshot({path:path.join(scratch, 'image-to-3d.png')});
  assert.equal(JSON.parse(fs.readFileSync(reportFile)).charged,chargesBefore+2);
  const cookie=(await context.cookies()).find(x => x.name==="cormak_browser");
  assert(cookie?.httpOnly);
  for(const item of [textResult,repeated,imageResult]){
    const serialized=JSON.stringify(item);
    assert(!serialized.includes(cookie.value) && !/cmcp_[ar]_/.test(serialized));
  }
  const jobMatch = pending.status.messages.find(m=>m[0]==="execution_error")[1].exception_message.match(/Job: ([a-f0-9-]{36})/);
  assert(jobMatch);
  await load("generate-and-reuse");
  await page.evaluate(job=>{
    const app = window.__qa_app;
    app.graph._nodes.find(n=>n.type==="CormakGenerationStatus").widgets.find(w=>w.name==="job_id").value=job;
  },jobMatch[1]);
  const reuseRun = await queue();
  const reuseResult = await history(reuseRun.prompt_id);
  fs.writeFileSync(path.join(scratch, 'reuse-history.json'),JSON.stringify(reuseResult,null,2));
  assert.equal(reuseResult.status.status_str,"success",JSON.stringify(reuseResult.status));
  assert.equal(JSON.parse(fs.readFileSync(reportFile)).charged,chargesBefore+2);
  await page.getByRole("button",{name:"Disconnect Cormak",exact:true}).click();
  await page.getByRole("button",{name:"Connect Cormak",exact:true}).waitFor();
  console.log("page errors",errors);
  fs.writeFileSync(path.join(scratch, 'browser-report.json'),JSON.stringify({passed:true,errors,stats:JSON.parse(fs.readFileSync(reportFile)),charges_this_run:2},null,2));
  await browser.close();
})().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
