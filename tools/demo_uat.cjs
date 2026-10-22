const {chromium}=require('playwright');
const fs=require('fs');
const base=process.env.UAT_URL||'http://localhost:4192';
fs.mkdirSync('uat-screenshots',{recursive:true});
(async()=>{
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
const page=await browser.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));
const report={viewports:[],errors};
for(const [width,height] of [[768,1024],[375,812],[1280,800]]){
 await page.setViewportSize({width,height});await page.goto(base+'/demos.html#microbiome');
 const cases=await page.locator('#workflow option').evaluateAll(es=>es.map(e=>e.value));
 let jumps=0;
 for(const id of cases){
  await page.locator('#workflow').selectOption(id);
  if(await page.locator('article:visible').count()!==1)throw Error('visible panel '+id);
  const state=await page.evaluate(()=>({w:innerWidth,sw:document.documentElement.scrollWidth}));
  if(state.sw>state.w+1)throw Error('overflow '+id+' '+width);
  const buttons=page.locator('#step-nav button');
  for(let i=1;i<await buttons.count();i++){
   await buttons.nth(i).click();
   const ok=await page.evaluate(()=>{const el=document.activeElement;const a=el.getBoundingClientRect(),b=document.querySelector('#picker').getBoundingClientRect();return el.classList.contains('step')&&a.top>=b.bottom-1&&a.top<innerHeight;});
   if(!ok)throw Error('jump hidden '+id+' '+i+' '+width);
   jumps++;
  }
 }
 report.viewports.push({width,height,cases:cases.length,jumps});
}
await page.setViewportSize({width:375,height:812});await page.locator('#workflow').selectOption('microbiome');
await page.getByRole('button',{name:'2. Samples',exact:true}).click();
report.mobile=await page.evaluate(()=>({sampleTop:document.activeElement.getBoundingClientRect().top,toolbarBottom:document.querySelector('#picker').getBoundingClientRect().bottom,sidewaysTables:[...document.querySelectorAll('#microbiome .scroll')].filter(e=>e.scrollWidth>e.clientWidth+1).length}));
await page.screenshot({path:'uat-screenshots/2026-10-22-mobile-samples.png'});
await page.locator('#microbiome').getByText('Record sources',{exact:true}).click();
if(!await page.locator('#microbiome .step').last().locator('.evidence').isVisible())throw Error('source disclosure');
await page.locator('#step-nav button').first().click();
const dl=page.waitForEvent('download');await page.locator('#microbiome a[download]').first().click();const download=await dl;await download.saveAs('/tmp/doe-uat-download.json');JSON.parse(fs.readFileSync('/tmp/doe-uat-download.json'));
report.download=true;
await page.locator('#workflow').focus();await page.keyboard.press('Tab');await page.keyboard.press('Tab');await page.keyboard.press('Enter');
report.keyboard=await page.evaluate(()=>document.activeElement.id==='microbiome-step-1');
if(!report.keyboard)throw Error('keyboard jump');
await page.goto(base+'/demos.html#not-a-demo');if(await page.locator('#workflow').inputValue()!=='evidence')throw Error('hash fallback');
await page.goto(base+'/demos.html#facility-status');await page.screenshot({path:'uat-screenshots/2026-10-22-mobile-status.png'});
const cdp=await page.context().newCDPSession(page);await cdp.send('Network.enable');await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
await page.goto(base+'/demos.html#microbiome');
report.cpu4xLocal=await page.evaluate(()=>{const n=performance.getEntriesByType('navigation')[0];return{loadMs:n.loadEventEnd,domMs:n.domContentLoadedEventEnd,bytes:n.decodedBodySize,resources:performance.getEntriesByType('resource').map(r=>r.name)}});
await page.getByRole('button',{name:'2. Samples',exact:true}).click();
report.cpu4xLocal.jumpWorked=await page.evaluate(()=>document.activeElement.classList.contains('step'));
await browser.close();fs.writeFileSync('uat-screenshots/results.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));if(errors.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exit(1)});
