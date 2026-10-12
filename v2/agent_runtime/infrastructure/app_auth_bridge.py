"""Trusted-origin renewal bridge. Only scoped tokens cross into an app's origin."""
from __future__ import annotations

import json
from urllib.parse import urlencode


def entry_script(*, root_origin: str, agent_id: str, namespace: str, token: str) -> str:
    frame_url = root_origin + "/app-session?" + urlencode({
        "agentId": agent_id, "namespace": namespace,
    })
    config = json.dumps({"root": root_origin, "frame": frame_url, "token": token}).replace("<", "\\u003c")
    return """<script>(()=>{const c=CONFIG;
function accept(token){const p=JSON.parse(atob(token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')));
window.__agentdAppToken={state:'ok',via:'app',accessToken:token,expiresAt:p.exp,accountId:p.sub};
fetch('/app-cookie?session='+encodeURIComponent(token),{cache:'no-store'}).catch(()=>{});
window.dispatchEvent(new CustomEvent('agentd:app-token'));}
accept(c.token);const f=document.createElement('iframe');f.hidden=true;f.src=c.frame;
window.addEventListener('message',e=>{if(e.source===f.contentWindow&&e.origin===c.root&&e.data?.type==='agentd-app-token')accept(e.data.token)});
window.addEventListener('DOMContentLoaded',()=>document.body.appendChild(f));})();</script>""".replace("CONFIG", config)


def renewal_page(*, accounts_url: str, agent_id: str, namespace: str, parent_origin: str) -> bytes:
    config = json.dumps({"accounts": accounts_url, "agent": agent_id, "namespace": namespace,
                         "parent": parent_origin}).replace("<", "\\u003c")
    return ("""<!doctype html><meta charset=utf-8><script>(async()=>{const c=CONFIG;
async function token(){let cached;try{cached=JSON.parse(localStorage.getItem('agentd.app-parent')||'null')}catch{}
if(cached&&cached.expiresAt>Date.now()/1000+120)return cached.token;
const r=await fetch(c.accounts+'/auth/refresh',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookie:true})});
if(!r.ok)throw Error('sign-in required');const a=await r.json();
cached={token:a.access_token,expiresAt:Date.now()/1000+(a.expires_in||600),accountId:a.account_id,email:a.email};
localStorage.setItem('agentd.app-parent',JSON.stringify(cached));return cached.token;}
async function renew(){try{const t=await(navigator.locks?navigator.locks.request('agentd.cookie-renew',token):token());
const q=new URLSearchParams({session:t,agentId:c.agent,namespace:c.namespace,returnOrigin:c.parent});
const r=await fetch('/app-token?'+q.toString(),{cache:'no-store'});if(!r.ok){if(r.status===401)localStorage.removeItem('agentd.app-parent');return;}
const a=await r.json();parent.postMessage({type:'agentd-app-token',token:a.token},c.parent);
}catch{}}
await renew();setInterval(renew,60000);})();</script>""").replace("CONFIG", config).encode()
