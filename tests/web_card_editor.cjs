// Rich Card Creator fields (PDF tab and notebook): the HTML they write must be plain Anki HTML.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9a8AAAAASUVORK5CYII=';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  for (const target of ['pdf','notebook']) {
   const page=await browser.newPage({viewport:{width:1100,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>{window.__cmds=[];window.pycmd=c=>window.__cmds.push(c);});
   const F=target==='pdf'?'#creator-front':'#nbc-front', B=target==='pdf'?'#creator-back':'#nbc-back';
   if (target==='pdf') {
     await page.goto('file://'+root+'/web_notebook/pdf_viewer.html');
     await page.evaluate(()=>{showViewer('a.pdf','/lib/a.pdf');currentPdfPath='/lib/a.pdf';currentPdfName='a.pdf';currentPdfId='p1';openCardCreator();});
   } else {
     await page.goto('file://'+root+'/web_notebook/index.html');
     await page.waitForFunction(()=>typeof window.nbcOpen==='function');
     await page.evaluate(()=>nbcOpen());
   }
   const val=sel=>page.evaluate(s=>document.querySelector(s).value,sel);
   const html=sel=>page.evaluate(s=>document.querySelector(s).innerHTML,sel);
   assert.equal(await page.evaluate(s=>document.querySelector(s).isContentEditable,F),true,target+': the field is a rich editor');
   assert.ok(await page.locator('.ce-toolbar').count()>=2,'each field has a toolbar');

   // headings and lists by typing, like in the notebook
   await page.click(F);
   await page.keyboard.type('## Heart');await page.keyboard.press('Enter');
   await page.keyboard.type('- valves');await page.keyboard.press('Enter');
   await page.keyboard.type('aortic');await page.keyboard.press('Tab');await page.keyboard.press('Enter');
   await page.keyboard.type('mitral');await page.keyboard.press('Shift+Tab');
   await page.keyboard.type(' (outer)');
   let v=await val(F);
   assert.match(v,/^<h2>Heart<\/h2>/,'"## " makes a heading: '+v);
   assert.match(v,/<ul><li>valves<ul><li>aortic<\/li>.*<\/ul>.*<\/li>/s,'Tab nests a list item: '+v);
   assert.equal(/ class=|style=/.test(v),false,'no classes or inline styles leak into the field');
   // numbered list starting at 3, nested ordered lists use a/i
   await page.click(B);
   await page.keyboard.type('3. three');await page.keyboard.press('Enter');
   await page.keyboard.type('four');await page.keyboard.press('Enter');await page.keyboard.type('x');await page.keyboard.press('Tab');
   v=await val(B);
   assert.match(v,/^<ol start="3"><li>three<\/li><li>four<ol type="a"><li>x<\/li><\/ol><\/li><\/ol>$/,'numbered list with start and nested letters: '+v);
   // Tab on the first item does not nest it; Tab outside lists leaves the field
   await page.click(F);await page.keyboard.press('Control+End');

   // formulas: $…$ becomes a rendered atom and is stored as \( … \)
   await page.evaluate(sel=>{document.querySelector(sel).value='';},F);
   await page.click(F);
   await page.keyboard.type('Area $x^2$ and more');
   await page.waitForSelector(F+' .ce-math .katex');
   assert.equal(await val(F),'Area \\(x^2\\) and more');
   await page.evaluate(sel=>{document.querySelector(sel).value='a $5 and $6 b';},F);
   await page.click(F);await page.keyboard.press('Control+End');await page.keyboard.type(' ok');
   assert.equal(await page.locator(F+' .ce-math').count(),0,'currency amounts are not formulas');
   await page.evaluate(sel=>{document.querySelector(sel).value='Set \\(a\\) and \\[\\frac{1}{2}\\]';},F);
   await page.waitForSelector(F+' .ce-math .katex');
   assert.equal(await page.locator(F+' .ce-math').count(),2,'stored formulas are shown rendered');
   assert.equal(await val(F),'Set \\(a\\) and \\[\\frac{1}{2}\\]');
   // a formula with "}}" cannot end a cloze early
   await page.evaluate(sel=>{document.querySelector(sel).value='\\(\\frac{a}{b}\\) x \\(f(g{h{i}})\\)';},F);
   assert.ok((await val(F)).includes('\\(f(g{h{i} })\\)'),'}} is written as } }');
   // clicking a formula opens its source for editing
   await page.click(F+' .ce-math >> nth=0');
   assert.match(await page.evaluate(sel=>document.querySelector(sel).textContent,F),/\$\\frac\{a\}\{b\}\$/);

   // toolbar: bold and heading buttons
   await page.evaluate(sel=>{document.querySelector(sel).value='plain';},F);
   await page.click(F);await page.keyboard.press('Control+a');
   await page.click(F.replace('#','#')+'  >> xpath=preceding-sibling::div[contains(@class,"ce-toolbar")]//button[@title="Bold (Ctrl+B)"]');
   assert.equal(await val(F),'<b>plain</b>');
   await page.click(F+' >> xpath=preceding-sibling::div[contains(@class,"ce-toolbar")]//button[text()="H1"]');
   assert.equal(await val(F),'<h1><b>plain</b></h1>');

   // paste: foreign HTML is cleaned, pictures land at the cursor
   await page.evaluate(sel=>{document.querySelector(sel).value='';},F);
   await page.click(F);
   const pasted=await page.evaluate(sel=>{const dt=new DataTransfer();dt.setData('text/html','<div style="color:red" onclick="e()"><h4>Hi</h4><script>window.__x=1</script><a href="http://e">link</a><p>a &lt; b</p></div>');dt.setData('text/plain','Hi link a < b');const e=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});document.querySelector(sel).dispatchEvent(e);return e.defaultPrevented;},F);
   assert.equal(pasted,true);
   assert.equal(await page.evaluate(()=>window.__x),undefined);
   v=await val(F);
   assert.equal(/script|style=|onclick|href/.test(v),false,'pasted HTML is cleaned: '+v);
   assert.match(v,/<h3>Hi<\/h3>/);
   await page.evaluate(sel=>{document.querySelector(sel).value='Look';},F);
   await page.click(F);await page.keyboard.press('Control+End');
   await page.evaluate(async([sel,uri])=>{const blob=await (await fetch(uri)).blob();const dt=new DataTransfer();dt.items.add(new File([blob],'s.png',{type:'image/png'}));document.querySelector(sel).dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));},[F,PNG]);
   await page.waitForFunction(sel=>document.querySelector(sel).value.includes('data-img'),F);
   await page.keyboard.type(' after');
   assert.match(await val(F),/^Look<img data-img="1"> after$/,'picture inline in the text: '+await val(F));
   assert.equal(await page.locator(F+' img').count(),1);

   // cloze deletion around a selection (also across markup)
   await page.evaluate(sel=>{document.querySelector(sel).value='The <b>aortic</b> valve';},F);
   await page.evaluate(sel=>{const f=document.querySelector(sel),r=document.createRange();r.setStart(f.firstChild,4);r.setEnd(f.querySelector('b').firstChild,6);const s=getSelection();s.removeAllRanges();s.addRange(r);},F);
   const result=await page.evaluate(([t])=>t==='pdf'?(insertCreatorCloze(false),'x'):(nbcInsertCloze(false),'x'),[target]);
   assert.equal(await val(F),'The {{c1::<b>aortic</b>}} valve');
   assert.equal(/\{\{c1::.*\}\}/.test(await val(F)),true);
   await page.evaluate(sel=>{const f=document.querySelector(sel),r=document.createRange();const t=f.lastChild,i=t.data.indexOf('valve');r.setStart(t,i);r.setEnd(t,i+5);const s=getSelection();s.removeAllRanges();s.addRange(r);},F);
   await page.evaluate(([t])=>t==='pdf'?insertCreatorCloze(false):nbcInsertCloze(false),[target]);
   assert.match(await val(F),/\{\{c2::valve\}\}$/,'a second cloze gets number 2');

   assert.deepEqual(errors,[],target+': no page errors');
   await page.close();
  }
  // old sessions (plain text with picture markers) are converted once
  const page=await browser.newPage();await page.addInitScript(()=>{window.pycmd=()=>{};});
  await page.goto('file://'+root+'/web_notebook/pdf_viewer.html');
  const migrated=await page.evaluate(()=>{const s=sanitizeCreatorSession({pdfPath:'/a',deckName:'D',images:{},cards:[{front:'a <b>\nline [[img:1]]',back:'x & y',type:'basic'}],draft:{front:'old',back:''}},'a.pdf','/a');return [s.cards[0],s.draft.front,s.rich];});
  assert.equal(migrated[0].front,'a &lt;b&gt;<br>line <img data-img="1">');
  assert.equal(migrated[0].back,'x &amp; y');
  assert.equal(migrated[2],true);
  const again=await page.evaluate(()=>{const s=sanitizeCreatorSession({pdfPath:'/a',deckName:'D',rich:true,cards:[{front:'<h1>t</h1>',back:'',type:'basic'}]},'a.pdf','/a');return s.cards[0].front;});
  assert.equal(again,'<h1>t</h1>','new sessions are not converted again');
  console.log('PASS card editor: headings, nested/numbered lists, formulas, paste cleaning, pictures, cloze, migration');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
