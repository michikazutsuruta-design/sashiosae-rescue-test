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
SITE = 'https://tinyurl.com/RIDEESTATE'
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

def threads(path, values=None, token=None):
    token=token or _active_threads_token or os.environ['RIDE_THREADS_ACCESS_TOKEN']
    req=Request('https://graph.threads.net/v1.0/'+path,
                None if values is None else urlencode(values).encode(),
                {'Authorization':'Bearer '+token})
    try:
        with urlopen(req,timeout=60) as response: return json.load(response)
    except HTTPError as e:
        raise RuntimeError('Threads HTTP '+str(e.code)+'; no automatic mutation retry') from None

# The existing GitHub secret remains the root key; refreshed credentials are
# authenticated ciphertext only. Neither plaintext token nor key enters Git/logs.
_active_threads_token = None
TOKEN_CACHE = BASE/'threads-token.enc'

def token_cipher():
    import base64, hashlib
    from cryptography.fernet import Fernet
    root=os.environ['RIDE_THREADS_ACCESS_TOKEN'].encode()
    key=hashlib.sha256(b'ride-threads-token-cache-v1\0'+root).digest()
    return Fernet(base64.urlsafe_b64encode(key))

def load_threads_token():
    global _active_threads_token
    if not TOKEN_CACHE.exists():
        _active_threads_token=os.environ['RIDE_THREADS_ACCESS_TOKEN']
        return
    try:
        value=json.loads(token_cipher().decrypt(TOKEN_CACHE.read_bytes()))
        if value.get('account')!='rideestate' or not value.get('access_token'):
            raise ValueError('Unexpected token cache')
        _active_threads_token=value['access_token']
    except Exception:
        raise RuntimeError('Encrypted Threads credential cannot be verified; inspect secret rotation before proceeding') from None

def refresh_threads_token(state, now):
    """Refresh, verify the same account, then persist authenticated ciphertext."""
    global _active_threads_token
    last=shared.parsed(state.get('token_refreshed_at'))
    if last and now-last<=dt.timedelta(days=7): return
    token=_active_threads_token or os.environ['RIDE_THREADS_ACCESS_TOKEN']
    url='https://graph.threads.net/refresh_access_token?'+urlencode({'grant_type':'th_refresh_token','access_token':token})
    try:
        with urlopen(Request(url,headers={'Authorization':'Bearer '+token}),timeout=60) as response:
            renewed=json.load(response)
        candidate=renewed.get('access_token')
        lifetime=renewed.get('expires_in')
        if not isinstance(candidate,str) or not candidate or not isinstance(lifetime,int) or lifetime<=0:
            raise ValueError('Invalid renewal response')
        verified=threads('me?fields=id,username',token=candidate)
        if verified.get('username')!='rideestate':
            raise ValueError('Unexpected renewed account')
        encrypted=token_cipher().encrypt(json.dumps({
            'account':'rideestate','access_token':candidate,
            'refreshed_at':now.isoformat()
        }).encode())
        BASE.mkdir(parents=True,exist_ok=True)
        temporary=TOKEN_CACHE.with_suffix('.tmp')
        temporary.write_bytes(encrypted)
        temporary.replace(TOKEN_CACHE)
    except HTTPError as error:
        state['token_refresh_warning']='Threads renewal HTTP '+str(error.code)
    except (OSError,ValueError,RuntimeError):
        state['token_refresh_warning']='Threads renewal could not be verified'
    else:
        state['token_refreshed_at']=now.isoformat()
        state['token_expires_at']=(now+dt.timedelta(seconds=lifetime)).isoformat()
        state.pop('token_refresh_warning',None)
        # A failed push stops the run. Do not claim durable renewal before it succeeds.
        persist(state)
        _active_threads_token=candidate
        print('Threads renewal verified and stored as authenticated ciphertext.')
        return
    persist(state)
    if state.get('token_refresh_warning'):
        print('Token maintenance needs attention; verified accounts may continue publishing.')

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
    caption=c['body_en']+'\n\n'+c['body_yue']+'\n\nRE/MAX RIDE | Ride Estate Co., Ltd.\nOfficial website / 官方網站: '+SITE
    if len(caption)>500: raise ValueError('Threads caption too long')
    if any(SequenceMatcher(None,c['body_en'],s).ratio()>.82 for s in history): raise ValueError('Repeated copy')
    return caption

def generate_draft(day,kind,history):
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


EDITORIAL_BACKUP = [
 ('A brighter morning','朝早嘅自然光','Picture your morning routine in a light-filled room. Explore our official website through the profile.','朝早入屋嘅自然光，會點樣影響你嘅生活節奏？撳個人檔案睇官方網站。'),
 ('Space for your day','日常需要嘅空間','Think about the space your daily routines need, from reading to sharing a meal. Find our official website in the profile.','睇空間之前，諗吓平日閱讀同食飯需要啲咩。想了解更多，撳個人檔案入面嘅官方網站。'),
 ('A view to pause for','停低睇吓風景','A window can frame a moment of calm. Consider the outlook you enjoy and explore our official website in the profile.','窗外嘅風景，可以令你想停低一陣。你鍾意點樣嘅景觀？撳個人檔案睇官方網站。'),
 ('Arrive at your own pace','返到屋企嘅一刻','Imagine the feeling of coming home. Which entrance details matter to you? Visit the official website linked in our profile.','返到屋企嗰一刻，你最重視入口嘅邊啲細節？撳個人檔案入面嘅官方網站了解更多。'),
 ('Room to gather','一齊相聚嘅空間','From quiet evenings to time with friends, consider how a living room fits your plans. Official website in our profile.','由自己靜靜休息，到同朋友相聚，客廳要點樣配合你嘅生活？撳個人檔案睇官方網站。'),
 ('The everyday details','留意生活細節','Look closer at materials, light and the connections between rooms. Explore more through the official website in our profile.','物料、光線，同房間之間嘅連接，都值得慢慢睇。想了解更多，撳個人檔案入面嘅官方網站。'),
 ('Find your daily rhythm','搵到生活節奏','Picture an ordinary day at home, from your first coffee to winding down. Talk to us through the website in our profile.','由第一杯咖啡，到夜晚放鬆，你想喺屋企過點樣嘅一日？撳個人檔案入面嘅官方網站同我哋傾吓。'),
 ('A place for quiet','留啲空間畀自己','Consider where you would read, rest or simply enjoy the light. Discover our official website through the profile link.','你會揀邊個角落睇書、休息，或者靜靜享受光線？想了解更多，撳個人檔案嘅官方網站。'),
 ('See how rooms connect','睇吓空間點樣連接','Notice how rooms connect and imagine moving through your day. Visit our official website via the profile link.','睇吓房間之間點樣連接，再諗平日行出行入順唔順。撳個人檔案入面嘅官方網站了解更多。'),
 ('Make room for comfort','住得自在啲','Comfort starts with your own priorities. Share what matters to you through the official website linked in our profile.','住得舒服，每個人重視嘅條件都唔同。撳個人檔案入面嘅官方網站，同我哋講吓你嘅需要。'),
 ('A different perspective','換個角度睇空間','Take a second look at the relationship between windows and living space. Our official website is linked in the profile.','換個角度睇吓窗戶同客廳嘅關係，可能會留意到唔同細節。更多介紹請撳個人檔案嘅官方網站。'),
 ('Your next chapter','你嘅下一段生活','Start with the everyday moments you want a home to support. Explore the official website through our profile.','搵屋企，不妨由你想過嘅日常開始諗。想了解更多，撳個人檔案入面嘅官方網站。'),
]

def generate(day,kind,history):
    # Bounded generation retries; vetted editorial copy keeps daily preparation running.
    for attempt in range(3):
        try: return generate_draft(day,kind,history)
        except (ValueError,RuntimeError,OSError):
            print('Draft did not pass checks; trying another safe draft.')
    start=(day.toordinal()+(4 if kind=='reel' else 0))%len(EDITORIAL_BACKUP)
    for offset in range(len(EDITORIAL_BACKUP)):
        en,zh,body,yue=EDITORIAL_BACKUP[(start+offset)%len(EDITORIAL_BACKUP)]
        c={'title_en':en,'title_yue':zh,'body_en':body,'body_yue':yue,
           'slides_en':['Natural light','Everyday space','Look closer','Official website in profile'],
           'slides_yue':['自然光入屋','日常生活空間','細節值得睇吓','撳個人檔案睇網站']}
        try: validate(c,history[-7:])
        except ValueError: continue
        print('Using checked editorial backup for daily continuity.')
        return c
    raise RuntimeError('No safe distinct editorial backup available')


def render(c,folder,kind):
    if kind=='reel': return ride_photos.render_reel(c,folder,BASE)
    return ride_photos.render_post(c,folder,BASE)


def public_media(state,path):
    persist(state)
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    return 'https://raw.githubusercontent.com/michikazutsuruta-design/sashiosae-rescue-test/'+sha+'/'+path.relative_to(ROOT).as_posix()

def run(mode):
    load_threads_token()
    now=dt.datetime.now(dt.timezone.utc); channel=accounts()
    print('Both Ride accounts verified; no access to other brands.')
    if mode=='diagnose': return
    state=shared.load(STATE,{'journal':{},'history':[]})
    if mode=='build': refresh_threads_token(state,now)
    posts=shared.fetch_posts({'instagram':channel},now)
    shared.refresh_scheduled_links(posts,state,persist,{IG},now)
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
                if generated>=8: break
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

def catch_up_today():
    """User-authorized daily recovery. Unknown outcomes never retry."""
    load_threads_token()
    now=dt.datetime.now(dt.timezone.utc);day=now.astimezone(shared.JST).date()
    if now.astimezone(shared.JST).hour<22: return  # A delayed run must not post the next day early.
    channel=accounts();state=shared.load(STATE,{'journal':{},'history':[]})
    key=f'{day}/post/instagram'
    posts=shared.fetch_posts({'instagram':channel},now)
    regular=[p for p in posts if p.get('dueAt') and shared.parsed(p['dueAt']).astimezone(shared.JST).date()==day and p.get('status')!='draft' and (p.get('metadata') or {}).get('type') not in ('reel','story')]
    if len(regular)>1: raise RuntimeError('Multiple daily Instagram posts; reconcile first')
    entry=state['journal'].get(key)
    if regular:
        p=regular[0]
        if p['status']=='error': raise RuntimeError('Existing daily Instagram error; reconcile first')
        if entry and entry.get('id') and entry['id']!=p['id']: raise RuntimeError('Daily identity mismatch')
        entry=state['journal'].setdefault(key,{})
        entry.update(id=p['id'],status=p['status'],dueAt=p['dueAt'],url=p.get('externalLink'));persist(state)
    elif entry:
        raise RuntimeError('Existing daily journal needs reconciliation; no repost')
    else:
        folder=BASE/f'{day}-post';content=folder/'content.json'
        if content.exists(): c=shared.load(content,{})
        elif day==dt.date(2026,11,5):
            c={'title_en':'Room to feel at home','title_yue':'住得舒服嘅空間',
               'body_en':'Explore daylight, flow and storage in Tokyo homes. Visit our official website through the profile to discuss what matters to you.',
               'body_yue':'想搵住得舒服嘅東京屋企？由採光、動線同收納睇起。撳個人檔案入面嘅官方網站，同我哋傾吓你嘅需要。',
               'slides_en':['Natural light','Space that flows','Storage for everyday life','Explore our official website'],
               'slides_yue':['自然光入屋','行得順嘅動線','日常收納空間','撳個人檔案睇官方網站']}
        else: c=generate(day,'post',state['history'])
        caption=validate(c,[])+'\n\n'+ride_photos.DISCLAIMER_EN+'\n'+ride_photos.DISCLAIMER_YUE
        if len(caption)>500: raise ValueError('Caption exceeds Threads limit')
        folder.mkdir(parents=True,exist_ok=True);content.write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8')
        media=render(c,folder,'post');url=public_media(state,media);stamp=now+dt.timedelta(minutes=2)
        state['journal'][key]={'status':'attempting','dueAt':stamp.isoformat(),'media':url,'caption':caption};persist(state)
        result=shared.graphql(shared.CREATE,{'input':{'text':caption,'channelId':IG,'assets':[{'image':{'url':url}}],
            'schedulingType':'automatic','mode':'customScheduled','dueAt':stamp.isoformat(),'needsApproval':False,'aiAssisted':True,
            'metadata':{'instagram':{'type':'post','shouldShareToFeed':True,'isAiGenerated':False}}}})['createPost']
        p=result.get('post')
        if not p: raise RuntimeError('Recovery outcome requires reconciliation')
        state['journal'][key].update(id=p['id'],status=p['status'],url=p.get('externalLink'),visual_version=ride_photos.VERSION)
        state['history']=(state['history']+[c['body_en']])[-120:];persist(state)
    if not state['journal'][key].get('caption') or not state['journal'][key].get('media'):
        raise RuntimeError('Daily content missing; do not invent a second post')
    run('threads')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('mode',choices=['diagnose','build','threads','monitor','photos','catchup']); mode=p.parse_args().mode
    catch_up_today() if mode=='catchup' else run(mode)




