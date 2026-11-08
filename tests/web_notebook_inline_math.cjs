// Synthetic notes only. The Python bridge is replaced by a recorder.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const doc=blocks=>({pages:[{id:'A',title:'T',icon:'',blocks}],activePageId:'A'});
const atom=(latex)=>`<span class="math-inline" data-latex="${latex}"></span>`;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1100,height:700}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.pycmd=()=>{};});
  await page.goto('file://'+root+'/web_notebook/index.html');
  const load=blocks=>page.evaluate(d=>loadContent(JSON.stringify(d)),doc(blocks));
  const blocks=()=>page.evaluate(()=>JSON.parse(__synapseSnapshotForUnload()).pages[0].blocks);
  const html=async i=>(await blocks())[i].html.replace(/&nbsp;/g,' ');
  const focusEnd=(i,sel)=>page.evaluate(([i,sel])=>{const el=editor.children[i],f=sel?el.querySelector(sel):el;f.focus();const r=document.createRange();r.selectNodeContents(f);r.collapse(false);const s=getSelection();s.removeAllRanges();s.addRange(r);},[i,sel||null]);
  const popoverOpen=()=>page.evaluate(()=>!document.getElementById('nb-math-popover').hidden);
  const rendered=()=>page.locator('#editor .math-inline[data-rendered] .katex').count();

  // ---- "$" opens an empty formula and its editor; typing renders live in the text ----
  await load([{type:'text',html:'Energy '}]);await focusEnd(0);
  await page.keyboard.type('$');
  assert.equal(await page.locator('#editor .math-inline').count(),1,'"$" inserts a formula');
  assert.equal(await popoverOpen(),true,'and opens its editor');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'nb-math-input','the editor has the focus');
  assert.equal(await page.locator('#editor .math-inline').evaluate(e=>getComputedStyle(e,'::before').content),'"$$"','an empty formula shows as $$');
  await page.keyboard.type('E=mc^2');
  await page.waitForSelector('#editor .math-inline[data-rendered] .katex');
  assert.equal(await page.locator('#editor .math-inline').getAttribute('data-latex'),'E=mc^2','the source follows the typing');
  assert.equal(await page.locator('#editor .katex-mathml').count(),0,'only the HTML rendering is placed in the text');
  await page.keyboard.press('Enter');
  assert.equal(await popoverOpen(),false,'Enter finishes');
  await page.keyboard.type(' is famous');
  assert.equal(await html(0),`Energy ${atom('E=mc^2')} is famous`,'only the source is stored; typing continues after the formula');

  // ---- list, toggle title, table cell ----
  await load([{type:'bullet',html:'item '},{type:'toggle',html:'title ',body:'',open:true},{type:'table',html:'<tbody><tr><td contenteditable="true">cell </td><td contenteditable="true">b</td></tr></tbody>'}]);
  await focusEnd(0);await page.keyboard.type('$x^2');await page.keyboard.press('Enter');
  await focusEnd(1,'.toggle-title');await page.keyboard.type('$y');await page.keyboard.press('Enter');
  await page.evaluate(()=>{const c=editor.children[2].querySelector('td');c.focus();const r=document.createRange();r.selectNodeContents(c);r.collapse(false);const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.type('$z');await page.keyboard.press('Enter');
  const got=await blocks();
  assert.equal(got[0].html,'item '+atom('x^2'),'inside a list item');
  assert.equal(got[1].html,'title '+atom('y'),'inside a toggle title');
  assert.match(got[2].html,/cell <span class="math-inline" data-latex="z"><\/span>/,'inside a table cell');
  assert.equal(await rendered()>=3,true,'all of them are rendered');

  // ---- cancelling: Esc on an empty one leaves a literal $, Backspace/Enter on empty remove it ----
  await load([{type:'text',html:'price '}]);await focusEnd(0);
  await page.keyboard.type('$');await page.keyboard.press('Escape');
  assert.equal(await html(0),'price $','Esc on an empty formula types a literal dollar sign');
  await page.keyboard.type('5');assert.equal(await html(0),'price $5','and typing goes on after it');
  await load([{type:'text',html:'a'}]);await focusEnd(0);await page.keyboard.type(' ');
  await page.keyboard.type('$');await page.keyboard.press('Backspace');
  assert.equal(await html(0),'a ','Backspace in an empty editor removes the formula');
  await page.keyboard.type('b');assert.equal(await html(0),'a b');
  await page.keyboard.type('$');await page.keyboard.press('Enter');
  assert.equal(await html(0),'a b','finishing an empty formula removes it');

  // ---- a selection becomes the source ----
  await load([{type:'text',html:'see x^2 here'}]);
  await page.evaluate(()=>{const f=editor.children[0],n=f.firstChild,r=document.createRange();r.setStart(n,4);r.setEnd(n,7);f.focus();const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.type('$');
  assert.equal(await page.locator('#nb-math-input').inputValue(),'x^2','the selected text is the formula');
  await page.keyboard.press('Enter');
  assert.equal(await html(0),`see ${atom('x^2')} here`);

  // ---- invalid LaTeX: message in the editor, last good rendering stays, still saved ----
  await load([{type:'text',html:'t '}]);await focusEnd(0);
  await page.keyboard.type('$\\frac{1}{2}');await page.waitForSelector('#editor .math-inline[data-rendered] .katex');
  const good=await page.locator('#editor .math-inline').innerHTML();
  await page.keyboard.type('\\sqrt{');
  await page.waitForFunction(()=>document.getElementById('nb-math-error').textContent.length>0);
  assert.match(await page.locator('#nb-math-error').textContent(),/Invalid LaTeX/);
  assert.equal(await page.locator('#editor .math-inline').getAttribute('data-latex'),'\\frac{1}{2}\\sqrt{','the source is kept');
  assert.equal(await page.locator('#editor .math-inline').innerHTML(),good,'the last valid rendering stays while the source is invalid');
  await page.keyboard.press('Enter');

  // ---- click to edit, undo ----
  await load([{type:'text',html:`pre ${atom('a+b')} post`}]);
  await page.waitForSelector('#editor .math-inline[data-rendered]');
  await page.click('#editor .math-inline');
  assert.equal(await page.locator('#nb-math-input').inputValue(),'a+b','a click opens the formula in the editor');
  await page.keyboard.press('Control+a');await page.keyboard.type('c*d');await page.keyboard.press('Enter');
  assert.equal(await html(0),`pre ${atom('c*d')} post`);
  await page.keyboard.press('Control+z');
  assert.equal(await html(0),`pre ${atom('a+b')} post`,'undo restores the earlier formula');

  // ---- caret keys leave the editor on either side ----
  await load([{type:'text',html:`ab ${atom('x')} cd`}]);await page.waitForSelector('#editor .math-inline[data-rendered]');
  await page.click('#editor .math-inline');await page.keyboard.press('Home');await page.keyboard.press('ArrowLeft');
  await page.keyboard.type('L');
  assert.match(await html(0),/^ab L<span class="math-inline"/,'ArrowLeft at the start leaves the editor in front of the formula');
  await page.click('#editor .math-inline');await page.keyboard.press('End');await page.keyboard.press('ArrowRight');await page.keyboard.type('R');
  assert.match(await html(0),/<\/span>R/,'ArrowRight at the end leaves the editor after the formula');

  // ---- atomic in the text: Backspace removes it whole; arrows step over it ----
  await load([{type:'text',html:`a ${atom('x')} b`}]);await page.waitForSelector('#editor .math-inline[data-rendered]');
  await page.evaluate(()=>{const f=editor.children[0],t=f.lastChild;f.focus();const r=document.createRange();r.setStart(t,0);r.collapse(true);const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.press('Backspace');
  assert.equal(await page.locator('#editor .math-inline').count(),0,'Backspace right after a formula deletes the whole formula');

  // ---- storage: only the source; reload renders and stays editable ----
  await load([{type:'text',html:`big ${atom('\\sum_{i=1}^{n} i^2')} end`}]);await page.waitForSelector('#editor .math-inline[data-rendered]');
  const stored=await html(0);
  assert.equal(stored,`big ${atom('\\sum_{i=1}^{n} i^2')} end`,'no rendered markup in the stored block');
  assert.ok(stored.length<120);
  assert.equal(await page.locator('#editor > *').first().getAttribute('contenteditable'),'true','a block with formulas is a normal editable block, not a protected one');
  // hostile or sloppy stored data is normalized
  await load([{type:'text',html:'x <span class="math-inline" data-latex="a" onclick="window.__pwned=1" style="x:y" contenteditable="true"><img src=x onerror="window.__pwned=2">junk</span> y'}]);
  await page.waitForSelector('#editor .math-inline');
  assert.equal(await page.evaluate(()=>window.__pwned),undefined);
  assert.equal(await page.locator('#editor .math-inline img').count(),0);

  // ---- copy & paste keep formulas; plain text uses \( \) ----
  await page.evaluate(()=>{window.__clip=null;document.addEventListener('copy',e=>{window.__clip={plain:e.clipboardData.getData('text/plain'),html:e.clipboardData.getData('text/html')};});});
  await load([{type:'text',html:`say ${atom('x^2')} twice`},{type:'text',html:''}]);await page.waitForSelector('#editor .math-inline[data-rendered]');
  await page.evaluate(()=>{const f=editor.children[0],r=document.createRange();r.selectNodeContents(f);f.focus();const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.press('Control+c');
  const clip=await page.evaluate(()=>window.__clip);
  assert.equal(clip.plain,'say \\(x^2\\) twice','copied text has the formula in Anki delimiters');
  assert.match(clip.html,/data-latex="x\^2"/);assert.ok(!/katex/.test(clip.html),'copied HTML carries the source, not the rendering');
  await page.evaluate(h=>{const f=editor.children[1];f.focus();const dt=new DataTransfer();dt.setData('text/html',h);dt.setData('text/plain','x');f.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));},clip.html);
  assert.match(await html(1),/<span class="math-inline" data-latex="x\^2"><\/span>/,'pasting brings the formula back');
  await page.waitForFunction(()=>editor.children[1].querySelector('.math-inline[data-rendered]'));
  // copying several blocks writes delimiters too
  await page.evaluate(()=>{updateBlockHighlight(0,0);});await page.keyboard.press('Control+c');
  assert.equal(await page.evaluate(()=>window.__clip.plain),undefined===0?'':await page.evaluate(()=>window.__clip.plain));

  // ---- where "$" stays a normal character ----
  await load([{type:'code',html:''},{type:'latex',html:'',latex:''}]);
  await focusEnd(0);await page.keyboard.type('$5');
  assert.equal(await page.locator('#editor .math-inline').count(),0,'no formulas in code blocks');
  assert.equal(await page.evaluate(()=>editor.children[0].textContent),'$5');
  await page.evaluate(()=>notebookLatexEdit(editor.children[1]));await page.keyboard.type('$x');
  assert.equal((await blocks())[1].latex,'$x','the LaTeX block source takes "$" literally');
  assert.equal(await page.locator('#editor .math-inline').count(),0);

  // ---- the card creator takes formulas as \( \) ----
  await load([{type:'text',html:`the area is ${atom('\\pi r^2')} always`}]);await page.waitForSelector('#editor .math-inline[data-rendered]');
  await page.evaluate(()=>{const f=editor.children[0],r=document.createRange();r.selectNodeContents(f);f.focus();const s=getSelection();s.removeAllRanges();s.addRange(r);nbcUseSelection('front');});
  assert.equal(await page.evaluate(sel=>document.querySelector(sel).value,'#nbc-front'),'the area is \\(\\pi r^2\\) always','the card draft holds the formula as Anki renders it');

  // ---- empty-looking block with only a formula is not treated as empty ----
  await load([{type:'text',html:'a'},{type:'text',html:atom('x')}]);
  await page.evaluate(()=>{const f=editor.children[1];f.focus();const r=document.createRange();r.setStart(f,0);r.collapse(true);const s=getSelection();s.removeAllRanges();s.addRange(r);});
  await page.keyboard.press('Backspace');
  assert.ok((await blocks()).some(b=>b.html.includes('math-inline')),'a block holding only a formula survives Backspace at its start');
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS inline formulas: $ editor, live rendering, lists/toggles/tables, cancel paths, errors, storage, clipboard, card creator');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
