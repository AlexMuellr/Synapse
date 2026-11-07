// The PDF tab's Card Creator: pictures pasted into Front/Back (PDF.js itself is not needed for this).
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1100,height:800}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.__cmds=[];window.pycmd=c=>window.__cmds.push(c);});
  await page.goto('file://'+root+'/web_notebook/pdf_viewer.html');
  assert.equal(await page.evaluate(()=>typeof window.SynapseCardImages),'object','the picture module is available');
  await page.evaluate(()=>{showViewer('a.pdf','/lib/a.pdf');currentPdfPath='/lib/a.pdf';currentPdfName='a.pdf';currentPdfId='p1';openCardCreator();});
  const png=(w,h,c='#2a6')=>page.evaluate(([w,h,c])=>{const k=document.createElement('canvas');k.width=w;k.height=h;const x=k.getContext('2d');x.fillStyle=c;x.fillRect(0,0,w,h);return k.toDataURL('image/png');},[w,h,c]);
  const paste=(sel,uri)=>page.evaluate(async([sel,uri])=>{const blob=await (await fetch(uri)).blob();const dt=new DataTransfer();dt.items.add(new File([blob],'s.png',{type:'image/png'}));const e=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});document.querySelector(sel).dispatchEvent(e);return e.defaultPrevented;},[sel,uri]);
  await page.fill('#creator-front','What is shown?');await page.focus('#creator-front');await page.keyboard.press('End');
  assert.equal(await paste('#creator-front',await png(80,50)),true);
  await page.waitForFunction(()=>document.getElementById('creator-front').value.includes('[[img:1]]'));
  await page.keyboard.type('Explain it.');
  assert.equal(await page.inputValue('#creator-front'),'What is shown?\n[[img:1]]\nExplain it.','text above and below the picture');
  assert.equal(await page.locator('#creator-front-images .creator-image').count(),1);
  await page.fill('#creator-back','Answer');await page.focus('#creator-back');await page.keyboard.press('End');
  await paste('#creator-back',await png(40,40,'#c33'));await page.waitForFunction(()=>document.getElementById('creator-back').value.includes('[[img:2]]'));
  await page.fill('#creator-deck-name','Anatomy');
  await page.evaluate(()=>document.getElementById('creator-deck-name').dispatchEvent(new Event('input',{bubbles:true})));
  await page.click('#creator-add-card');
  assert.match(await page.locator('.creator-card-front').first().textContent(),/What is shown\?\s*🖼\s*Explain it\./,'the list shows a picture glyph');
  // a card without its picture would be an error in Python, so the page only sends what is used
  await page.evaluate(()=>{window.__cmds=[];});
  await page.click('#creator-finish');
  const sent=await page.evaluate(()=>window.__cmds.filter(c=>c.startsWith('notion_mini:pdf:create-batch:')).map(c=>JSON.parse(c.slice('notion_mini:pdf:create-batch:'.length))));
  assert.equal(sent.length,1);
  assert.deepEqual(Object.keys(sent[0].images).sort(),['1','2']);
  assert.match(sent[0].images['1'],/^data:image\/png;base64,/);
  assert.equal(sent[0].cards[0].front,'What is shown?\n[[img:1]]\nExplain it.');
  assert.equal(sent[0].cards[0].back,'Answer\n[[img:2]]');
  // the saved library (which holds the session) keeps the pictures only while a card uses them
  await page.evaluate(()=>save());
  const saved=await page.evaluate(()=>window.__cmds.filter(c=>c.startsWith('notion_mini:pdf:save:')).map(c=>JSON.parse(c.slice('notion_mini:pdf:save:'.length))).at(-1));
  assert.deepEqual(Object.keys(saved.cardCreatorSession.images).sort(),['1','2']);
  await page.evaluate(()=>window.onPdfCardBatchCreated(false,'not now'));   // the batch ends; the page is usable again
  // a session loaded from disk is cleaned
  await page.evaluate(()=>{state.cardCreatorSession={pdfPath:'/lib/a.pdf',deckName:'D',cards:[],draft:{type:'basic',front:'x [[img:7]]'},images:{7:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9a8AAAAASUVORK5CYII=',8:'data:text/html;base64,PGI+',z:'x'}};state.cardCreatorSession=sanitizeCreatorSession(state.cardCreatorSession,'a.pdf','/lib/a.pdf');});
  assert.deepEqual(await page.evaluate(()=>Object.keys(state.cardCreatorSession.images)),['7']);
  // limit of pictures per session
  await page.evaluate(()=>{state.cardCreatorSession=sanitizeCreatorSession({},'a.pdf','/lib/a.pdf');renderCardCreator();});
  await page.fill('#creator-front','');await page.focus('#creator-front');
  const tiny=await png(8,8);
  for(let i=0;i<13;i++){await paste('#creator-front',tiny);await page.waitForTimeout(40);}
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(()=>Object.keys(state.cardCreatorSession.images).length),12,'at most 12 pictures per session');
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS PDF card pictures: paste at the cursor, glyph in the list, payload, cleaning, limit');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
