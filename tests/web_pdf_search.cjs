// Search inside a PDF, with the real bundled PDF.js and a small PDF built here (synthetic text only).
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const root=path.resolve(__dirname,'..');
// A valid multi-page PDF with Helvetica text; every page is an array of text lines.
function buildPdf(pages){
 const objects=[];const add=body=>{objects.push(body);return objects.length;};
 const catalog=add('');const pagesObj=add('');const font=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');
 const kids=[];
 pages.forEach(lines=>{
  const content=lines.map((l,i)=>`BT /F1 16 Tf 50 ${740-i*24} Td (${l.replace(/[()\\]/g,'\\$&')}) Tj ET`).join('\n');
  const c=add(`<< /Length ${Buffer.byteLength(content,'latin1')} >>\nstream\n${content}\nendstream`);
  kids.push(add(`<< /Type /Page /Parent ${pagesObj} 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 ${font} 0 R >> >> /Contents ${c} 0 R >>`));
 });
 objects[catalog-1]=`<< /Type /Catalog /Pages ${pagesObj} 0 R >>`;
 objects[pagesObj-1]=`<< /Type /Pages /Kids [${kids.map(k=>k+' 0 R').join(' ')}] /Count ${kids.length} >>`;
 let out='%PDF-1.4\n';const offsets=[];
 objects.forEach((o,i)=>{offsets.push(Buffer.byteLength(out,'latin1'));out+=`${i+1} 0 obj\n${o}\nendobj\n`;});
 const xref=Buffer.byteLength(out,'latin1');
 out+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`+offsets.map(o=>String(o).padStart(10,'0')+' 00000 n \n').join('')+`trailer\n<< /Size ${objects.length+1} /Root ${catalog} 0 R >>\nstartxref\n${xref}\n%%EOF`;
 return Buffer.from(out,'latin1');
}
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const pdf=buildPdf([
   ['Anatomy of the heart','The aortic valve has three cusps.','Mitral valve: two cusps'],
   ['Chapter two','Cafe au lait and Café are equal for the search.','The VALVE again, and the valves'],
   ['Last page','nothing relevant here'],
   Array.from({length:30},(_,i)=>'filler line number '+i).concat(['hidden needle valve at the bottom']),
  ]);
  const page=await browser.newPage({viewport:{width:900,height:800}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const lib=fs.readFileSync(path.join(root,'web_notebook/pdfjs/pdf.min.js'),'utf8'),worker=fs.readFileSync(path.join(root,'web_notebook/pdfjs/pdf.worker.min.js'),'utf8');
  await page.addInitScript(({lib,worker})=>{window.__cmds=[];window.pycmd=c=>window.__cmds.push(c);window.__PDFJS_WORKER_CODE__=worker;(0,eval)(lib);},{lib,worker});
  await page.goto('file://'+root+'/web_notebook/pdf_viewer.html');
  assert.equal(await page.evaluate(()=>typeof pdfjsLib),'object','the bundled PDF.js loads');
  await page.evaluate(b64=>renderPdfB64(b64,'heart.pdf','/lib/heart.pdf',1),pdf.toString('base64'));
  await page.waitForSelector('.page-wrapper[data-rendered="1"] .textLayer span');
  const count=()=>page.locator('#find-count').textContent();
  const marks=()=>page.evaluate(()=>Array.from(document.querySelectorAll('mark.pdf-hit')).map(m=>[m.textContent,m.classList.contains('active')]));
  assert.equal(await page.locator('#find-bar').isHidden(),true,'the search bar is hidden until asked for');

  // Ctrl+F opens it; typing searches the whole document, ignoring case
  await page.keyboard.press('Control+f');
  assert.equal(await page.locator('#find-bar').isVisible(),true);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'find-input','the search box is focused');
  await page.keyboard.type('valve');
  await page.waitForFunction(()=>/ \/ /.test(document.getElementById('find-count').textContent));
  assert.equal(await count(),'1 / 5','valve occurs 5 times: 2 on page 1, 2 on page 2 (VALVE, valves), 1 on page 4');
  await page.waitForFunction(()=>document.querySelectorAll('mark.pdf-hit').length>0);
  let m=await marks();assert.ok(m.length>=2,'hits on the rendered page are marked');
  assert.equal(m.filter(x=>x[1]).length,1,'exactly one hit is the active one');
  assert.equal(m.find(x=>x[1])[0].toLowerCase(),'valve');
  // Enter / Shift+Enter step through all hits and wrap around
  await page.keyboard.press('Enter');assert.equal(await count(),'2 / 5');
  await page.keyboard.press('Enter');await page.keyboard.press('Enter');await page.keyboard.press('Enter');assert.equal(await count(),'5 / 5');
  await page.waitForFunction(()=>document.querySelector('mark.pdf-hit.active'),null,{timeout:8000});
  const activeBox=await page.evaluate(()=>{const r=document.querySelector('mark.pdf-hit.active').getBoundingClientRect(),w=document.getElementById('canvas-wrap').getBoundingClientRect();return r.top>=w.top-2&&r.bottom<=w.bottom+2;});
  assert.equal(activeBox,true,'the active hit on a distant page is scrolled into view');
  await page.keyboard.press('Enter');assert.equal(await count(),'1 / 5','it wraps to the first hit');
  await page.keyboard.press('Shift+Enter');assert.equal(await count(),'5 / 5','Shift+Enter goes back');
  // accents and case are ignored, in both directions
  await page.fill('#find-input','CAFÉ');await page.waitForFunction(()=>document.getElementById('find-count').textContent==='1 / 2');
  await page.fill('#find-input','cafe au');await page.waitForFunction(()=>document.getElementById('find-count').textContent==='1 / 1');
  // several words, extra spaces
  await page.fill('#find-input','aortic   valve has');await page.waitForFunction(()=>document.getElementById('find-count').textContent==='1 / 1');
  // no result
  await page.fill('#find-input','zebra crossing');await page.waitForFunction(()=>document.getElementById('find-count').textContent==='No results');
  assert.equal((await marks()).length,0,'no marks without hits');assert.match(await page.locator('#find-count').getAttribute('class'),/none/);
  // regex characters are plain text
  await page.fill('#find-input','.*');await page.waitForFunction(()=>document.getElementById('find-count').textContent==='No results');
  // marks never change the text itself
  await page.fill('#find-input','valve');await page.waitForFunction(()=>document.getElementById('find-count').textContent.endsWith('/ 5'));
  const texts=await page.evaluate(()=>Array.from(document.querySelectorAll('.textLayer')).map(l=>l.textContent));
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#find-bar').isHidden(),true,'Esc closes the bar');
  assert.equal((await marks()).length,0,'and clears every highlight');
  const after=await page.evaluate(()=>Array.from(document.querySelectorAll('.textLayer')).map(l=>l.textContent));
  assert.deepEqual(after.slice(0,texts.length),texts.slice(0,after.length),'the text layer text is untouched by marking');
  // a selected word is taken over when the bar opens; reopening keeps working after a zoom (re-render)
  await page.evaluate(()=>{const span=Array.from(document.querySelectorAll('.textLayer span')).find(s=>s.textContent.includes('Mitral'));const r=document.createRange();r.setStart(span.firstChild,0);r.setEnd(span.firstChild,6);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);});
  await page.evaluate(()=>toggleFind());
  assert.equal(await page.inputValue('#find-input'),'Mitral','the selected text becomes the query');
  await page.waitForFunction(()=>document.getElementById('find-count').textContent==='1 / 1');
  await page.evaluate(()=>changeZoom(0.4));
  await page.waitForFunction(()=>document.querySelector('mark.pdf-hit'),null,{timeout:8000});
  assert.equal((await marks())[0][0],'Mitral','hits are marked again after the pages re-render');
  // a new document starts clean
  await page.evaluate(b64=>renderPdfB64(b64,'other.pdf','/lib/other.pdf',1),buildPdf([['Another valve here']]).toString('base64'));
  await page.waitForSelector('.page-wrapper[data-rendered="1"] .textLayer span');
  assert.equal(await page.locator('#find-bar').isHidden(),true);assert.equal(await page.evaluate(()=>pdfFind.hits.length),0);
  await page.keyboard.press('Control+f');await page.keyboard.type('valve');
  await page.waitForFunction(()=>document.getElementById('find-count').textContent==='1 / 1');
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS PDF search: Ctrl+F, whole document, case/accent-insensitive, stepping, scrolling, highlights, re-render, new document');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
