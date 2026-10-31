// Synthetic notes only. No live Anki profile or collection is accessed.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const doc=blocks=>({pages:[{id:'A',title:'T',icon:'',blocks}],activePageId:'A'});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>window.pycmd=()=>{});
  const load=async blocks=>page.evaluate(data=>loadContent(JSON.stringify(data)),doc(blocks));
  const blocks=()=>page.evaluate(()=>JSON.parse(__synapseSnapshotForUnload()).pages[0].blocks);
  const indents=async()=>(await blocks()).map(b=>b.indent||0);
  const focusBlock=async(i,atStart=true)=>page.evaluate(([i,atStart])=>{
   const el=editor.children[i],f=el.querySelector('.todo-content')||el;f.focus();
   const r=document.createRange();r.selectNodeContents(f);r.collapse(atStart);const s=getSelection();s.removeAllRanges();s.addRange(r);
  },[i,atStart]);
  await page.goto('file://'+root+'/web_notebook/index.html');

  // ---- Feature 1: nested lists ----
  const list=[{type:'bullet',html:'A'},{type:'numbered',html:'B'},{type:'todo',html:'C',checked:false},{type:'bullet',html:'D'},{type:'text',html:'plain'}];
  await load(list);
  assert.deepEqual(await blocks(),list,'legacy blocks without indent round-trip untouched');
  await focusBlock(0);await page.keyboard.press('Tab');
  assert.deepEqual(await indents(),[0,0,0,0,0],'first list item cannot be indented');
  await focusBlock(1);await page.keyboard.press('Tab');
  assert.deepEqual(await indents(),[0,1,0,0,0],'second item becomes child of the first');
  await focusBlock(1);await page.keyboard.press('Tab');
  assert.deepEqual(await indents(),[0,1,0,0,0],'cannot nest deeper than previous item + 1');
  await focusBlock(2);await page.keyboard.press('Tab');await page.keyboard.press('Tab');
  assert.deepEqual(await indents(),[0,1,2,0,0],'mixed bullet/numbered/todo nesting');
  assert.equal((await blocks())[2].type,'todo');assert.equal((await blocks())[2].checked,false);
  // Shift+Tab moves a block with its children outwards
  await focusBlock(1);await page.keyboard.press('Shift+Tab');
  assert.deepEqual(await indents(),[0,0,1,0,0],'outdent keeps children relative to parent');
  // Undo / redo
  await page.keyboard.press('Control+z');assert.deepEqual(await indents(),[0,1,2,0,0],'undo restores indent');
  await page.keyboard.press('Control+y');assert.deepEqual(await indents(),[0,0,1,0,0],'redo re-applies indent');
  // Save / load round trip
  const saved=await blocks();await load(saved);assert.deepEqual(await blocks(),saved,'indent survives reload');
  // Outdenting to 0 drops the field instead of storing indent:0
  await focusBlock(2);await page.keyboard.press('Shift+Tab');
  assert.ok(!('indent' in (await blocks())[2]),'indent 0 is not stored');
  // Tab outside lists keeps native behaviour (no indent, no preventDefault)
  await focusBlock(4);
  await page.keyboard.press('Tab');
  assert.deepEqual(await indents(),[0,0,0,0,0],'text block ignores Tab');
  assert.equal(await page.evaluate(()=>document.activeElement===editor.children[4]),false,'Tab outside a list still moves focus');
  // Enter on empty indented item outdents; Backspace at start outdents
  await load([{type:'bullet',html:'A'},{type:'bullet',html:'',indent:1}]);
  await focusBlock(1);await page.keyboard.press('Enter');
  assert.deepEqual(await indents(),[0,0],'Enter on empty nested item outdents');
  assert.equal((await blocks())[1].type,'bullet');
  await load([{type:'bullet',html:'A'},{type:'numbered',html:'text',indent:1}]);
  await focusBlock(1);await page.keyboard.press('Backspace');
  assert.deepEqual(await indents(),[0,0],'Backspace at start of nested item outdents');
  assert.equal((await blocks()).length,2);
  // Enter inherits indent and type
  await load([{type:'bullet',html:'A'},{type:'numbered',html:'x',indent:1}]);
  await focusBlock(1,false);await page.keyboard.press('Enter');
  const split=await blocks();assert.equal(split[2].type,'numbered');assert.equal(split[2].indent,1);
  // Multi-selection
  await load([{type:'bullet',html:'A'},{type:'bullet',html:'B'},{type:'bullet',html:'C'}]);
  await page.evaluate(()=>updateBlockHighlight(1,2));
  await page.keyboard.press('Tab');
  assert.deepEqual(await indents(),[0,1,1],'Tab indents all selected list items');
  // Numbering per level (CSS counters)
  await load([{type:'numbered',html:'1'},{type:'numbered',html:'1a',indent:1},{type:'numbered',html:'1b',indent:1},{type:'numbered',html:'2'},{type:'numbered',html:'2a',indent:1},{type:'text',html:'break'},{type:'numbered',html:'again'}]);
  const labels=await page.evaluate(()=>Array.from(editor.children).map(el=>el.classList.contains('numbered')?getComputedStyle(el,'::before').content:null));
  assert.deepEqual(labels,['counter(nb0) "."','counter(nb1, lower-alpha) "."','counter(nb1, lower-alpha) "."','counter(nb0) "."','counter(nb1, lower-alpha) "."',null,'counter(nb0) "."'],'each level uses its own counter style');
  if(process.env.SYNAPSE_TEST_SHOT)await page.screenshot({path:process.env.SYNAPSE_TEST_SHOT});
  // Markdown trigger and slash entry
  await load([{type:'text',html:''}]);
  await focusBlock(0);await page.keyboard.type('1. x');
  assert.equal((await blocks())[0].type,'numbered');
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS nested lists: Tab/Shift+Tab, limits, undo, round trip, numbering');

  // ---- Feature 2: LaTeX block ----
  await load([{type:'text',html:''}]);
  await focusBlock(0);await page.keyboard.type('/form');
  assert.equal(await page.evaluate(()=>Array.from(document.querySelectorAll('#slash-menu .menu-item')).filter(i=>i.style.display!=='none').map(i=>i.querySelector('.menu-shortcut').textContent).join(',')),'/latex','/form alias filters the slash menu to /latex');
  for(const alias of ['/latex','/math','/formel']){
   await load([{type:'text',html:''}]);await focusBlock(0);await page.keyboard.type(alias);
   assert.deepEqual((await blocks()).map(b=>b.type),['latex','text'],alias+' inserts a LaTeX block followed by a text block');
  }
  assert.equal(await page.evaluate(()=>document.activeElement.className),'latex-source','new LaTeX block starts in edit mode');
  await page.keyboard.type('\\frac{a}{b}+x^2');
  assert.deepEqual((await blocks())[0],{type:'latex',html:'',latex:'\\frac{a}{b}+x^2'});
  await page.waitForSelector('.latex-preview .katex');
  assert.equal(await page.locator('.latex-error').first().isHidden(),true,'valid formula shows no error');
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(()=>editor.children[0].classList.contains('editing')),false,'Escape leaves edit mode');
  assert.equal(await page.evaluate(()=>document.activeElement.className),'latex-preview');
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(()=>editor.children[0].classList.contains('editing')),true,'Enter re-opens editing');
  await page.keyboard.press('Control+Enter');
  assert.equal(await page.evaluate(()=>editor.children[0].classList.contains('editing')),false,'Ctrl+Enter leaves edit mode');
  await page.click('.latex-preview');
  assert.equal(await page.evaluate(()=>editor.children[0].classList.contains('editing')),true,'click on the formula edits it');
  // invalid LaTeX: error is shown, nothing crashes, source is still saved
  await page.keyboard.type('\\frac{');
  await page.waitForSelector('.latex-error:not([hidden])');
  assert.match(await page.locator('.latex-error').first().textContent(),/Invalid LaTeX/);
  assert.equal((await blocks())[0].latex,'\\frac{a}{b}+x^2\\frac{');
  // round trip incl. unknown fields; KaTeX renders after reload
  const latexDoc=[{type:'latex',html:'',latex:'E=mc^2',custom:{keep:true}},{type:'text',html:'after'}];
  await load(latexDoc);
  assert.deepEqual(await blocks(),latexDoc,'LaTeX block with extra fields round-trips untouched');
  await page.waitForSelector('.latex-preview .katex');
  assert.equal(await page.evaluate(()=>editor.children[0].classList.contains('editing')),false,'filled formula loads in display mode');
  // cannot be converted away silently while it holds a formula
  assert.equal(await page.evaluate(()=>convertedBlock(editor.children[0],'text')),null);
  // deleting an emptied block with Backspace can be undone
  await load([{type:'text',html:'t'},{type:'latex',html:'',latex:'x'}]);
  await page.evaluate(()=>notebookLatexEdit(editor.children[1]));
  await page.keyboard.press('Backspace');await page.keyboard.press('Backspace');
  assert.deepEqual((await blocks()).map(b=>b.type),['text']);
  await page.keyboard.press('Control+z');
  assert.deepEqual((await blocks()).map(b=>b.type),['text','latex'],'undo restores deleted LaTeX block');
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS LaTeX block: slash aliases, live preview, errors, modes, persistence, undo');

  // ---- Feature 3: colors on single words ----
  const RED='rgb(197, 61, 61)',YELLOW_BG='rgb(255, 240, 179)';
  const html=async i=>(await blocks())[i].html;
  const selectWord=(i,word)=>page.evaluate(([i,word])=>{
   const host=editor.children[i].querySelector('.todo-content')||editor.children[i];
   const at=host.textContent.indexOf(word);if(at<0)return false;
   const locate=pos=>{const walker=document.createTreeWalker(host,NodeFilter.SHOW_TEXT);let n,seen=0;while(n=walker.nextNode()){if(pos<=seen+n.length)return [n,pos-seen];seen+=n.length;}};
   const [sn,so]=locate(at),[en,eo]=locate(at+word.length),r=document.createRange();
   r.setStart(sn,so);r.setEnd(en,eo);host.focus();const sel=getSelection();sel.removeAllRanges();sel.addRange(r);return true;
  },[i,word]);
  const swatch=async(row,idx)=>{
   await page.waitForFunction(()=>getComputedStyle(document.getElementById('format-toolbar')).display==='flex');
   await page.click('#format-toolbar button[data-i18n-title="color"]');
   await page.locator('#notebook-popup .nb-swatches').nth(row).locator('button').nth(idx).click();
  };
  const colorDoc=[{type:'text',html:'hello brave world'},{type:'bullet',html:'second <b>bold</b> <a href="https://example.org">link</a>'},{type:'todo',html:'third task',checked:false}];
  await load(colorDoc);
  assert.ok(await selectWord(0,'brave'));await swatch(0,9);
  assert.equal(await html(0),`hello <span style="color: ${RED};">brave</span> world`,'only the selected word is colored');
  assert.deepEqual((await blocks()).slice(1),colorDoc.slice(1),'other blocks untouched');
  // background on another word; recoloring the same word never nests
  assert.ok(await selectWord(0,'hello'));await swatch(1,4);
  assert.ok(await selectWord(0,'brave'));await swatch(0,6);
  assert.match(await html(0),/^<span style="background-color: rgb\(255, 240, 179\);">hello<\/span> <span style="color: rgb\(11, 110, 153\);">brave<\/span> world$/);
  // partial reselection of a colored word splits it
  assert.ok(await selectWord(0,'av'));await swatch(0,9);
  assert.match(await html(0),/br<span style="color: rgb\(197, 61, 61\);">av<\/span>e/,'only the selected letters change color');
  // Default removes the color completely (no leftover spans)
  assert.ok(await selectWord(0,'brave'));await swatch(0,0);
  assert.equal((await html(0)).replace(/<span style="background-color[^>]*>hello<\/span>/,'hello'),'hello brave world','default color leaves plain text');
  assert.ok(await selectWord(0,'hello'));await swatch(1,0);
  assert.equal(await html(0),'hello brave world','background reset leaves plain text too');
  // combines with bold and links
  assert.ok(await selectWord(1,'bold'));await swatch(0,3);
  assert.ok(await selectWord(1,'link'));await swatch(1,6);
  const combined=await html(1);
  assert.match(combined,/<b>(<span[^>]*>)?bold(<\/span>)?<\/b>|<span[^>]*><b>bold<\/b><\/span>/);
  assert.match(combined,/href="https:\/\/example\.org"/);
  assert.ok(combined.includes('color: rgb(217, 115, 13)')&&combined.includes('background-color: rgb(217, 234, 250)'),combined);
  // todo block (separate wrapper element) and undo / redo
  assert.ok(await selectWord(2,'task'));await swatch(0,2);
  assert.match(await html(2),/third <span style="color: rgb\(159, 107, 83\);">task<\/span>/);
  assert.equal((await blocks())[2].checked,false);
  await page.keyboard.press('Control+z');assert.equal(await html(2),'third task','undo removes the color');
  await page.keyboard.press('Control+y');assert.match(await html(2),/<span style="color/,'redo restores it');
  // selection across block boundaries
  await load([{type:'text',html:'alpha beta'},{type:'bullet',html:'gamma delta'}]);
  await page.evaluate(()=>{
   const a=editor.children[0].firstChild,b=editor.children[1].firstChild,r=document.createRange();
   r.setStart(a,6);r.setEnd(b,5);editor.children[0].focus();const s=getSelection();s.removeAllRanges();s.addRange(r);
  });
  await swatch(0,9);
  assert.deepEqual((await blocks()).map(b=>b.html),[`alpha <span style="color: ${RED};">beta</span>`,`<span style="color: ${RED};">gamma</span> delta`],'a selection across blocks colors each block');
  // persistence + legacy colored data
  const persisted=await blocks();await load(persisted);assert.deepEqual(await blocks(),persisted,'colors survive save and reload');
  const legacy=[{type:'text',html:'<span style="color: rgb(11, 110, 153);">legacy</span> <font color="#c53d3d">font</font>'}];
  await load(legacy);assert.deepEqual(await blocks(),legacy,'previously stored colors load unchanged');
  // block color from the gutter still colors the whole block
  await load([{type:'text',html:'whole block'}]);
  await page.evaluate(()=>{notebookActionBlock=editor.children[0];openNotebookColors(notebookPopup,true);});
  await page.locator('#notebook-popup .nb-swatches').nth(0).locator('button').nth(9).click();
  assert.equal(await html(0),`<span style="color: ${RED};">whole block</span>`,'gutter block color still works');
  // dark mode shows readable variants of the stored colors without changing the data
  await load([{type:'text',html:`<span style="background-color: ${YELLOW_BG};">hi</span> <span style="color: ${RED};">there</span>`}]);
  await page.evaluate(()=>document.body.classList.add('nightMode'));
  assert.deepEqual(await page.evaluate(()=>Array.from(editor.querySelectorAll('span')).map(el=>[getComputedStyle(el).color,getComputedStyle(el).backgroundColor])),[['rgb(255, 255, 255)','rgb(89, 86, 59)'],['rgb(255, 115, 105)','rgba(0, 0, 0, 0)']]);
  assert.equal(await html(0),`<span style="background-color: ${YELLOW_BG};">hi</span> <span style="color: ${RED};">there</span>`,'stored colors stay theme independent');
  await page.evaluate(()=>document.body.classList.remove('nightMode'));
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS word colors: partial/multi-block selection, reset, combinations, undo/redo, persistence, dark mode');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
