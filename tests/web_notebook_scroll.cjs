// Synthetic notes only. The Python bridge is replaced by a recorder.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const long=n=>Array.from({length:n},(_,i)=>({type:'text',html:'Line number '+i}));
const doc=(a,b)=>({pages:[{id:'A',title:'A',icon:'',blocks:long(a)},{id:'B',title:'B',icon:'',blocks:long(b)}],activePageId:'A'});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  // ---- notebook: the position handed in by Python is restored; scrolling is reported back ----
  const page=await browser.newPage({viewport:{width:1000,height:600}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.__cmds=[];window.pycmd=c=>window.__cmds.push(c);window.__SYNAPSE_VIEW_STATE__={page:'A',scroll:{A:700}};});
  await page.goto('file://'+root+'/web_notebook/index.html');
  const top=()=>page.evaluate(()=>document.querySelector('.scroll-wrapper').scrollTop);
  const views=()=>page.evaluate(()=>window.__cmds.filter(c=>c.startsWith('notion_mini:view:')).map(c=>JSON.parse(c.slice('notion_mini:view:'.length))));
  await page.evaluate(d=>loadContent(JSON.stringify(d)),doc(120,120));
  await page.waitForTimeout(400);
  const restored=await top();
  assert.ok(Math.abs(restored-700)<=3,'the page opens where the reader left it, not at the top ('+restored+')');
  // the reader scrolls: the position is reported (debounced) with the page id
  await page.evaluate(()=>{document.querySelector('.scroll-wrapper').scrollTop=1200;});
  await page.waitForTimeout(500);
  let last=(await views()).at(-1);
  assert.equal(last.tool,'notebook');assert.equal(last.state.page,'A');assert.equal(last.state.scroll.A,1200);
  // switching pages inside the notebook: each page keeps its own position
  await page.evaluate(()=>loadPage('B',true));await page.waitForTimeout(200);
  assert.equal(await top(),0,'a page that was never scrolled starts at the top');
  await page.evaluate(()=>{document.querySelector('.scroll-wrapper').scrollTop=300;});await page.waitForTimeout(500);
  await page.evaluate(()=>loadPage('A',true));await page.waitForTimeout(500);
  assert.ok(Math.abs(await top()-1200)<=3,'back on the first page, it is where it was');
  last=(await views()).at(-1);assert.equal(last.state.scroll.B,300,'both positions are kept');
  // a reload of the data (for example after a sync) keeps the position too
  await page.evaluate(d=>loadContent(JSON.stringify(d)),doc(120,120));await page.waitForTimeout(500);
  assert.ok(Math.abs(await top()-1200)<=3,'reloading the same data keeps the reader in place');
  // the reader is not pulled back while the position is being restored
  await page.evaluate(d=>loadContent(JSON.stringify(d)),doc(120,120));
  await page.evaluate(()=>{document.querySelector('.scroll-wrapper').scrollTop=50;});
  await page.waitForTimeout(1100);
  assert.ok(await top()<200,'scrolling during the restore wins ('+await top()+')');
  // garbage from Python is ignored
  const bad=await browser.newPage({viewport:{width:1000,height:600}});
  await bad.addInitScript(()=>{window.pycmd=()=>{};window.__SYNAPSE_VIEW_STATE__={scroll:{A:'x',B:null,C:{},D:Infinity}};});
  await bad.goto('file://'+root+'/web_notebook/index.html');
  await bad.evaluate(d=>loadContent(JSON.stringify(d)),doc(40,40));await bad.waitForTimeout(300);
  assert.equal(await bad.evaluate(()=>document.querySelector('.scroll-wrapper').scrollTop),0);

  // ---- to-do list ----
  const todo=await browser.newPage({viewport:{width:600,height:400}});const todoErrors=[];todo.on('pageerror',e=>todoErrors.push(e.message));
  await todo.addInitScript(()=>{window.__cmds=[];window.pycmd=c=>window.__cmds.push(c);window.__SYNAPSE_VIEW_STATE__={scroll:650};});
  await todo.goto('file://'+root+'/web_notebook/todo.html');
  const items=JSON.stringify(Array.from({length:80},(_,i)=>({id:'t'+i,text:'Task '+i,done:false,tag:'',createdAt:1})));
  await todo.evaluate(j=>window.loadTodos(j),items);await todo.waitForTimeout(400);
  const pos=await todo.evaluate(()=>document.getElementById('todo-list-wrap').scrollTop);
  assert.ok(Math.abs(pos-650)<=3,'the task list also opens where it was ('+pos+')');
  await todo.evaluate(()=>{document.getElementById('todo-list-wrap').scrollTop=900;});await todo.waitForTimeout(500);
  const sent=await todo.evaluate(()=>window.__cmds.filter(c=>c.startsWith('notion_mini:view:')).map(c=>JSON.parse(c.slice('notion_mini:view:'.length))));
  assert.deepEqual(sent.at(-1),{tool:'todo',state:{scroll:900}});
  assert.deepEqual(errors,[],'no page errors');assert.deepEqual(todoErrors,[],'no to-do page errors');
  console.log('PASS scroll position: restored per page and per tool, reported when scrolling, reader wins, bad data ignored');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
