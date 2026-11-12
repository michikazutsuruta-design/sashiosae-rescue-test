"""Little Compass Japan only. Durable, fail-closed daily publishing journal."""
import argparse, base64, datetime as dt, hashlib, json, os, subprocess, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'social/lcj'
STATE=BASE/'state.json'
JST=dt.timezone(dt.timedelta(hours=9))
USERNAME='littlecompassjapan'
IG='17841427674452731'
REPO='michikazutsuruta-design/sashiosae-rescue-test'
FREE='https://littlecompassjapan.gumroad.com/l/japan-with-ease-free'

def load(path, default):
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else default

def persist(state):
    BASE.mkdir(parents=True,exist_ok=True)
    temp=STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(state,indent=2,ensure_ascii=False),encoding='utf8')
    temp.replace(STATE)
    if os.environ.get('GITHUB_ACTIONS')!='true': return
    subprocess.run(['git','add','social/lcj'],cwd=ROOT,check=True)
    if subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT).returncode==1:
        subprocess.run(['git','commit','-m','Update Little Compass publishing journal'],cwd=ROOT,check=True)
        subprocess.run(['git','pull','--rebase','origin','main'],cwd=ROOT,check=True)
        subprocess.run(['git','push','origin','HEAD'],cwd=ROOT,check=True)

def api(host,path,token,values=None):
    request=Request(host+path,None if values is None else urlencode(values).encode(),
                    {'Authorization':'Bearer '+token})
    try:
        with urlopen(request,timeout=60) as response: return json.load(response)
    except HTTPError as error:
        # Never print response bodies, URLs or credentials. Mutations are not retried.
        raise RuntimeError('Meta API HTTP '+str(error.code)+'; inspect before any retry') from None
    except OSError:
        raise RuntimeError('Meta API outcome unknown; inspect before any retry') from None

def graph(path,token,values=None): return api('https://graph.facebook.com/v27.0/',path,token,values)
def threads(path,token,values=None): return api('https://graph.threads.net/v1.0/',path,token,values)

def threads_token(state,now):
    from cryptography.fernet import Fernet
    root=os.environ['LCJ_THREADS_ACCESS_TOKEN']
    cipher=Fernet(base64.urlsafe_b64encode(hashlib.sha256(('lcj-cache-v1\0'+root).encode()).digest()))
    cache=Path(os.environ.get('RUNNER_TEMP',str(BASE)))/'lcj-token-cache/token.enc'
    token=root
    if cache.exists():
        try: token=json.loads(cipher.decrypt(cache.read_bytes()))['access_token']
        except Exception: raise RuntimeError('LCJ encrypted token cache invalid') from None
    last=state.get('token_refreshed_at')
    if last and now-dt.datetime.fromisoformat(last)<dt.timedelta(days=7): return token
    # Meta requires token age >=24 hours before refresh. Bootstrap records are seeded.
    query='refresh_access_token?'+urlencode({'grant_type':'th_refresh_token','access_token':token})
    refreshed=api('https://graph.threads.net/',query,token)
    candidate=refreshed['access_token']
    if threads('me?fields=id,username',candidate).get('username')!=USERNAME:
        raise RuntimeError('Refreshed token has wrong account')
    cache.parent.mkdir(parents=True,exist_ok=True)
    cache.write_bytes(cipher.encrypt(json.dumps({'access_token':candidate}).encode()))
    state['token_refreshed_at']=now.isoformat()
    state['token_expires_at']=(now+dt.timedelta(seconds=refreshed['expires_in'])).isoformat()
    persist(state)
    return candidate

def accounts(state,now):
    ig_token=os.environ['LCJ_INSTAGRAM_ACCESS_TOKEN']
    th_token=threads_token(state,now)
    ig=graph(IG+'?fields=id,username',ig_token)
    th=threads('me?fields=id,username',th_token)
    if ig.get('id')!=IG or ig.get('username')!=USERNAME or th.get('username')!=USERNAME:
        raise RuntimeError('Account mismatch; no publication permitted')
    state['checked_at']=now.isoformat()
    state['accounts']={'instagram':ig['id'],'threads':th['id'],'username':USERNAME}
    persist(state)
    return ig_token,th_token,th['id']

def due(now): return 14<=now.astimezone(JST).hour<24

def media(state):
    pack=load(BASE/'media-metadata.json',None)
    if not pack or len(pack)<3: raise RuntimeError('Need at least three licensed photo designs')
    for item in pack:
        if not item.get('source') or item.get('license')!='https://unsplash.com/license':
            raise RuntimeError('Missing licensed source')
        path=BASE/'media'/item['file']
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists(): path.write_bytes(base64.b64decode(item['base64'],validate=True))
    persist(state)
    return pack

def today_posts(platform,token,owner,day):
    if platform=='instagram':
        result=graph(owner+'/media?fields=id,caption,permalink,timestamp&limit=100',token)
    else:
        result=threads(owner+'/threads?fields=id,text,permalink,timestamp&limit=100',token)
    found=[]
    for post in result.get('data',[]):
        stamp=dt.datetime.fromisoformat(post['timestamp'].replace('Z','+00:00'))
        if stamp.astimezone(JST).date()==day: found.append(post)
    # If a page is full, fail closed rather than assert absence from a partial page.
    if len(result.get('data',[]))>=100 and not found:
        raise RuntimeError('Remote post history incomplete; inspect before publishing')
    return found

def caption(topic,platform,day):
    if platform=='threads':
        value=topic['short']+'\n\nFree 12-page Japan With Ease preview: '+FREE+'?utm_source=threads&utm_medium=social&utm_campaign=daily&utm_content='+day.isoformat()
        if len(value)>500: raise ValueError('Threads caption too long')
        return value
    return topic['body']+'\n\nJapan With Ease: an English PDF companion for your first Japan trip. Japanese + romaji + English, practical checklists and planning pages. Free 12-page preview through the profile link.\n\n#JapanTravel #FirstTimeInJapan #LittleCompassJapan'

def publish_one(platform,token,owner,state,now,topics,pack):
    day=now.astimezone(JST).date(); key=day.isoformat()+'/'+platform
    entry=state.setdefault('journal',{}).get(key)
    if entry and entry.get('status') in ('sent','observed_external'): return
    observed=today_posts(platform,token,owner,day)
    if observed:
        state['journal'][key]={'status':'observed_external','ids':[p['id'] for p in observed],
            'urls':[p.get('permalink') for p in observed],'checked_at':now.isoformat()}
        persist(state);return
    if entry and entry.get('status') in ('preparing','publishing','unknown'):
        raise RuntimeError('Uncertain previous mutation for '+key+'; no resend')
    if entry is None:
        sent=[v for k,v in sorted(state['journal'].items()) if k.endswith('/'+platform) and v.get('photo')]
        previous=sent[-1].get('photo') if sent else None
        offset=(day-dt.date(2026,11,12)).days
        topic=topics[offset%len(topics)]
        photo=pack[offset%len(pack)]
        if photo['file']==previous: photo=next(p for p in pack if p['file']!=previous)
        url=f'https://raw.githubusercontent.com/{REPO}/main/social/lcj/media/'+photo['file']
        with urlopen(url,timeout=60) as response:
            if response.status!=200 or not response.headers.get('Content-Type','').startswith('image/'):
                raise RuntimeError('Public media unavailable')
        text=caption(topic,platform,day)
        entry={'status':'preparing','text':text,'photo':photo['file'],'source':photo['source'],
               'started_at':now.isoformat()}
        state['journal'][key]=entry;persist(state)
        try:
            if platform=='instagram': created=graph(owner+'/media',token,{'image_url':url,'caption':text})
            else: created=threads(owner+'/threads',token,{'media_type':'IMAGE','image_url':url,'text':text})
        except Exception:
            entry['status']='unknown';persist(state);raise
        entry.update(status='ready',container=created['id']);persist(state)
    container=entry['container']
    if platform=='instagram': status=graph(container+'?fields=status_code',token).get('status_code')
    else: status=threads(container+'?fields=status',token).get('status')
    if status=='IN_PROGRESS': print(platform+' media processing; later run will check');return
    if status!='FINISHED': raise RuntimeError(platform+' container not publishable: '+str(status))
    entry['status']='publishing';persist(state)
    try:
        if platform=='instagram': result=graph(owner+'/media_publish',token,{'creation_id':container})
        else: result=threads(owner+'/threads_publish',token,{'creation_id':container})
    except Exception:
        entry['status']='unknown';persist(state);raise
    entry.update(status='sent',id=result['id'],sent_at=now.isoformat());persist(state)
    if platform=='instagram': detail=graph(result['id']+'?fields=id,permalink',token)
    else: detail=threads(result['id']+'?fields=id,permalink',token)
    entry['url']=detail.get('permalink');persist(state)
    print(platform+' published: '+str(entry['url']))

def main(operation):
    now=dt.datetime.now(dt.timezone.utc)
    state=load(STATE,{'journal':{}})
    tokens=accounts(state,now)
    pack=media(state)
    topics=load(BASE/'topics.json',[])
    if len(topics)<14: raise RuntimeError('Insufficient reviewed topic library')
    for topic in topics:
        for platform in ('instagram','threads'): caption(topic,platform,now.astimezone(JST).date())
    if operation=='diagnose':
        for platform,token,owner in [('instagram',tokens[0],IG),('threads',tokens[1],tokens[2])]:
            posts=today_posts(platform,token,owner,now.astimezone(JST).date())
            print(platform+' account verified; today posts='+str(len(posts)))
        print('LCJ connection/content checks passed. No posts created.');return
    if operation=='monitor':
        print('Account checks passed; monitor only.');return
    config=load(BASE/'config.json',{})
    if not config.get('enabled') or not due(now):
        print('Publishing disabled or outside same-day 14:00-23:59 JST window.');return
    errors=[]
    for platform,token,owner in [('instagram',tokens[0],IG),('threads',tokens[1],tokens[2])]:
        try: publish_one(platform,token,owner,state,now,topics,pack)
        except Exception as error: errors.append(platform+': '+str(error))
    if errors: raise RuntimeError('; '.join(errors))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['diagnose','publish','monitor'])
    try: main(parser.parse_args().operation)
    except Exception as error:
        print(type(error).__name__+': '+str(error));raise SystemExit(1)
