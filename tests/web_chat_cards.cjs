// Synthetic data only. The Python bridge is replaced by a recorder.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9a8AAAAASUVORK5CYII=';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage({viewport:{width:520,height:820}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('file://'+root+'/chat_ui.html');
  await page.evaluate(()=>{window.__calls=[];sendToPython=(a,d)=>window.__calls.push([a,d]);});
  const calls=name=>page.evaluate(n=>window.__calls.filter(c=>c[0]===n).map(c=>c[1]),name);
  const cardsButton=page.locator('#start-center [data-notes-cards-button]');
  assert.equal(await cardsButton.isDisabled(),true,'disabled without selected notes');
  await page.evaluate(()=>{receiveNotes({folders:[],pages:[{id:'a',title:'Heart'}]});openNotesPicker();});
  await page.locator('#notes-list input').check();await page.click('#notes-modal .notes-btn.primary');
  assert.equal(await cardsButton.isDisabled(),false,'enabled once notes are attached');
  await cardsButton.click();
  assert.deepEqual(await calls('notes_cards'),[{noteIds:['a']}]);
  assert.equal(await cardsButton.isDisabled(),true,'no double start while generating');
  // error while generating returns to normal
  await page.evaluate(()=>receiveCardsError('Boom'));
  assert.equal(await page.evaluate(()=>busy),false);
  assert.equal(await cardsButton.isDisabled(),false);
  // suggestions arrive
  await page.evaluate(([png])=>receiveCardSuggestions({defaultDeck:7,decks:[{id:1,name:'Default'},{id:7,name:'Anatomy'}],images:{img1:png},
   cards:[{front:'Which valve?',back:'Aortic',image:'img1'},{front:'Second',back:'B',image:null},{front:'Third',back:'C',image:null}]}),[PNG]);
  assert.equal(await page.locator('#cards-modal').evaluate(e=>e.classList.contains('open')),true);
  assert.equal(await page.locator('#cards-deck').inputValue(),'7','current deck preselected');
  assert.equal(await page.locator('.card-item').count(),3);
  assert.equal(await page.locator('.card-image img').count(),1,'only the card with an image shows a picture');
  assert.equal(await page.locator('#cards-count').textContent(),'3 / 3');
  if(process.env.SYNAPSE_TEST_SHOT)await page.screenshot({path:process.env.SYNAPSE_TEST_SHOT});
  // edit, change placement, untick, remove another card's picture
  await page.locator('.card-item textarea').nth(1).fill('Aortic valve');
  await page.locator('.card-image select').selectOption('front');
  await page.locator('.card-item-head input').nth(2).uncheck();
  assert.equal(await page.locator('#cards-count').textContent(),'2 / 3');
  await page.click('#cards-add-btn');
  assert.deepEqual(await calls('create_cards'),[{deckId:7,cards:[
   {front:'Which valve?',back:'Aortic valve',image:'img1',placement:'front'},
   {front:'Second',back:'B',image:null,placement:'back'}]}],'only reviewed cards are sent, never image data');
  assert.equal(await page.locator('#cards-add-btn').isDisabled(),true,'no double submit');
  await page.evaluate(()=>receiveCardsError('Could not create the cards.'));
  assert.match(await page.locator('#cards-status').textContent(),/Could not create/);
  assert.equal(await page.locator('#cards-modal').evaluate(e=>e.classList.contains('open')),true,'preview stays open on failure');
  assert.equal(await page.locator('#cards-add-btn').isDisabled(),false);
  // remove picture, then success closes and reports
  await page.click('.card-image .notes-btn');
  assert.equal(await page.locator('.card-image').count(),0);
  await page.click('#cards-add-btn');
  assert.equal((await calls('create_cards')).at(-1).cards[0].image,null);
  await page.evaluate(()=>receiveCardsCreated(2,'Anatomy'));
  assert.equal(await page.locator('#cards-modal').evaluate(e=>e.classList.contains('open')),false);
  assert.match(await page.locator('#chat').textContent(),/Added 2 cards to “Anatomy”\./);
  // Escape closes the preview, nothing is sent
  const before=(await calls('create_cards')).length;
  await page.evaluate(()=>receiveCardSuggestions({defaultDeck:1,decks:[{id:1,name:'Default'}],images:{},cards:[{front:'Q',back:'A',image:null}]}));
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#cards-modal').evaluate(e=>e.classList.contains('open')),false);
  assert.equal((await calls('create_cards')).length,before);
  // empty selection is refused locally
  await page.evaluate(()=>receiveCardSuggestions({defaultDeck:1,decks:[{id:1,name:'Default'}],images:{},cards:[{front:'Q',back:'A',image:null}]}));
  await page.locator('.card-item-head input').uncheck();
  assert.equal(await page.locator('#cards-add-btn').isDisabled(),true);
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS cards from notes: enable/disable, preview, edit, picture placement/removal, submit payload, errors');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
