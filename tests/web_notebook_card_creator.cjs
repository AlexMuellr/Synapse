// Synthetic notes only. The Python bridge is replaced by a recorder.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const doc=(blocks,extra={})=>({pages:[{id:'A',title:'Anatomy',icon:'',blocks}],activePageId:'A',...extra});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1280,height:800}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>d.accept());
  await page.addInitScript(()=>{window.__cmds=[];window.pycmd=c=>window.__cmds.push(c);window.__SYNAPSE_DECKS__=['Anatomy','Anatomy::Heart','Biology','Ärzte'];window.__SYNAPSE_CURRENT_DECK__='Biology';});
  await page.goto('file://'+root+'/web_notebook/index.html');
  const load=d=>page.evaluate(d=>loadContent(JSON.stringify(d)),d);
  const snap=()=>page.evaluate(()=>JSON.parse(__synapseSnapshotForUnload()));
  const batches=()=>page.evaluate(()=>window.__cmds.filter(c=>c.includes(':notes:create-batch:')).map(c=>JSON.parse(c.slice(c.indexOf(':notes:create-batch:')+':notes:create-batch:'.length))));
  await load(doc([{type:'text',html:'The aortic valve has three cusps'},{type:'text',html:'Second paragraph'}]));

  // panel opens beside the editor and does not cover it
  assert.equal(await page.locator('#nb-card-creator').isVisible(),false);
  await page.click('#nb-creator-launch');
  assert.equal(await page.locator('#nb-card-creator').isVisible(),true);
  const editorBox=await page.locator('#editor').boundingBox(),panelBox=await page.locator('#nb-card-creator').boundingBox();
  assert.ok(editorBox.x+editorBox.width<=panelBox.x+1,'editor is pushed aside, not covered');
  assert.equal(await page.locator('#nbc-finish').isDisabled(),true,'nothing to create yet');

  // manual card
  await page.fill('#nbc-front','What has three cusps?');await page.fill('#nbc-back','The aortic valve');
  await page.click('#nbc-add-card');
  assert.equal(await page.locator('.creator-card-item').count(),1);
  assert.equal(await page.locator('#nbc-count-badge').textContent(),'1');
  assert.equal(await page.inputValue('#nbc-front'),'','form is cleared after adding');
  // duplicate and missing front are refused
  await page.fill('#nbc-front','What has three cusps?');await page.fill('#nbc-back','The aortic valve');await page.click('#nbc-add-card');
  assert.equal(await page.locator('.creator-card-item').count(),1,'duplicate refused');
  await page.fill('#nbc-front','');await page.fill('#nbc-back','orphan');await page.click('#nbc-add-card');
  assert.match(await page.locator('#nbc-status').textContent(),/front/i);
  await page.fill('#nbc-back','');

  // selected text goes to the chosen side and appends
  const select=word=>page.evaluate(word=>{
   const host=Array.from(editor.children).find(e=>e.textContent.includes(word));host.focus();
   const walker=document.createTreeWalker(host,NodeFilter.SHOW_TEXT);const n=walker.nextNode();const at=n.textContent.indexOf(word);
   const r=document.createRange();r.setStart(n,at);r.setEnd(n,at+word.length);const s=getSelection();s.removeAllRanges();s.addRange(r);
  },word);
  const toolbarShown=()=>page.waitForFunction(()=>getComputedStyle(document.getElementById('format-toolbar')).display==='flex');
  await select('aortic valve');await toolbarShown();
  await page.click('#format-toolbar button[data-i18n="card_front"]');
  assert.equal(await page.inputValue('#nbc-front'),'aortic valve');
  await page.waitForTimeout(60);await select('Second');await toolbarShown();await page.click('#format-toolbar button[data-i18n="card_back"]');
  assert.equal(await page.inputValue('#nbc-back'),'Second');
  await page.waitForTimeout(60);await select('three cusps');await toolbarShown();await page.click('#format-toolbar button[data-i18n="card_back"]');
  assert.equal(await page.inputValue('#nbc-back'),'Second\nthree cusps','a second selection is appended');
  assert.equal((await snap()).pages[0].blocks[0].html,'The aortic valve has three cusps','notes themselves are untouched');

  // cloze
  await page.selectOption('#nbc-type','cloze');
  assert.equal(await page.locator('#nbc-cloze-tools').isVisible(),true);
  await page.fill('#nbc-front','The heart has four chambers');
  await page.evaluate(()=>{const el=document.getElementById('nbc-front');el.focus();el.setSelectionRange(14,18);});
  await page.click('#nbc-cloze-tools button >> nth=0');
  assert.equal(await page.inputValue('#nbc-front'),'The heart has {{c1::four}} chambers');
  await page.fill('#nbc-back','');
  await page.click('#nbc-add-card');
  assert.equal(await page.locator('.creator-card-item').count(),2);
  await page.selectOption('#nbc-type','cloze');await page.fill('#nbc-front','no cloze here');await page.click('#nbc-add-card');
  assert.match(await page.locator('#nbc-status').textContent(),/cloze/i,'cloze without deletion refused');
  await page.fill('#nbc-front','');

  // edit and remove
  await page.click('.creator-card-item >> nth=0 >> .creator-item-btn >> nth=0');
  assert.equal(await page.inputValue('#nbc-front'),'What has three cusps?');
  await page.fill('#nbc-back','The aortic valve (3 cusps)');await page.click('#nbc-add-card');
  assert.equal(await page.locator('.creator-card-item').count(),2,'update replaces instead of adding');
  assert.match(await page.locator('.creator-card-back >> nth=0').textContent(),/3 cusps/);

  // deck combobox: current deck first, filter, accent-insensitive, free text
  await page.click('#nbc-deck-name');
  assert.equal(await page.locator('#nbc-deck-menu .deck-menu-option >> nth=0').textContent(),'Biology');
  await page.fill('#nbc-deck-name','arz');
  assert.equal(await page.locator('#nbc-deck-menu .deck-menu-option').count(),1,'accent-insensitive search');
  await page.fill('#nbc-deck-name','Anatomy::Heart');await page.locator('#nbc-deck-menu .deck-menu-option').first().click();
  assert.equal(await page.inputValue('#nbc-deck-name'),'Anatomy::Heart');

  // the session is saved with the notebook but is not part of undo history
  const saved=(await snap()).cardCreatorSession;
  assert.equal(saved.cards.length,2);assert.equal(saved.deckName,'Anatomy::Heart');
  await page.evaluate(()=>{const el=editor.children[1];el.focus();el.textContent='Second paragraph edited';el.dispatchEvent(new Event('input',{bubbles:true}));pushUndoSnapshot();});
  await page.evaluate(()=>navigateNotebook(-1));
  assert.equal((await snap()).cardCreatorSession.cards.length,2,'undo never touches the card session');

  // finish: one payload with everything, source label per card
  await page.check('#nbc-include-source');await page.fill('#nbc-front','Draft front');await page.fill('#nbc-back','Draft back');
  await page.click('#nbc-finish');
  const [batch]=await batches();
  assert.equal(batch.deckName,'Anatomy::Heart');
  assert.equal(batch.cards.length,3,'the open draft is included');
  assert.deepEqual(batch.cards.map(c=>c.type),['basic','cloze','basic']);
  assert.deepEqual(batch.cards[2],{front:'Draft front',back:'Draft back',type:'basic',includeSource:true,source:'Anatomy',pages:[]});
  assert.equal(await page.locator('#nbc-finish').isDisabled(),true,'no double submit while pending');
  // failure keeps the session, success clears it
  await page.evaluate(()=>onNotebookCardBatchCreated(false,'Could not create the cards.'));
  assert.equal(await page.locator('.creator-card-item').count(),3);
  assert.match(await page.locator('#nbc-status').textContent(),/Could not create/);
  await page.click('#nbc-finish');
  await page.evaluate(()=>onNotebookCardBatchCreated(true,'Created 4 cards in “Anatomy::Heart”.'));
  assert.equal(await page.locator('.creator-card-item').count(),0);
  assert.equal(await page.locator('#nbc-count-badge').evaluate(e=>e.classList.contains('show')),false);
  assert.equal((await snap()).cardCreatorSession.cards.length,0);

  // an unfinished session survives a reload and shows its count; unknown keys elsewhere stay
  await load(doc([{type:'text',html:'x'}],{cardCreatorSession:{deckName:'D',cards:[{id:'1',type:'basic',front:'F',back:'B'}],draft:{type:'basic',front:'half',back:'',includeSource:false}},futureKey:{keep:true}}));
  assert.equal(await page.locator('#nbc-count-badge').textContent(),'1');
  assert.equal(await page.inputValue('#nbc-front'),'half','an open panel is refreshed after a reload');assert.equal(await page.inputValue('#nbc-deck-name'),'D');
  assert.deepEqual((await snap()).futureKey,{keep:true});
  // malicious/garbage session data is sanitized, not trusted
  await load(doc([{type:'text',html:'x'}],{cardCreatorSession:{cards:[{front:5,type:'evil'},'x',null,{front:'ok',back:'b',type:'evil'}],draft:{type:'nope',front:{}},editingIndex:99}}));
  assert.equal(await page.locator('.creator-card-item').count(),1,'cards without a usable front are dropped');
  assert.equal(await page.inputValue('#nbc-type'),'basic');
  await page.click('#nbc-discard');
  assert.equal(await page.locator('.creator-card-item').count(),0);
  assert.equal(await page.locator('#nb-card-creator').isVisible(),false,'discarding closes the panel');
  // typing in the panel never triggers editor shortcuts (undo / slash menu)
  await page.click('#nb-creator-launch');
  await page.fill('#nbc-front','/h1 not a command');
  assert.equal(await page.evaluate(()=>document.getElementById('slash-menu').style.display==='block'),false);
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS notes card creator: panel, selection to front/back, cloze, edit, decks, session persistence, batch payload, errors');

  // ---- pictures pasted into the text of a field (Ctrl+V), with text above and below ----
  const png=(w,h,color='#2a6')=>page.evaluate(([w,h,color])=>{const c=document.createElement('canvas');c.width=w;c.height=h;const x=c.getContext('2d');x.fillStyle=color;x.fillRect(0,0,w,h);return c.toDataURL('image/png');},[w,h,color]);
  const pasteImage=(sel,uri,extra={})=>page.evaluate(async([sel,uri,extra])=>{
   const blob=await (await fetch(uri)).blob();const dt=new DataTransfer();
   if(!extra.htmlOnly)dt.items.add(new File([blob],'shot.png',{type:'image/png'}));
   if(extra.html)dt.setData('text/html',extra.html.replace('URI',uri));if(extra.text)dt.setData('text/plain',extra.text);
   const e=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});document.querySelector(sel).dispatchEvent(e);return e.defaultPrevented;
  },[sel,uri,extra]);
  const dims=uri=>page.evaluate(u=>new Promise(r=>{const i=new Image();i.onload=()=>r([i.naturalWidth,i.naturalHeight]);i.src=u;}),uri);
  await load([{type:'text',html:'x'}]);await page.evaluate(()=>{nbcOpen();});
  await page.evaluate(()=>{delete appState.cardCreatorSession;});await page.evaluate(()=>nbcOpen());
  await page.selectOption('#nbc-type','basic');
  await page.fill('#nbc-front','above');await page.focus('#nbc-front');await page.keyboard.press('End');
  const small=await png(60,40);
  assert.equal(await pasteImage('#nbc-front',small),true,'a pasted picture is taken over by the creator');
  await page.waitForFunction(()=>document.getElementById('nbc-front').value.includes('[[img:1]]'));
  assert.equal(await page.inputValue('#nbc-front'),'above\n[[img:1]]\n','the marker sits on its own line at the cursor');
  await page.keyboard.type('below');
  assert.equal(await page.inputValue('#nbc-front'),'above\n[[img:1]]\nbelow','text can be written below the picture');
  assert.equal(await page.locator('#nbc-front-images .creator-image').count(),1,'a thumbnail appears under the field');
  // in the middle of a line, and a second picture
  await page.fill('#nbc-back','abcd');await page.evaluate(()=>{const e=document.getElementById('nbc-back');e.focus();e.setSelectionRange(2,2);});
  await pasteImage('#nbc-back',await png(30,30,'#c33'));
  await page.waitForFunction(()=>document.getElementById('nbc-back').value.includes('[[img:'));
  assert.equal(await page.inputValue('#nbc-back'),'ab\n[[img:2]]\ncd','pasting inside a line splits it around the picture');
  // ordinary text paste is left to the browser
  assert.equal(await page.evaluate(()=>{const dt=new DataTransfer();dt.setData('text/plain','just text');const e=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});document.getElementById('nbc-front').dispatchEvent(e);return e.defaultPrevented;}),false);
  // a picture copied out of a note (HTML with a data: image and no text) is taken as well; with text it stays a text paste
  await page.fill('#nbc-front','');
  assert.equal(await pasteImage('#nbc-front',small,{htmlOnly:true,html:'<meta charset="utf-8"><img src="URI">'}),true);
  await page.waitForFunction(()=>document.getElementById('nbc-front').value.includes('[[img:'));
  assert.match(await page.inputValue('#nbc-front'),/^\[\[img:3\]\]\n$/);
  assert.equal(await pasteImage('#nbc-front',small,{htmlOnly:true,html:'<img src="URI">',text:'caption text'}),false,'a clipboard with text is a text paste');
  // big screenshots are scaled down
  await page.fill('#nbc-front','');await pasteImage('#nbc-front',await png(3200,1800,'#36c'));
  await page.waitForFunction(()=>document.getElementById('nbc-front').value.includes('[[img:4]]'));
  const bigId=await page.evaluate(()=>Object.keys(appState.cardCreatorSession.images).includes('4'));assert.ok(bigId);
  const [bw,bh]=await dims(await page.evaluate(()=>appState.cardCreatorSession.images['4']));
  assert.ok(Math.max(bw,bh)<=1600,'large pictures are scaled to 1600 px ('+bw+'x'+bh+')');
  // thumbnail: click finds the marker, × removes marker and picture
  const idIn=side=>page.evaluate(side=>(document.getElementById('nbc-'+side).value.match(/\[\[img:(\d+)\]\]/)||[])[1],side);
  await page.fill('#nbc-front','keep');await page.focus('#nbc-front');await page.keyboard.press('End');
  await pasteImage('#nbc-front',small);await page.waitForFunction(()=>document.getElementById('nbc-front').value.includes('[[img:'));
  await page.keyboard.type('more');
  const keptId=await idIn('front');assert.equal(await page.inputValue('#nbc-front'),`keep\n[[img:${keptId}]]\nmore`);
  await page.locator('#nbc-front-images .creator-image img').first().click();
  assert.equal(await page.evaluate(()=>{const e=document.getElementById('nbc-front');return e.value.slice(e.selectionStart,e.selectionEnd);}),`[[img:${keptId}]]`,'clicking a thumbnail selects its marker');
  await page.locator('#nbc-front-images .creator-image-remove').first().click();
  assert.equal(await page.inputValue('#nbc-front'),'keep\nmore','removing a picture removes its marker and closes the gap');
  assert.equal(await page.locator('#nbc-front-images .creator-image').count(),0);
  // cards list shows a glyph; the session keeps only used pictures; the batch carries just those
  await page.fill('#nbc-front','Q');await page.focus('#nbc-front');await page.keyboard.press('End');
  await pasteImage('#nbc-front',small);await page.waitForFunction(()=>document.getElementById('nbc-front').value.includes('[[img:'));
  await page.keyboard.type('what?');const usedId=await idIn('front');
  await page.fill('#nbc-back','A');
  await page.evaluate(()=>{document.getElementById('nbc-deck-name').value='Deck';document.getElementById('nbc-deck-name').dispatchEvent(new Event('input',{bubbles:true}));});
  await page.click('#nbc-add-card');
  assert.match(await page.locator('.creator-card-front').last().textContent(),/Q\n?🖼\n?what\?/,'the marker shows as a picture glyph in the list');
  await page.evaluate(()=>{window.__cmds=[];});
  await page.click('#nbc-finish');
  const [sent]=await batches();
  assert.deepEqual(Object.keys(sent.images),[usedId],'only pictures that cards still use are sent');
  assert.equal(sent.cards[0].front,`Q\n[[img:${usedId}]]\nwhat?`);
  await page.evaluate(()=>onNotebookCardBatchCreated(true,'ok'));
  await page.evaluate(()=>{persistNow();});
  assert.deepEqual(Object.keys((await snap()).cardCreatorSession.images||{}),[],'after creating, unused pictures are not kept');
  // restored session: malformed pictures are dropped
  await load([{type:'text',html:'x'}]);
  await page.evaluate(()=>{appState.cardCreatorSession={deckName:'D',cards:[],draft:{type:'basic',front:'[[img:5]] [[img:6]]',back:'',includeSource:false},images:{5:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9a8AAAAASUVORK5CYII=',6:'data:image/svg+xml;base64,PHN2Zz4=',x:'data:image/png;base64,AAAA'},nextImage:'bad'};});
  await page.evaluate(()=>{delete window.__nothing;nbcRefresh();});
  const restored=await page.evaluate(()=>{nbcOpen();return Object.keys(appState.cardCreatorSession.images);});
  assert.deepEqual(restored,['5'],'only well-formed pictures survive loading a session');
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS card pictures: paste at the cursor, text above and below, scaling, thumbnails, payload, cleaning');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
