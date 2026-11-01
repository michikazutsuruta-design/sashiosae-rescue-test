"""Ride only: Buffer Instagram + official Threads, with durable no-retry journals."""
import datetime as dt, json, os, re, subprocess, time, textwrap
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
from difflib import SequenceMatcher
import runner as shared

ROOT = shared.ROOT
BASE = ROOT/'social/ride'
STATE = BASE/'state.json'
IG = '6ae6e299e29c69a1712300e4'
START = dt.date(2026,11,4)  # Existing Publer reservations own Nov 1-3.
SITE = 'https://www.remaxride-tsuruta.com/'
TOPICS = ['daily transport and local amenities','preparing viewing questions','comparing home layouts',
          'organizing a remote enquiry','clarifying intended home use','preparing an area shortlist',
          'considering everyday storage needs','planning a viewing itinerary','noting daylight preferences']

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
    caption=c['body_en']+'\n\n'+c['body_yue']+'\n\nRE/MAX RIDE | Ride Estate Co., Ltd.\n'+SITE
    if len(caption)>500: raise ValueError('Threads caption too long')
    if any(SequenceMatcher(None,c['body_en'],s).ratio()>.82 for s in history): raise ValueError('Repeated copy')
    return caption

def generate(day,kind,history):
    fields={'title_en':48,'title_yue':22,'body_en':155,'body_yue':100}
    props={k:{'type':'string','minLength':3,'maxLength':n} for k,n in fields.items()}
    props.update({k:{'type':'array','minItems':4,'maxItems':4,'items':{'type':'string','minLength':2,'maxLength':60}} for k in ('slides_en','slides_yue')})
    instruction=('Write original RE/MAX RIDE home-search preparation content. English AND natural Hong Kong Cantonese written with Traditional characters, not Mandarin. '
        'Only general organization, everyday home preferences and viewing questions. No laws, tax, finance, investment advice, numerical claims, listings, prices, availability, achievements or guarantees. '
        'No personal names, URLs, hashtags or digits. English body max155 characters and Cantonese body max100 characters. '
        'The body_yue must use spoken Cantonese words such as 嘅, 睇, 撳; avoid Mandarin expressions 的, 看房, 請瀏覽. '
        'Example Cantonese style: 睇樓之前，寫低你重視嘅條件。撳個人檔案入面嘅官方網站。 '
        'Use exactly these keys: title_en, title_yue, body_en, body_yue, slides_en, slides_yue. '
        'slides_en and slides_yue must each contain four strings of at most sixty characters. title_en max forty eight characters, title_yue max twenty two. '
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
    from PIL import Image,ImageDraw,ImageFont
    folder.mkdir(parents=True,exist_ok=True)
    h=1920 if kind=='reel' else 1350
    font=os.environ.get('SOCIAL_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    def make(i,en,yue):
        im=Image.new('RGB',(1080,h),'#F4F0E7'); d=ImageDraw.Draw(im)
        d.rectangle((0,0,1080,180),fill='#173249')
        def draw(text,size,y,color='#173249',width=26):
            f=ImageFont.truetype(font,size)
            d.multiline_text((72,y),'\n'.join(textwrap.wrap(text,width=width,break_long_words=True)),font=f,fill=color,spacing=22)
        draw('RE/MAX RIDE',58,45,'white')
        draw(c['title_en'],45,245,width=32)
        draw(c['title_yue'],48,430,width=18)
        draw(en,40,650 if kind=='reel' else 610,width=35)
        draw(yue,44,1000 if kind=='reel' else 880,width=19)
        draw('Official website in profile',28,h-220,width=45)
        draw('撳個人檔案入面嘅官方網站',30,h-155,width=28)
        draw('Ride Estate Co., Ltd.',24,h-80,width=45)
        path=folder/f'{i}.png'; im.save(path); return path
    if kind=='post': return make(0,c['slides_en'][0],c['slides_yue'][0])
    for i in range(4): make(i,c['slides_en'][i],c['slides_yue'][i])
    (folder/'concat.txt').write_text(''.join(f"file '{i}.png'\nduration 5\n" for i in range(4))+"file '3.png'\n")
    target=folder/'reel.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(folder/'concat.txt'),'-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000','-t','20','-r','30','-c:v','libx264','-preset','fast','-crf','25','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(target)],check=True)
    return target

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
            caption=validate(c,[]); media=render(c,folder,kind); url=public_media(state,media)
            state['journal'][key]={'status':'attempting','dueAt':stamp.isoformat()}; persist(state)
            payload={'text':caption,'channelId':IG,'assets':[{('video' if kind=='reel' else 'image'):{'url':url}}],
                     'schedulingType':'automatic','mode':'customScheduled','dueAt':stamp.isoformat(),'needsApproval':False,'aiAssisted':True,
                     'metadata':{'instagram':{'type':kind,'shouldShareToFeed':True,'isAiGenerated':True}}}
            result=shared.graphql(shared.CREATE,{'input':payload})['createPost']
            if not result.get('post'): raise RuntimeError('Instagram scheduling requires reconciliation')
            p=result['post']; state['journal'][key]={'id':p['id'],'status':p['status'],'dueAt':p['dueAt'],'media':url,'caption':caption}; count+=1; posts.append(p); persist(state)
    if mode=='threads':
        day=now.astimezone(shared.JST).date(); stamp=shared.due(day,22)
        if day<START or not stamp<=now<=stamp+dt.timedelta(hours=2): return
        key=f'{day}/post/threads'
        if key in state['journal']: return  # Unknown attempts stop; never retry blindly.
        source=state['journal'].get(f'{day}/post/instagram',{})
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
        if entry.get('status') in ('error','attempting'): raise RuntimeError('Check unresolved Ride entry '+key)
        if stamp and stamp<now-dt.timedelta(minutes=45) and entry.get('status') in ('scheduled','sending'):
            raise RuntimeError('Ride publication overdue '+key)
    persist(state)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('mode',choices=['diagnose','build','threads','monitor']); run(p.parse_args().mode)
