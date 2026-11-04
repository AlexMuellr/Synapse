// Synthetic notes only. Clipboard events are observed/dispatched in the page.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const doc=blocks=>({pages:[{id:'A',title:'T',icon:'',blocks}],activePageId:'A'});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1000,height:700}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.pycmd=()=>{};window.__clip=null;});
  await page.goto('file://'+root+'/web_notebook/index.html');
  // Registered after the app's own listeners, so it sees what the app put on the clipboard.
  await page.evaluate(()=>{document.addEventListener('copy',e=>{window.__clip={plain:e.clipboardData.getData('text/plain'),html:e.clipboardData.getData('text/html'),blocks:e.clipboardData.getData('application/x-synapse-blocks'),prevented:e.defaultPrevented};});});
  const load=blocks=>page.evaluate(d=>loadContent(JSON.stringify(d)),doc(blocks));
  const blocks=()=>page.evaluate(()=>JSON.parse(__synapseSnapshotForUnload()).pages[0].blocks);
  const selected=()=>page.evaluate(()=>Array.from(editor.children).map((b,i)=>b.classList.contains('block-selected')?i:-1).filter(i=>i>=0));
  const box=i=>page.locator('#editor > *').nth(i).boundingBox();
  const drag=async(from,to)=>{const a=await box(from),c=await box(to);await page.mouse.move(a.x+8,a.y+a.height/2);await page.mouse.down();await page.mouse.move(c.x+60,c.y+c.height/2,{steps:8});await page.mouse.up();};
  const paste=(data,targetSelector)=>page.evaluate(([data,sel])=>{
   const dt=new DataTransfer();dt.setData('application/x-synapse-blocks',typeof data==='string'?data:JSON.stringify(data));
   const target=sel?document.querySelector(sel):document.body;
   target.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));
  },[data,targetSelector||null]);
  const sample=[{type:'h2',html:'Title'},{type:'bullet',html:'one <b>bold</b>'},{type:'numbered',html:'two',start:3},{type:'numbered',html:'sub',indent:1},{type:'todo',html:'task',checked:true},{type:'latex',html:'',latex:'x^2'},{type:'text',html:'last'}];
  await load(sample);

  // ---- copy several lines after dragging over them ----
  await drag(0,3);
  assert.deepEqual(await selected(),[0,1,2,3],'dragging over blocks selects them');
  await page.keyboard.press('Control+c');
  const clip=await page.evaluate(()=>window.__clip);
  assert.ok(clip.prevented,'the app takes over copying for block selections');
  assert.equal(clip.plain,'## Title\n- one bold\n3. two\n  a. sub'.replace('  a. sub','  1. sub'),'plain text keeps the structure');
  assert.match(clip.html,/<h2>Title<\/h2><ul><li>one <b>bold<\/b><\/li><\/ul><ol><li>two<\/li><li>sub<\/li><\/ol>/);
  const payload=JSON.parse(clip.blocks);assert.equal(payload.version,1);
  assert.deepEqual(payload.blocks.map(b=>b.type),['h2','bullet','numbered','numbered']);
  assert.equal(payload.blocks[2].start,3);assert.equal(payload.blocks[3].indent,1);
  assert.ok(payload.blocks.every(b=>!('id' in b)),'no stable ids are copied');
  assert.deepEqual((await blocks()).map(b=>b.type),sample.map(b=>b.type),'copying changes nothing');

  // ---- cut removes them, undo brings them back ----
  await page.keyboard.press('Control+x');
  assert.deepEqual((await blocks()).map(b=>b.type),['todo','latex','text'],'cut removes the selected blocks');
  assert.equal(JSON.parse(await page.evaluate(()=>window.__clip.blocks)).blocks.length,4,'the cut blocks are on the clipboard');
  await page.evaluate(()=>navigateNotebook(-1));
  assert.equal((await blocks()).length,7,'undo restores cut blocks');

  // ---- paste back: types, numbering and indent survive; position and caret ----
  await load([{type:'text',html:'before'},{type:'text',html:''},{type:'text',html:'after'}]);
  await page.evaluate(()=>{const f=editor.children[1];f.focus();});
  await paste(payload,'#editor > :nth-child(2)');
  let got=await blocks();
  assert.deepEqual(got.map(b=>b.type),['text','h2','bullet','numbered','numbered','text'],'an empty block is replaced by the pasted blocks');
  assert.equal(got[3].start,3);assert.equal(got[4].indent,1);assert.equal(got[2].html,'one <b>bold</b>');
  assert.equal(await page.evaluate(()=>{const s=getSelection();return editor.children[4].contains(s.anchorNode);}),true,'the caret ends in the last pasted block');
  await load([{type:'text',html:'before'},{type:'text',html:'has text'}]);
  await paste(payload,'#editor > :nth-child(2)');
  assert.deepEqual((await blocks()).map(b=>b.type),['text','text','h2','bullet','numbered','numbered'],'a block with text keeps it and the paste goes below');

  // ---- hostile clipboard data is cleaned ----
  await load([{type:'text',html:''}]);
  await paste({blocks:[{type:'text evil" onload="1',html:'<img src=x onerror=window.__pwned=1><script>window.__pwned=2</script>ok',id:'steal',indent:99,start:-4,extra:{a:1}},null,'x',{type:'latex',latex:5}]},'#editor > :nth-child(1)');
  got=await blocks();
  assert.equal(await page.evaluate(()=>window.__pwned),undefined);
  assert.equal(got[0].type,'text');assert.ok(!('id' in got[0]));assert.ok(!('extra' in got[0]));assert.ok(!/script|onerror/i.test(got[0].html));assert.equal(got[0].indent,undefined,'indent applies to list blocks only');
  await paste('not json','#editor > :nth-child(1)');await paste({blocks:'nope'},'#editor > :nth-child(1)');
  await paste({blocks:Array.from({length:900},(_,i)=>({type:'text',html:String(i)}))},'#editor > :nth-child(1)');
  assert.ok((await blocks()).length<=502,'a huge paste is capped');

  // ---- keyboard selection: Ctrl+A twice, Shift+Down/Up, typing ends it ----
  await load(sample);
  await page.evaluate(()=>{const f=editor.children[1];f.focus();const r=document.createRange();r.selectNodeContents(f);r.collapse(false);const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.press('Control+a');
  assert.deepEqual(await selected(),[],'the first Ctrl+A only selects the text of the block');
  await page.keyboard.press('Control+a');
  assert.deepEqual(await selected(),[0,1,2,3,4,5,6],'the second Ctrl+A selects all blocks');
  await page.keyboard.press('Control+c');
  assert.equal(JSON.parse(await page.evaluate(()=>window.__clip.blocks)).blocks.length,7);
  await page.keyboard.press('Escape');assert.deepEqual(await selected(),[],'Escape clears a keyboard block selection');
  assert.equal((await blocks()).length,7,'and does not insert a block');
  await page.evaluate(()=>{const f=editor.children[1];f.focus();const r=document.createRange();r.selectNodeContents(f);r.collapse(false);const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.press('Shift+ArrowDown');assert.deepEqual(await selected(),[1,2],'Shift+Down at the end of a block starts a block selection');
  await page.keyboard.press('Shift+ArrowDown');assert.deepEqual(await selected(),[1,2,3]);
  await page.keyboard.press('Shift+ArrowUp');assert.deepEqual(await selected(),[1,2]);
  await page.keyboard.press('Shift+ArrowUp');await page.keyboard.press('Shift+ArrowUp');assert.deepEqual(await selected(),[0,1],'it can also grow upwards past the anchor');
  await page.keyboard.press('Shift+ArrowUp');assert.deepEqual(await selected(),[0,1],'and stops at the first block');
  await page.keyboard.press('x');assert.deepEqual(await selected(),[],'typing ends the selection');
  // Shift+Arrow in the middle of text keeps native text selection
  await load(sample);
  await page.evaluate(()=>{const f=editor.children[6];f.focus();const r=document.createRange();r.setStart(f.firstChild,1);r.collapse(true);const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.press('Shift+ArrowRight');assert.deepEqual(await selected(),[]);
  assert.equal(await page.evaluate(()=>getSelection().toString()),'a');

  await page.evaluate(()=>{const f=editor.children[1];f.focus();const r=document.createRange();r.selectNodeContents(f);r.collapse(false);const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.press('Shift+ArrowDown');await page.keyboard.press('Shift+ArrowDown');
  assert.deepEqual(await selected(),[1,2,3]);
  await page.keyboard.press('Backspace');
  assert.deepEqual((await blocks()).map(b=>b.type),['h2','todo','latex','text'],'Backspace deletes the selected blocks and nothing else');

  // ---- paste while blocks are selected: goes after the selection ----
  await load(sample);
  await drag(0,1);
  assert.deepEqual(await selected(),[0,1]);
  await paste({blocks:[{type:'quote',html:'inserted'}]});
  got=await blocks();
  assert.deepEqual(got.slice(0,4).map(b=>b.type),['h2','bullet','quote','numbered'],'inserted after the last selected block');
  assert.deepEqual(await selected(),[],'the selection is cleared afterwards');

  // ---- normal text copy and paste is untouched ----
  await load([{type:'text',html:'plain words here'}]);
  await page.evaluate(()=>{const f=editor.children[0];f.focus();const r=document.createRange();r.setStart(f.firstChild,0);r.setEnd(f.firstChild,5);const s=getSelection();s.removeAllRanges();s.addRange(r);window.__clip=null;});
  await page.keyboard.press('Control+c');
  assert.equal(await page.evaluate(()=>window.__clip&&window.__clip.prevented),false,'text selections use the browser copy');
  await page.evaluate(()=>{const dt=new DataTransfer();dt.setData('text/html','<b>rich</b>');dt.setData('text/plain','rich');editor.children[0].dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));});
  assert.match((await blocks())[0].html,/rich/,'ordinary HTML paste still works');
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS copy/cut/paste of several blocks, Ctrl+A and Shift+Arrow block selection, hostile clipboard data');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
