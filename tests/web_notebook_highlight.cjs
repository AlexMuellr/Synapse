// Synthetic notes only. The Python bridge is replaced by a recorder.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const doc=(blocks,extra={})=>({pages:[{id:'A',title:'T',icon:'',blocks}],activePageId:'A',...extra});
const YELLOW='rgb(255, 240, 179)',RED='rgb(197, 61, 61)';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1100,height:700}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.pycmd=()=>{};});
  await page.goto('file://'+root+'/web_notebook/index.html');
  const load=(blocks,extra)=>page.evaluate(d=>loadContent(JSON.stringify(d)),doc(blocks,extra));
  const snap=()=>page.evaluate(()=>JSON.parse(__synapseSnapshotForUnload()));
  const html=async i=>(await snap()).pages[0].blocks[i].html;
  const select=(i,word)=>page.evaluate(([i,word])=>{
   const host=editor.children[i].querySelector('.todo-content')||editor.children[i];
   const at=host.textContent.indexOf(word);if(at<0)return false;
   const locate=pos=>{const walker=document.createTreeWalker(host,NodeFilter.SHOW_TEXT);let n,seen=0;while(n=walker.nextNode()){if(pos<=seen+n.length)return [n,pos-seen];seen+=n.length;}};
   const [sn,so]=locate(at),[en,eo]=locate(at+word.length),r=document.createRange();
   r.setStart(sn,so);r.setEnd(en,eo);host.focus();const sel=getSelection();sel.removeAllRanges();sel.addRange(r);return true;
  },[i,word]);
  await load([{type:'text',html:'alpha beta gamma'},{type:'bullet',html:'delta epsilon'}]);

  // default: yellow background with Ctrl+Shift+H
  assert.ok(await select(0,'beta'));await page.keyboard.press('Control+Shift+H');
  assert.equal(await html(0),`alpha <span style="background-color: ${YELLOW};">beta</span> gamma`,'default shortcut highlights in yellow');
  assert.equal(await page.evaluate(()=>getSelection().toString()),'beta','the words stay selected');
  // pressing it again on exactly that color removes it
  await page.keyboard.press('Control+Shift+H');
  assert.equal(await html(0),'alpha beta gamma','a second press removes the highlight');
  await page.keyboard.press('Control+Shift+H');await page.keyboard.press('Control+Shift+H');
  assert.equal(await html(0),'alpha beta gamma','it toggles');

  // the shortcut follows the last color picked in the palette (text color red)
  assert.ok(await select(0,'gamma'));
  await page.waitForFunction(()=>getComputedStyle(document.getElementById('format-toolbar')).display==='flex');
  await page.click('#format-toolbar button[data-i18n-title="color"]');
  await page.locator('#notebook-popup .nb-swatches').nth(0).locator('button').nth(9).click();
  assert.equal((await snap()).highlightColor.property,'color');assert.equal((await snap()).highlightColor.key,'red');
  assert.ok(await select(1,'delta'));await page.keyboard.press('Control+Shift+H');
  assert.equal(await html(1),`<span style="color: ${RED};">delta</span> epsilon`,'the last picked color (red text) is used');
  // a background pick switches the shortcut back to backgrounds; "Default" never becomes the highlight
  assert.ok(await select(1,'epsilon'));
  await page.waitForFunction(()=>getComputedStyle(document.getElementById('format-toolbar')).display==='flex');
  await page.click('#format-toolbar button[data-i18n-title="color"]');
  await page.locator('#notebook-popup .nb-swatches').nth(1).locator('button').nth(6).click();
  assert.deepEqual((await snap()).highlightColor,{property:'background-color',key:'blue'});
  assert.ok(await select(0,'alpha'));
  await page.waitForFunction(()=>getComputedStyle(document.getElementById('format-toolbar')).display==='flex');
  await page.click('#format-toolbar button[data-i18n-title="color"]');
  await page.locator('#notebook-popup .nb-swatches').nth(1).locator('button').nth(0).click();
  assert.equal((await snap()).highlightColor.key,'blue','picking Default does not change the highlight color');

  // not a selection → nothing happens and the key is not swallowed
  await page.evaluate(()=>{const f=editor.children[0];f.focus();const r=document.createRange();r.setStart(f.firstChild,0);r.collapse(true);const s=getSelection();s.removeAllRanges();s.addRange(r);window.__seen=null;document.addEventListener('keydown',e=>{window.__seen=e.defaultPrevented;});});
  const before=await html(0);await page.keyboard.press('Control+Shift+H');assert.equal(await html(0),before);

  // across blocks, undo, and the history ignores the highlight color itself
  await load([{type:'text',html:'one two'},{type:'text',html:'three four'}],{highlightColor:{property:'background-color',key:'green'}});
  await page.evaluate(()=>{const a=editor.children[0].firstChild,b=editor.children[1].firstChild,r=document.createRange();r.setStart(a,4);r.setEnd(b,5);editor.children[0].focus();const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.press('Control+Shift+H');
  assert.deepEqual([await html(0),await html(1)],['one <span style="background-color: rgb(217, 238, 228);">two</span>','<span style="background-color: rgb(217, 238, 228);">three</span> four'],'a selection across blocks is colored in each block; the stored green is used');
  await page.evaluate(()=>navigateNotebook(-1));
  assert.deepEqual([await html(0),await html(1)],['one two','three four'],'undo removes the highlight');
  assert.equal((await snap()).highlightColor.key,'green','undo never touches the remembered color');

  // the toolbar button does the same and shows the color and shortcut
  await load([{type:'text',html:'button test'}],{highlightColor:{property:'background-color',key:'purple'}});
  const title=await page.locator('#nb-highlight-btn').getAttribute('title');
  assert.match(title,/Highlight with the last color/);assert.match(title,/Purple/);assert.match(title,/Ctrl\+Shift\+H/);
  assert.equal(await page.locator('#nb-highlight-btn .nb-hl-swatch').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(233, 223, 244)');
  assert.ok(await select(0,'test'));
  await page.waitForFunction(()=>getComputedStyle(document.getElementById('format-toolbar')).display==='flex');
  await page.click('#nb-highlight-btn');
  assert.match(await html(0),/background-color: rgb\(233, 223, 244\);">test/);

  // a configured shortcut replaces the default; empty disables; Cmd/Ctrl mapping and F-keys work
  await page.evaluate(()=>synapseSetNotebookShortcuts({highlight:'Alt+M',highlightLabel:'Alt+M'}));
  await load([{type:'text',html:'custom keys'}]);
  assert.ok(await select(0,'custom'));await page.keyboard.press('Control+Shift+H');
  assert.equal(await html(0),'custom keys','the old shortcut no longer fires');
  await page.keyboard.press('Alt+m');assert.match(await html(0),/<span style="background-color: rgb\(255, 240, 179\);">custom<\/span>/,'the new shortcut works');
  assert.match(await page.locator('#nb-highlight-btn').getAttribute('title'),/Alt\+M/);
  await page.evaluate(()=>synapseSetNotebookShortcuts({highlight:'F6'}));
  await load([{type:'text',html:'function key'}]);assert.ok(await select(0,'function'));await page.keyboard.press('F6');
  assert.match(await html(0),/<span style="background-color/);
  await page.evaluate(()=>synapseSetNotebookShortcuts({highlight:'Ctrl+Alt+1'}));
  await load([{type:'text',html:'digit key'}]);assert.ok(await select(0,'digit'));await page.keyboard.press('Control+Alt+1');
  assert.match(await html(0),/<span style="background-color/);
  await page.evaluate(()=>synapseSetNotebookShortcuts({highlight:''}));
  await load([{type:'text',html:'disabled'}]);assert.ok(await select(0,'disabled'));await page.keyboard.press('Control+Alt+1');await page.keyboard.press('Control+Shift+H');
  assert.equal(await html(0),'disabled','an empty shortcut turns the keyboard shortcut off');
  await page.evaluate(()=>synapseSetNotebookShortcuts(null));await page.evaluate(()=>synapseSetNotebookShortcuts('x'));

  // never inside other inputs
  await page.evaluate(()=>synapseSetNotebookShortcuts({highlight:'Ctrl+Shift+H'}));
  await page.fill('#searchInput','find me');await page.evaluate(()=>{const i=document.getElementById('searchInput');i.focus();i.select();});
  await page.keyboard.press('Control+Shift+H');assert.equal(await page.inputValue('#searchInput'),'find me');
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS highlight shortcut: last color, toggle, palette link, configurable keys, button, undo');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
