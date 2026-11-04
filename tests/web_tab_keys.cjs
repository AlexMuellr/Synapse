// The Ctrl+Tab handler that every tool page gets (workspace_tab_keys.js), tested on a page with a focused editor.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const root=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const source=fs.readFileSync(path.join(root,'workspace_tab_keys.js'),'utf8').split('__BRIDGE__').join('notion_mini');
  await page.setContent('<body><div id="field" contenteditable="true" tabindex="0">text</div><input id="box" value="x"><button id="b">b</button></body>');
  await page.evaluate(()=>{window.__cmds=[];window.pycmd=c=>window.__cmds.push(c);});
  await page.addScriptTag({content:source});
  await page.addScriptTag({content:source});     // injected twice (reload): still one handler
  const cmds=()=>page.evaluate(()=>window.__cmds.splice(0));
  await page.focus('#field');
  await page.keyboard.press('Control+Tab');assert.deepEqual(await cmds(),['notion_mini:switch:next']);
  await page.keyboard.press('Control+Shift+Tab');assert.deepEqual(await cmds(),['notion_mini:switch:prev']);
  await page.keyboard.press('Control+PageDown');assert.deepEqual(await cmds(),['notion_mini:switch:next']);
  await page.keyboard.press('Control+PageUp');assert.deepEqual(await cmds(),['notion_mini:switch:prev']);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'field','Ctrl+Tab does not move the focus');
  await page.focus('#box');await page.keyboard.press('Control+Tab');assert.deepEqual(await cmds(),['notion_mini:switch:next'],'it also works from an input');
  // plain keys keep working: Tab moves focus, other Ctrl chords are untouched
  await page.focus('#field');await page.keyboard.press('Tab');assert.deepEqual(await cmds(),[]);
  assert.notEqual(await page.evaluate(()=>document.activeElement.id),'field','a plain Tab still moves the focus');
  await page.keyboard.press('Control+a');await page.keyboard.press('Alt+Tab');await page.keyboard.press('Control+Alt+Tab');await page.keyboard.press('Control+Shift+PageDown');
  assert.deepEqual(await cmds(),[]);
  // a page that already handled the key wins
  await page.evaluate(()=>{document.addEventListener('keydown',e=>{if(e.key==='PageDown'&&e.ctrlKey&&e.shiftKey)e.preventDefault();},true);});
  assert.deepEqual(errors,[]);
  console.log('PASS Ctrl+Tab / Ctrl+PageUp/Down switch tabs from fields, plain keys unchanged, double injection safe');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
