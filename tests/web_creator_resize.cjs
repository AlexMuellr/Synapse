// The Card Creator can be made wider/narrower by dragging its left edge (notebook, and PDF tab in the wide layout).
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  for (const target of ['pdf','notebook']) {
   const ctx=await browser.newContext({viewport:{width:1400,height:900}});
   const page=await ctx.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>{window.__cmds=[];window.pycmd=c=>window.__cmds.push(c);});
   const P=target==='pdf'?'#card-creator-panel':'#nb-card-creator', H=target==='pdf'?'#creator-resizer':'#nbc-resizer';
   const open=async()=>{
     if (target==='pdf') {
       await page.goto('file://'+root+'/web_notebook/pdf_viewer.html');
       await page.evaluate(()=>{document.body.classList.add('synapse-wide');showViewer('a.pdf','/lib/a.pdf');currentPdfPath='/lib/a.pdf';currentPdfName='a.pdf';currentPdfId='p1';openCardCreator();});
     } else {
       await page.goto('file://'+root+'/web_notebook/index.html');
       await page.waitForFunction(()=>typeof window.nbcOpen==='function');
       await page.evaluate(()=>nbcOpen());
     }
   };
   const width=()=>page.evaluate(s=>document.querySelector(s).getBoundingClientRect().width,P);
   await open();
   const start=await width();
   const box=await page.locator(H).boundingBox();
   const y=box.y+200, x=box.x+box.width/2;
   // drag left by 150px -> 150px wider
   await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x-80,y,{steps:4});await page.mouse.move(x-150,y,{steps:4});await page.mouse.up();
   const wider=await width();
   assert.ok(Math.abs(wider-(start+150))<4,target+': wider by the drag distance: '+start+' -> '+wider);
   // clamps: tiny and huge
   const b2=await page.locator(H).boundingBox();
   await page.mouse.move(b2.x+3,y);await page.mouse.down();await page.mouse.move(1390,y,{steps:5});await page.mouse.up();
   assert.equal(Math.round(await width()),280,target+': minimum width');
   const b3=await page.locator(H).boundingBox();
   await page.mouse.move(b3.x+3,y);await page.mouse.down();await page.mouse.move(5,y,{steps:5});await page.mouse.up();
   const max=await width();
   assert.ok(max<=760&&max>=600,target+': maximum width is capped: '+max);
   // remembered after reload
   await page.mouse.move((await page.locator(H).boundingBox()).x+3,y);await page.mouse.down();await page.mouse.move(1000,y,{steps:5});await page.mouse.up();
   const set=await width();
   await open();
   assert.ok(Math.abs(await width()-set)<2,target+': width survives a reload');
   // double-click resets
   await page.locator(H).dblclick();
   assert.ok(Math.abs(await width()-start)<2,target+': double-click restores the default');
   assert.deepEqual(errors,[],target+': no page errors');
   await ctx.close();
  }
  console.log('PASS card creator resize: drag, clamp, remember, reset (notebook and PDF tab)');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
