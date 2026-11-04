// Synthetic data only. The Python bridge is replaced by a recorder.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const status=(over={})=>({configured:true,folder_exists:true,git_available:true,device:'abc',last_sync:0,first_sync:false,busy:false,error:null,result:null,dirty:false,
 config:{sync_folder:'/home/me/sync',sync_git:true,sync_auto_pull:true,sync_auto_push:false,sync_notebook:true,sync_todos:true,sync_pdfs:false,sync_first:'merge'},
 git:{repo:true,branch:'main',remote:true,upstream:'origin/main',ahead:0,behind:0,changed:[]},
 local:{notebook:{added:[],modified:[],deleted:[]},todos:{added:[],modified:[],deleted:[]},pdfs:null},...over});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.SYNAPSE_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage({viewport:{width:760,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.__cmds=[];window.pycmd=c=>window.__cmds.push(c);});
  await page.goto('file://'+root+'/web_notebook/sync.html');
  const cmds=()=>page.evaluate(()=>window.__cmds.filter(c=>c.startsWith('notion_mini:sync:')).map(c=>c.slice('notion_mini:sync:'.length)));
  const render=s=>page.evaluate(s=>window.syncRender(s),s);
  const chips=()=>page.locator('#chips .chip').allTextContents();
  assert.ok((await cmds()).includes('refresh'),'asks for a fresh status when opened');

  // clean state
  await render(status({last_sync:1700000000}));
  let c=await chips();
  assert.ok(c.some(t=>/Everything is synced/.test(t)));assert.ok(c.some(t=>/Branch main/.test(t)));assert.ok(c.some(t=>/Last sync/.test(t)));
  assert.match(await page.locator('#pending').textContent(),/Everything is synced\./);
  assert.equal(await page.locator('#files-wrap').isVisible(),false,'no file list when nothing changed');

  // dirty state with pending items, unpushed commits and hostile titles
  await render(status({dirty:true,last_sync:0,git:{repo:true,branch:'main',remote:true,upstream:'',ahead:2,behind:1,changed:[['M','notebook/pages/a.json'],['A','notebook/pages/<img src=x onerror=window.__pwned=1>.json']]},
   local:{notebook:{added:[{id:'n',title:'<img src=x onerror=window.__pwned=1>'}],modified:[{id:'a',title:'Alpha'}],deleted:[{id:'b',title:''}]},todos:{added:[],modified:[{id:'t',title:'milk'}],deleted:[]}}}));
  c=await chips();
  assert.ok(c.some(t=>/Changes not pushed/.test(t)));assert.ok(c.some(t=>/2 to push/.test(t)));assert.ok(c.some(t=>/1 to pull/.test(t)));assert.ok(c.some(t=>/Never synced/.test(t)));
  assert.equal(await page.locator('#chips .chip.dirty').count()>=2,true);
  const pending=await page.locator('#pending').textContent();
  assert.match(pending,/Notebook · New/);assert.match(pending,/Notebook · Changed/);assert.match(pending,/Notebook · Deleted/);assert.match(pending,/To-do list · Changed/);assert.match(pending,/Untitled/);assert.match(pending,/2 saved changes are waiting/);
  assert.equal(await page.evaluate(()=>window.__pwned),undefined,'titles and file names are rendered as text, never as HTML');
  assert.equal(await page.locator('#pending img').count(),0);
  assert.equal(await page.locator('#files-wrap').isVisible(),true);
  assert.equal(await page.locator('#files .badge').count(),2);

  // not configured / not a repository / git missing
  await render(status({configured:false,config:{...status().config,sync_folder:''},git:{repo:false,branch:'',remote:false,upstream:'',ahead:0,behind:0,changed:[]}}));
  assert.ok((await chips()).some(t=>/Not set up/.test(t)));assert.match(await page.locator('#pending').textContent(),/Set up a sync folder/);
  await render(status({git:{repo:false,branch:'',remote:false,upstream:'',ahead:0,behind:0,changed:[]}}));
  assert.ok((await chips()).some(t=>/Not a Git repository/.test(t)));
  await render(status({git_available:false,git:{repo:false,branch:'',remote:false,upstream:'',ahead:0,behind:0,changed:[]}}));
  assert.ok((await chips()).some(t=>/Git is not installed/.test(t)));
  await render(status({git:{repo:true,branch:'main',remote:false,upstream:'',ahead:0,behind:0,changed:[]}}));
  assert.ok((await chips()).some(t=>/No remote set/.test(t)));

  // actions: buttons send commands and lock until Python answers
  await render(status());
  await page.evaluate(()=>{window.__cmds.length=0;});
  await page.click('#btn-sync');
  assert.deepEqual(await cmds(),['run:{"kind":"sync"}']);
  assert.equal(await page.locator('#btn-sync').isDisabled(),true);assert.match(await page.locator('#btn-sync').textContent(),/Syncing/);
  await page.evaluate(()=>window.syncResult({ok:true,kind:'sync',pushed:true,changed:{notebook:true,todos:false},conflicts:[['notebook','a','a-conflict-1']],skipped:[],error:null}));
  assert.equal(await page.locator('#btn-sync').isDisabled(),false);
  let msg=await page.locator('#message').textContent();
  assert.match(msg,/Synced\./);assert.match(msg,/New data from the other computer/);assert.match(msg,/1 pages were changed on both computers/);
  assert.match(await page.locator('#message').getAttribute('class'),/warn/,'a conflict is shown as a warning');
  await page.evaluate(()=>window.syncResult({ok:true,kind:'sync',pushed:true,changed:{pdfs:true},conflicts:[],skipped:[],files:3,oversized:['big.pdf'],missing_files:2,error:null}));
  msg=await page.locator('#message').textContent();
  assert.match(msg,/3 PDF files were copied/);assert.match(msg,/1 PDFs are larger than 50 MB/);assert.match(msg,/2 PDFs have no file on this computer/);
  assert.match(await page.locator('#message').getAttribute('class'),/warn/,'oversized or missing PDF files are a warning');
  await render(status({local:{notebook:null,todos:null,pdfs:{added:[{id:'p',title:'lecture.pdf'}],modified:[],deleted:[]}}}));
  assert.match(await page.locator('#pending').textContent(),/PDFs · New/);assert.match(await page.locator('#pending').textContent(),/lecture\.pdf/);
  await render(status());
  await page.click('#btn-pull');
  assert.equal(await page.locator('#btn-push').isDisabled(),true,'no second command while one is running');
  await page.evaluate(()=>window.syncResult({ok:true,kind:'pull',changed:{},conflicts:[],skipped:[],error:null}));
  assert.match(await page.locator('#message').textContent(),/Pulled and merged/);
  await page.click('#btn-push');
  await page.evaluate(()=>window.syncResult({ok:true,kind:'push',changed:{},conflicts:[],skipped:[],error:null}));
  assert.match(await page.locator('#message').textContent(),/Pushed\./);
  assert.deepEqual((await cmds()).slice(1),['run:{"kind":"pull"}','run:{"kind":"push"}']);

  // every error kind gets a readable message
  for(const [kind,pattern,cls] of [['offline',/No connection/,'warn'],['auth',/could not sign in/,'error'],['rejected',/Pull first/,'warn'],['timeout',/too long/,'warn'],['missing',/not a Git repository/,'warn'],['not_configured',/Choose a sync folder/,'error'],['busy',/already running/,'warn'],['flush',/could not be saved/,'error'],['save',/could not be stored/,'error'],['weird',/Something specific/,'error']]){
   await page.evaluate(k=>window.syncResult({ok:false,error:[k,'Something specific']}),kind);
   assert.match(await page.locator('#message').textContent(),pattern,kind);assert.match(await page.locator('#message').getAttribute('class'),new RegExp(cls),kind);
  }

  // settings round trip
  await render(status());
  await page.fill('#folder','/new/place');await page.uncheck('#use-git');
  assert.equal(await page.locator('#clone-field').isVisible(),false,'clone field only matters with Git');
  await page.check('#auto-push');await page.uncheck('#cat-todos');assert.equal(await page.isChecked('#cat-pdfs'),false,'PDFs are off by default');await page.check('#cat-pdfs');await page.selectOption('#first','remote');
  await page.evaluate(()=>{window.__cmds.length=0;});
  await page.click('#btn-save');
  assert.deepEqual(JSON.parse((await cmds())[0].slice('save:'.length)),{sync_folder:'/new/place',sync_git:false,sync_auto_pull:true,sync_auto_push:true,sync_notebook:true,sync_todos:false,sync_pdfs:true,sync_first:'remote'});
  // unsaved edits survive a status refresh; unsaved edits are saved before a run
  await page.fill('#folder','/typing');await render(status());
  assert.equal(await page.inputValue('#folder'),'/typing','a refresh never overwrites what you are typing');
  await page.evaluate(()=>{window.__cmds.length=0;});await page.click('#btn-sync');
  const sent=await cmds();assert.ok(sent[0].startsWith('save:')&&sent[1].startsWith('run:'),'settings are saved before syncing');
  await page.evaluate(()=>window.syncResult({ok:true,kind:'sync',changed:{},conflicts:[],skipped:[],error:null}));
  // folder picker and clone
  await page.evaluate(()=>window.syncFolderChosen('/picked'));
  assert.equal(await page.inputValue('#folder'),'/picked');
  await page.check('#use-git');await page.evaluate(()=>{window.__cmds.length=0;});
  await page.click('#btn-clone');assert.match(await page.locator('#message').textContent(),/Enter the repository address/);assert.deepEqual(await cmds(),[]);
  await page.fill('#repo-url','git@example.org:me/notes.git');await page.click('#btn-clone');
  const cloneCmds=await cmds();assert.ok(cloneCmds[0].startsWith('save:'));assert.equal(cloneCmds[1],'clone:{"url":"git@example.org:me/notes.git"}');
  await page.evaluate(()=>window.syncResult({ok:true}));
  await page.evaluate(()=>{window.__cmds.length=0;});await page.click('#btn-choose');assert.deepEqual(await cmds(),['choose-folder']);

  // garbage from Python never breaks the page
  await page.evaluate(()=>{window.syncRender(null);window.syncRender('x');window.syncResult(null);});
  assert.equal(await page.locator('#btn-sync').isVisible(),true);
  assert.deepEqual(errors,[],'no page errors');
  console.log('PASS sync tab: status chips, pending changes, safe rendering, actions, messages, settings');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
