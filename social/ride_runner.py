"""Ride only: Buffer Instagram + official Threads, with durable no-retry journals."""
import datetime as dt, json, os, re, subprocess, time, textwrap
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
from difflib import SequenceMatcher
import runner as shared
import ride_visuals as ride_photos

ROOT = shared.ROOT
BASE = ROOT/'social/ride'
STATE = BASE/'state.json'
IG = '6ae6e299e29c69a1712300e4'
START = dt.date(2026,11,4)  # Existing Publer reservations own Nov 1-3.
SITE = 'https://www.remaxride-tsuruta.com/'
TOPICS = ['space and daylight','Tokyo skyline views','the entrance experience',
          'room flow and comfort','details of everyday living','finding a home in Tokyo',
          'light and open living space','neighbourhood views','a place to feel at home']

def persist(state):
    BASE.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
    subprocess.run(['git','add','social/ride'],cwd=ROOT,check=True)
    if subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT).returncode == 1:
        subprocess.run(['git','commit','-m','Update Ride publishing journal and media'],cwd=ROOT,check=True)
        subprocess.run(['git','pull','--rebase','origin','main'],cwd=ROOT,check=True)
        subprocess.run(['git','push','origin','HEAD'],cwd=ROOT,check=True)

def threads(path, values=None):
    token=os.environ['RIDE_THREADS_ACCESS_TOKEN']
    req=Request('https://graph.threads.net/v1.0/'+path,
                None if values is None else urlencode(values).encode(),
                {'Authorization':'Bearer '+token})
    try:
        with urlopen(req,timeout=60) as response: return json.load(response)
    except HTTPError as e:
        raise RuntimeError('Threads HTTP '+str(e.code)+'; no automatic mutation retry') from None

def accounts():
    account=threads('me?fields=id,username')
    if account.get('username') != 'rideestate': raise RuntimeError('Wrong Threads account')
    channel=shared.graphql(shared.CHANNEL,{'input':{'id':IG}})['channel']
    if channel['name'].lstrip('@')!='rideestate' or channel['service']!='instagram':
        raise RuntimeError('Wrong Instagram account')
    if any(channel[k] for k in ('isDisconnected','isLocked','isQueuePaused')):
        raise RuntimeError('Instagram connection needs attention')
    return channel

def slots(now):
    result=[]
    for n in range(5):
        day=now.astimezone(shared.JST).date()+dt.timedelta(days=n)
        for kind,hour in [('post',22)]+([('reel',16)] if day.weekday() in (0,2,4) else []):
            if day<START and (kind=='post' or day<dt.date(2026,11,2)): continue
            stamp=shared.due(day,hour)
            if stamp>now+dt.timedelta(minutes=20): result.append((stamp,day,kind))
    return sorted(result)

def validate(c,history):
    limits={'title_en':48,'title_yue':22,'body_en':155,'body_yue':100}
    if set(c)!=set(limits)|{'slides_en','slides_yue'}: raise ValueError('Unexpected structure')
    for k,size in limits.items():
        if not isinstance(c[k],str) or not 3<=len(c[k])<=size: raise ValueError('Length: '+k)
    for k in ('slides_en','slides_yue'):
        if not isinstance(c[k],list) or len(c[k])!=4 or any(not isinstance(s,str) or not 2<=len(s)<=60 for s in c[k]):
            raise ValueError('Four short slides required')
    joined=json.dumps(c,ensure_ascii=False)
    if re.search(r'鶴田|Tsuruta|https?://|[0-9０-９]|guarantee|yield|return on|tax.free|profit|回報|保證|穩賺|免稅|獨家|放盤|筍盤',joined,re.I):
        raise ValueError('Unapproved claim')
    if not re.search(r'嘅|唔|睇|喺|撳|咩|搵|你哋|佢|諗|點樣|買嘢|啲|冇|咁',c['body_yue']): raise ValueError('Cantonese required')
    caption=c['body_en']+'\n\n'+c['body_yue']+'\n\nRE/MAX RIDE | Ride Estate Co., Ltd.\nEnglish: '+SITE+'en\n繁體中文: '+SITE
    if len(caption)>500: raise ValueError('Threads caption too long')
    if any(SequenceMatcher(None,c['body_en'],s).ratio()>.82 for s in history): raise ValueError('Repeated copy')
    return caption

def generate(day,kind,history):
    fields={'title_en':28,'title_yue':16,'body_en':140,'body_yue':90}
    props={k:{'type':'string','minLength':3,'maxLength':n} for k,n in fields.items()}
    props.update({k:{'type':'array','minItems':4,'maxItems':4,'items':{'type':'string','minLength':2,'maxLength':60}} for k in ('slides_en','slides_yue')})
    instruction=('Write photo-led RE/MAX RIDE Tokyo residence editorial copy. Headlines must be short and visually engaging: two to five English words and a short Cantonese phrase. Keep headlines about space, daylight, skyline views, entrance or everyday living; do not use administrative checklist titles. English AND natural Hong Kong Cantonese written with Traditional characters, not Mandarin. '
        'Only general organization, everyday home preferences and viewing questions. No laws, tax, finance, investment advice, numerical claims, listings, prices, availability, achievements or guarantees. '
        'No personal names, URLs, hashtags or digits. English body max140 characters and Cantonese body max90 characters. '
        'The body_yue must use spoken Cantonese words such as 嘅, 睇, 撳; avoid Mandarin expressions 的, 看房, 請瀏覽. '
        'Example Cantonese style: 睇樓之前，寫低你重視嘅條件。撳個人檔案入面嘅官方網站。 '
        'Use exactly these keys: title_en, title_yue, body_en, body_yue, slides_en, slides_yue. '
        'slides_en and slides_yue must each contain four strings of at most sixty characters. title_en max twenty eight characters, title_yue max sixteen. '
        'Invite readers to use the official website in the profile. Output only the JSON object, not schema definitions. /no_think')
    payload={'messages':[{'role':'system','content':instruction},{'role':'user','content':json.dumps({'theme':TOPICS[(day.toordinal()+(4 if kind=='reel' else 0))%len(TOPICS)],'kind':kind,'avoid':history[-10:]})}],
             'max_tokens':1800,'temperature':.65,'response_format':{'type':'json_schema','json_schema':{'type':'object','additionalProperties':False,'properties':props,'required':list(props)}}}
    raw=shared.request_json('https://api.cloudflare.com/client/v4/accounts/'+os.environ['CLOUDFLARE_ACCOUNT_ID']+'/ai/run/@cf/qwen/qwen3-30b-a3b-fp8',payload,os.environ['CLOUDFLARE_API_TOKEN'])
    if not raw.get('success'): raise RuntimeError('Free AI generation failed; no paid fallback')
    c=raw['result'].get('response')
    if isinstance(c,str): c=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',re.sub(r'<think>.*?</think>','',c,flags=re.S)).strip())
    try:
        validate(c,history)
    except ValueError as error:
        # Draft text is public marketing copy, never credentials or account metadata.
        raise ValueError(str(error)+'; generated draft='+json.dumps(c,ensure_ascii=False)) from None
    return c

def render(c,folder,kind):
    if kind=='reel': return ride_photos.render_reel(c,folder,BASE)
    return ride_photos.render_post(c,folder,BASE)


def public_media(state,path):
    persist(state)
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    return 'https://raw.githubusercontent.com/michikazutsuruta-design/sashiosae-rescue-test/'+sha+'/'+path.relative_to(ROOT).as_posix()

def run(mode):
    now=dt.datetime.now(dt.timezone.utc); channel=accounts()
    print('Both Ride accounts verified; no access to other brands.')
    if mode=='diagnose': return
    state=shared.load(STATE,{'journal':{},'history':[]})
    if mode=='build' and now.astimezone(shared.JST).date()>=dt.date(2026,11,3):
        last=shared.parsed(state.get('token_refreshed_at'))
        if not last or now-last>dt.timedelta(days=7):
            renewed=threads('refresh_access_token?grant_type=th_refresh_token')
            if renewed.get('access_token')!=os.environ['RIDE_THREADS_ACCESS_TOKEN']:
                raise RuntimeError('Threads returned a changed token. Update encrypted RIDE_THREADS_ACCESS_TOKEN; token is not logged.')
            state['token_refreshed_at']=now.isoformat()
            persist(state)
    posts=shared.fetch_posts({'instagram':channel},now)
    byid={p['id']:p for p in posts}
    for entry in state['journal'].values():
        if entry.get('id') in byid:
            p=byid[entry['id']]; entry.update(status=p['status'],url=p.get('externalLink'))
    persist(state)
    if mode in ('build','photos'):
        for key,entry in state['journal'].items():
            if entry.get('photo_update')=='attempting':
                raise RuntimeError('Inspect uncertain photo update before retry: '+key)
            if '/reel/instagram' not in key or entry.get('status')!='scheduled': continue
            if entry.get('visual_version')==ride_photos.VERSION: continue
            stamp=shared.parsed(entry.get('dueAt'))
            if not stamp or stamp<=now+dt.timedelta(minutes=20): continue
            if entry.get('id') not in byid: raise RuntimeError('Cannot verify reserved Reel '+key)
            day=key.split('/')[0]; folder=BASE/f'{day}-reel'
            c=shared.load(folder/'content.json',{})
            media=render(c,folder,'reel'); url=public_media(state,media)
            caption=validate(c,[])+'\n\n'+ride_photos.DISCLAIMER_EN+'\n'+ride_photos.DISCLAIMER_YUE
            entry['photo_update']='attempting'; persist(state)
            edit='''mutation Edit($input: EditPostInput!) { editPost(input:$input) {
              ... on PostActionSuccess { post { id channelId dueAt status text externalLink } }
              ... on MutationError { message } } }'''
            result=shared.graphql(edit,{'input':{'id':entry['id'],'assets':[{'video':{'url':url}}],
                'text':caption,'aiAssisted':True,
                'metadata':{'instagram':{'type':'reel','shouldShareToFeed':True,'isAiGenerated':False}}}})['editPost']
            p=result.get('post')
            if not p or p['id']!=entry['id'] or p['channelId']!=IG or shared.parsed(p['dueAt'])!=stamp:
                raise RuntimeError('Photo edit requires reconciliation: '+key)
            entry.update(photo_update='complete',visual_version=ride_photos.VERSION,media=url,caption=caption,status=p['status'])
            persist(state)
    if mode=='build':
        count=sum(p['status'] in ('scheduled','sending') for p in posts); generated=0
        for stamp,day,kind in slots(now):
            key=f'{day}/{kind}/instagram'
            found=[p for p in posts if shared.same_slot(p,IG,stamp)]
            if len(found)>1: raise RuntimeError('Multiple Instagram reservations at '+key)
            if found:
                entry=state['journal'].setdefault(key,{})
                entry.update(id=found[0]['id'],status=found[0]['status'],dueAt=stamp.isoformat())
                continue
            if key in state['journal'] or count>=10: continue
            folder=BASE/f'{day}-{kind}'; content=folder/'content.json'
            if content.exists(): c=shared.load(content,{})
            else:
                if generated>=2: break
                c=generate(day,kind,state['history']); generated+=1
                folder.mkdir(parents=True,exist_ok=True); content.write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')
                state['history']=(state['history']+[c['body_en']])[-120:]
            caption=validate(c,[])
            caption+='\n\n'+ride_photos.DISCLAIMER_EN+'\n'+ride_photos.DISCLAIMER_YUE
            media=render(c,folder,kind); url=public_media(state,media)
            state['journal'][key]={'status':'attempting','dueAt':stamp.isoformat()}; persist(state)
            payload={'text':caption,'channelId':IG,'assets':[{('video' if kind=='reel' else 'image'):{'url':url}}],
                     'schedulingType':'automatic','mode':'customScheduled','dueAt':stamp.isoformat(),'needsApproval':False,'aiAssisted':True,
                     'metadata':{'instagram':{'type':kind,'shouldShareToFeed':True,'isAiGenerated':False}}}
            result=shared.graphql(shared.CREATE,{'input':payload})['createPost']
            if not result.get('post'): raise RuntimeError('Instagram scheduling requires reconciliation')
            p=result['post']; state['journal'][key]={'id':p['id'],'status':p['status'],'dueAt':p['dueAt'],'media':url,'caption':caption,'visual_version':ride_photos.VERSION}; count+=1; posts.append(p); persist(state)
    if mode=='threads':
        day=now.astimezone(shared.JST).date(); stamp=shared.due(day,22)
        if day<START or not stamp<=now<=stamp+dt.timedelta(hours=2): return
        key=f'{day}/post/threads'
        if key in state['journal']: return  # Unknown attempts stop; never retry blindly.
        source=state['journal'].get(f'{day}/post/instagram',{})
        # Public campaign data only: do not upload or replace the existing publication journal.
        override=shared.load(BASE/'campaign-overrides.json',{}).get(str(day))
        if override:
            if set(override)!={'media','caption'} or len(override['caption'])>500:
                raise RuntimeError('Invalid public campaign override')
            source={**source,**override}
        if not source.get('caption') or not source.get('media'): raise RuntimeError('No prepared daily Ride content')
        recent=threads('me/threads?fields=id,text,permalink&limit=100').get('data',[])
        matched=[p for p in recent if p.get('text')==source['caption']]
        if matched:
            state['journal'][key]={'id':matched[0]['id'],'status':'published','url':matched[0].get('permalink')}; persist(state); return
        state['journal'][key]={'status':'attempting','dueAt':stamp.isoformat()}; persist(state)
        container=threads('me/threads',{'media_type':'IMAGE','image_url':source['media'],'text':source['caption']})
        state['journal'][key]['container_id']=container['id']; persist(state)
        ready=False
        for n in range(6):
            result=threads(container['id']+'?fields=status,error_message')
            if result.get('status')=='FINISHED': ready=True; break
            if result.get('status') in ('ERROR','EXPIRED'): break
            time.sleep(10)
        if not ready: raise RuntimeError('Threads container not ready; inspect before any retry')
        result=threads('me/threads_publish',{'creation_id':container['id']})
        state['journal'][key].update(id=result['id'],status='published'); persist(state)
        public=threads(result['id']+'?fields=id,permalink,text')
        if public.get('text')!=source['caption'] or not public.get('permalink'): raise RuntimeError('Threads publication verification incomplete')
        state['journal'][key]['url']=public['permalink']; persist(state)
    for key,entry in state['journal'].items():
        stamp=shared.parsed(entry.get('dueAt'))
        if entry.get('status') in ('error','attempting') or entry.get('photo_update')=='attempting': raise RuntimeError('Check unresolved Ride entry '+key)
        if stamp and stamp<now-dt.timedelta(minutes=45) and entry.get('status') in ('scheduled','sending'):
            raise RuntimeError('Ride publication overdue '+key)
    persist(state)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('mode',choices=['diagnose','build','threads','monitor','photos']); run(p.parse_args().mode)


