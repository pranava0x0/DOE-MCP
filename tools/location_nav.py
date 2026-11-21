"""Shared, progressively enhanced page location for generated static pages."""
from __future__ import annotations

import html


def navigation(entries: list[tuple[str, str, int]], *, context: str = "", topbar: str = "") -> str:
    links = "".join(f'<li class="location-depth-{depth}"><a href="#{html.escape(target, quote=True)}"'
                    + (' aria-current="location"' if index == 0 else '')
                    + f'>{html.escape(label)}</a></li>' for index, (target, label, depth) in enumerate(entries))
    first = entries[0][1] if entries else "Overview"
    return (f'<aside class="page-location" data-location-nav data-location-context="{html.escape(context, quote=True)}" '
            f'data-location-topbar="{html.escape(topbar, quote=True)}">'
            '<details class="location-menu" open><summary class="location-toggle">'
            f'<span>Current: </span><span data-current-location>{html.escape(first)}</span></summary>'
            f'<nav aria-label="On this page"><p class="location-context">{html.escape(context or "On this page")}</p>'
            f'<ul data-location-links>{links}</ul></nav></details></aside>')


CSS = """
.page-location{color:#203c31;background:#faf8f0;border-bottom:1px solid #b8c6ba;z-index:8}
.page-location *{box-sizing:border-box}
.page-location .location-menu{margin:0;border:0;border-radius:0;background:inherit}
.page-location .location-toggle{min-height:44px;padding:10px 16px;cursor:pointer;line-height:1.45;font:600 .9rem/1.45 system-ui,sans-serif}
.page-location .location-toggle [data-current-location]{font-weight:400}
.page-location .location-toggle:focus-visible,.page-location a:focus-visible{outline:3px solid #a65b14;outline-offset:-3px}
.page-location nav{display:block;padding:8px 12px;background:inherit}
.page-location .location-context{font:650 .9rem/1.45 system-ui,sans-serif;margin:4px 8px 10px;overflow-wrap:anywhere}
.page-location ul{display:block;list-style:none;padding:0;margin:0}
.page-location li{margin:0;padding:0;line-height:1.35}
.page-location a{display:block;min-height:36px;padding:8px 10px;border-left:3px solid transparent;color:#305547;text-decoration:none;font:400 .87rem/1.45 system-ui,sans-serif;overflow-wrap:anywhere}
.page-location .location-depth-1 a{padding-left:22px;font-size:.81rem}
.page-location a:hover{background:#e8eee3}
.page-location a[aria-current="location"]{border-left-color:#1e5d47;background:#dfeadd;color:#183b2e;font-weight:700}
[data-location-target]{scroll-margin-top:var(--location-offset,70px)}
@media(min-width:1200px){
body[data-location-layout]{padding-left:15rem}
.page-location{position:fixed;left:0;top:0;bottom:0;width:15rem;border:0;border-right:1px solid #b8c6ba;overflow:auto;padding:16px 8px}
/* Preserve focus until layout() transfers it to a desktop section link. */
.page-location .location-menu[open]>.location-toggle:not(:focus){display:none}
}
@media(max-width:1199px){
.page-location{position:sticky;top:var(--location-site-top,0px)}
.page-location .location-toggle{display:list-item;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.page-location nav{max-height:min(65vh,32rem);overflow:auto;overscroll-behavior:contain}
.page-location a{min-height:44px}
}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto!important}}
"""


SCRIPT = r"""
(()=>{
  const shell=document.querySelector('[data-location-nav]');
  if(!shell)return;
  const disclosure=shell.querySelector('.location-menu');
  const toggle=shell.querySelector('.location-toggle');
  const links=shell.querySelector('[data-location-links]');
  const currentText=shell.querySelector('[data-current-location]');
  const contextText=shell.querySelector('.location-context');
  const desktop=matchMedia('(min-width:1200px)');
  let context=shell.dataset.locationContext||'', targets=[], pending=false, last='';
  document.body.dataset.locationLayout='';
  const decode=value=>{try{return decodeURIComponent(value);}catch{return '';}};
  function visible(el){
    if(!el||el.closest('[hidden]')||!el.getClientRects().length)return false;
    for(let ancestor=el.parentElement;ancestor;ancestor=ancestor.parentElement){
      if(ancestor.tagName==='DETAILS'&&!ancestor.open){
        const summary=ancestor.querySelector(':scope > summary');
        if(!summary||!summary.contains(el))return false;
      }
    }
    return getComputedStyle(el).visibility!=='hidden';
  }
  function measure(){
    const selector=shell.dataset.locationTopbar;
    const bar=selector?document.querySelector(selector):null;
    const height=bar&&visible(bar)?bar.getBoundingClientRect().height:0;
    document.documentElement.style.setProperty('--location-site-top',height+'px');
    const own=desktop.matches?0:shell.getBoundingClientRect().height;
    document.documentElement.style.setProperty('--location-offset',(height+own+16)+'px');
    shell.dataset.locationOffset=String(height+own+16);
    return height+own+16;
  }
  function setCurrent(item){
    if(!item)return;
    for(const anchor of links.querySelectorAll('a')){
      if(decode(anchor.hash.slice(1))===item.id)anchor.setAttribute('aria-current','location');
      else anchor.removeAttribute('aria-current');
    }
    const label=item.label+(context&&context!==item.label?' · '+context:'');
    if(currentText.textContent!==label)currentText.textContent=label;
    shell.dataset.currentTarget=item.id;
    if(last!==item.id){
      last=item.id;
      document.dispatchEvent(new CustomEvent('location:current',{detail:{id:item.id}}));
    }
  }
  function update(){
    pending=false;
    const offset=measure();
    const eligible=targets.filter(item=>visible(item.el));
    let item=eligible[0];
    for(const candidate of eligible){if(candidate.el.getBoundingClientRect().top<=offset+8)item=candidate;else break;}
    const page=document.scrollingElement;
    if(page.scrollHeight>innerHeight+2&&page.scrollTop+innerHeight>=page.scrollHeight-2)item=eligible.at(-1)||item;
    setCurrent(item);
  }
  function schedule(){if(!pending){pending=true;requestAnimationFrame(update);}}
  function configure(entries,newContext){
    if(newContext!==undefined)context=newContext;
    contextText.textContent=context||'On this page';
    const restoreLinkFocus=links.contains(document.activeElement);
    if(entries){
      links.replaceChildren();
      for(const entry of entries){
        const li=document.createElement('li'),anchor=document.createElement('a');
        li.className='location-depth-'+(entry.depth||0);
        anchor.href='#'+encodeURIComponent(entry.id);anchor.textContent=entry.label;
        li.append(anchor);links.append(li);
      }
    }
    targets=[...links.querySelectorAll('a[href^="#"]')].map(anchor=>{
      const id=decode(anchor.hash.slice(1));return{id,label:anchor.textContent,el:document.getElementById(id)};
    }).filter(item=>item.el);
    targets.sort((a,b)=>a.el===b.el?0:a.el.compareDocumentPosition(b.el)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1);
    for(const target of targets)target.el.dataset.locationTarget='';
    if(entries&&restoreLinkFocus)links.querySelector('a')?.focus({preventScroll:true});
    last='';schedule();
  }
  function reveal(target){
    document.dispatchEvent(new CustomEvent('location:reveal',{detail:{target}}));
    for(let node=target;node;node=node.parentElement){if(node.tagName==='DETAILS')node.open=true;}
    const own=target.querySelector('details.sect');if(own)own.open=true;
    for(let sibling=target.nextElementSibling;sibling&&!/^H[1-4]$/.test(sibling.tagName);sibling=sibling.nextElementSibling){
      if(sibling.tagName==='DETAILS'){sibling.open=true;break;}
    }
  }
  function navigate(id,{push=true,focus=true}={}){
    const target=document.getElementById(id);if(!target)return false;
    target.dataset.locationTarget='';
    reveal(target);
    const menuHadFocus=links.contains(document.activeElement);
    if(!desktop.matches)disclosure.open=false;
    measure();
    if(!focus&&menuHadFocus&&!desktop.matches)toggle.focus({preventScroll:true});
    if(push&&decode(location.hash.slice(1))!==id){try{history.pushState(null,'','#'+encodeURIComponent(id));}catch{}}
    if(focus){if(!target.hasAttribute('tabindex'))target.tabIndex=-1;target.focus({preventScroll:true});}
    target.scrollIntoView({block:'start',behavior:'auto'});schedule();return true;
  }
  document.addEventListener('click',event=>{
    if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    const anchor=event.target instanceof Element?event.target.closest('a[href^="#"]'):null;
    if(!anchor||anchor.hasAttribute('download')||anchor.target&&anchor.target!=='_self')return;
    const id=decode(anchor.getAttribute('href').slice(1));if(!id||!document.getElementById(id))return;
    event.preventDefault();navigate(id);
  });
  const follow=()=>{const id=decode(location.hash.slice(1));
    if(!id||!navigate(id,{push:false,focus:false})){document.dispatchEvent(new CustomEvent('location:reset'));schedule();}
  };
  addEventListener('hashchange',follow);addEventListener('popstate',follow);
  addEventListener('scroll',schedule,{passive:true});addEventListener('resize',schedule);
  document.addEventListener('toggle',schedule,true);
  disclosure.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!desktop.matches&&disclosure.open){disclosure.open=false;toggle.focus({preventScroll:true});event.preventDefault();}
  });
  document.addEventListener('focusin',event=>{
    if(!desktop.matches&&disclosure.open&&!shell.contains(event.target)){disclosure.open=false;schedule();}
  });
  function layout(){
    const toggleFocused=document.activeElement===toggle;
    disclosure.open=desktop.matches||links.contains(document.activeElement);
    if(desktop.matches&&toggleFocused)(links.querySelector('[aria-current]')||links.querySelector('a'))?.focus({preventScroll:true});
    schedule();
  }
  desktop.addEventListener('change',layout);
  new ResizeObserver(schedule).observe(shell);
  const bar=shell.dataset.locationTopbar?document.querySelector(shell.dataset.locationTopbar):null;
  if(bar)new ResizeObserver(schedule).observe(bar);
  window.DOELocation={configure,navigate,refresh:schedule};
  configure();layout();requestAnimationFrame(follow);
})();
"""
