"""PC-independent content build, Buffer scheduling and publication monitoring.

Default disabled. No secrets in files. Unknown mutation outcomes are never retried.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, re, subprocess, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parents[1]
JST = dt.timezone(dt.timedelta(hours=9))
STATE = ROOT / 'social/state.json'
CONFIG = ROOT / 'social/config.json'
FOOTER = ('初回相談無料。プロフィールの公式サイトへ。フォームは24時間受付、担当者確認後に返信します。'
          '\n※相談だけで差押え・競売の手続は停止しません。期限や法的判断は通知元・専門家へ確認してください。'
          '\n個人番号・口座情報はコメントやDMに送らないでください。'
          '\n運営：株式会社ライドエステート\n#差押えレスキュー #不動産相談')

def load(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def write_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(STATE)

def persist(state):
    """Persist journal remotely BEFORE creating a post; serial workflow prevents races."""
    write_state(state)
    subprocess.run(['git', 'add', 'social/state.json', 'social/content', 'social/media'], cwd=ROOT, check=True)
    changed = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT)
    if changed.returncode == 1:
        subprocess.run(['git', 'commit', '-m', 'Update social publishing journal and media'], cwd=ROOT, check=True)
        subprocess.run(['git', 'push', 'origin', 'HEAD'], cwd=ROOT, check=True)
    elif changed.returncode != 0:
        raise RuntimeError('Cannot inspect journal changes')

def request_json(url, payload, token):
    req = Request(url, json.dumps(payload).encode(), {'Authorization': 'Bearer '+token,
                  'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(req, timeout=90) as response:
            return json.load(response)
    except HTTPError as error:
        # Do not log bodies/tokens, and do not retry 429 or uncertain mutations.
        raise RuntimeError(f'HTTP {error.code}; Retry-After={error.headers.get("Retry-After", "unknown")}') from None

def graphql(query, variables):
    body = request_json('https://api.buffer.com', {'query': query, 'variables': variables}, os.environ['BUFFER_API_KEY'])
    if body.get('errors'):
        raise RuntimeError('Buffer GraphQL request failed; check scopes/schema in a read-only diagnostic')
    return body['data']

CHANNEL = '''query Channel($input: ChannelInput!) {
 channel(input:$input) {id name service externalLink organizationId isDisconnected isQueuePaused isLocked}
}'''
POSTS = '''query Posts($input: PostsInput!, $after: String) {
 posts(input:$input, first:100, after:$after) {
 edges {node {id channelId text dueAt status externalLink metadata { ... on InstagramPostMetadata {type} }}}
 pageInfo {hasNextPage endCursor}
 }}'''
CREATE = '''mutation Create($input: CreatePostInput!) {
 createPost(input:$input) {
 ... on PostActionSuccess {post {id channelId dueAt status text externalLink}}
 ... on MutationError {message}
 }}'''

def channels(config):
    result = {}
    for service in ('instagram', 'threads'):
        channel = graphql(CHANNEL, {'input': {'id': config[service]}})['channel']
        if channel['name'].lstrip('@') != config['handle'] or channel['service'] != service:
            raise RuntimeError('Wrong posting account: stopped')
        if any(channel[k] for k in ('isDisconnected', 'isLocked', 'isQueuePaused')):
            raise RuntimeError('Channel requires attention: '+service)
        result[service] = channel
    return result

def fetch_posts(channel_map, now):
    organizations = {c['organizationId'] for c in channel_map.values()}
    if len(organizations) != 1:
        raise RuntimeError('Unexpected organizations')
    args = {'organizationId': next(iter(organizations)), 'filter': {
        'channelIds': [c['id'] for c in channel_map.values()],
        'startDate': (now-dt.timedelta(days=7)).isoformat()}}
    result, after = [], None
    for _ in range(10):
        page = graphql(POSTS, {'input': args, 'after': after})['posts']
        result.extend(e['node'] for e in (page['edges'] or []))
        if not page['pageInfo']['hasNextPage']:
            return result
        after = page['pageInfo']['endCursor']
    raise RuntimeError('Too many posts to reconcile safely')

def due(date, hour):
    return dt.datetime.combine(date, dt.time(hour), JST).astimezone(dt.timezone.utc)

def target_slots(now, horizon=3):
    slots = []
    for offset in range(1, horizon+1):
        day = now.astimezone(JST).date()+dt.timedelta(days=offset)
        slots.extend((day, 'post', service, due(day,21)) for service in ('instagram','threads'))
        if day.weekday() in (0,2,4):
            slots.append((day, 'reel', 'instagram', due(day,15)))
    return sorted(slots, key=lambda x:x[3])

def parsed(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')) if value else None

def same_slot(post, channel_id, timestamp):
    return (post['channelId']==channel_id and post.get('status')!='draft' and post.get('dueAt')
            and abs((parsed(post['dueAt'])-timestamp).total_seconds())<60)

def validate(content, history):
    if not isinstance(content, dict) or set(content) != {'title','body','slides'}:
        raise ValueError('Unexpected content structure')
    if not isinstance(content['title'],str) or not 2<=len(content['title'])<=18:
        raise ValueError('Title length')
    if not isinstance(content['body'],str) or not 30<=len(content['body'])<=170:
        raise ValueError('Body length')
    if not isinstance(content['slides'],list) or len(content['slides'])!=4:
        raise ValueError('Four slides required')
    if any(not isinstance(s,str) or not 2<=len(s)<=42 for s in content['slides']):
        raise ValueError('Slide length')
    text = content['title']+content['body']+''.join(content['slides'])
    # These posts are limited to organization/preparation, not legal/financial advice.
    if re.search(r'鶴田|[0-9０-９]|https?://|@|必ず|絶対|保証|借金.{0,5}(なく|消)|停止でき|高利回り|実績|口コミ|成功率|万円|利率|条文|法律では|裁判所は|債権者は|してください.{0,3}(口座|個人番号)',text):
        raise ValueError('Unapproved claim or sensitive-data instruction')
    if any(SequenceMatcher(None,content['body'],old).ratio()>.80 for old in history):
        raise ValueError('Content too similar to recent posts')
    return content

def generate(config, day, kind, history):
    topic = config['topics'][(day.toordinal()+(3 if kind=='reel' else 0))%len(config['topics'])]
    instruction = ('日本語の不動産相談窓口の短い投稿を作成。対象テーマの相談準備・情報整理だけを扱う。'
        '法律・制度・期限・費用・結果・金融判断を説明しない。恐怖訴求、架空事例、個人名、数値、URL、ハッシュタグは禁止。'
        '相談の効果や停止を約束しない。個人番号や口座情報、画像などの送信を求めない。'
        '本文は三十〜百七十文字、見出しは十八文字以下、スライド四枚は各四十二文字以下。'
        'JSONのみ返す: {"title":"短い見出し","body":"本文","slides":["一枚目","二枚目","三枚目","四枚目"]}。'
        '最後のスライドはプロフィールの公式サイトへの案内。フッターやCTAはプログラムが付ける。/no_think')
    url = f'https://api.cloudflare.com/client/v4/accounts/{os.environ["CLOUDFLARE_ACCOUNT_ID"]}/ai/run/{config["model"]}'
    raw = request_json(url, {'messages':[{'role':'system','content':instruction},
        {'role':'user','content':json.dumps({'theme':topic,'format':kind,'avoid':history[-12:]},ensure_ascii=False)}],
        'max_tokens':1800,'temperature':0.65}, os.environ['CLOUDFLARE_API_TOKEN'])
    if not raw.get('success',False):
        raise RuntimeError('AI generation failed; no paid fallback')
    result = raw['result']
    text = result.get('response') or result.get('choices',[{}])[0].get('message',{}).get('content','')
    text = re.sub(r'<think>.*?</think>','',text,flags=re.S).strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$','',text).strip()
    return validate(json.loads(text),history)

def wrapped(text, limit):
    return '\n'.join(text[i:i+limit] for i in range(0,len(text),limit))

def render(content, folder, kind):
    from PIL import Image, ImageDraw, ImageFont
    folder.mkdir(parents=True,exist_ok=True)
    font_path = os.environ.get('SOCIAL_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    height = 1920 if kind=='reel' else 1080
    def slide(text,index):
        im=Image.new('RGB',(1080,height),'#F7F3E9'); d=ImageDraw.Draw(im)
        d.rectangle((0,0,1080,180),fill='#142C46')
        def draw(t,size,y,color='#142C46',chars=16):
            f=ImageFont.truetype(font_path,size)
            d.multiline_text((70,y),wrapped(t,chars),font=f,fill=color,spacing=24)
        draw('差押えレスキュー',50,50,'white')
        draw(content['title'],52,240,chars=17)
        draw(text,48,480 if kind=='reel' else 440,chars=17)
        draw('初回相談無料',36,height-350)
        draw('プロフィールの公式サイトへ',30,height-280,chars=25)
        draw('株式会社ライドエステート｜RE/MAX RIDE',24,height-190,chars=40)
        path=folder/f'{index}.png'; im.save(path); return path
    if kind=='post':
        return slide(content['body'][:85],0)
    for i,text in enumerate(content['slides']): slide(text,i)
    playlist=folder/'concat.txt'
    playlist.write_text(''.join(f"file '{i}.png'\nduration 5\n" for i in range(4))+"file '3.png'\n")
    output=folder/'reel.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(playlist),
        '-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000','-t','20','-r','30',
        '-c:v','libx264','-preset','fast','-crf','25','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(output)],check=True)
    return output

def checkpoint_media(state,config,path):
    persist(state)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    url=f'https://raw.githubusercontent.com/{config["repository"]}/{commit}/{path.relative_to(ROOT).as_posix()}'
    # Fetch the public asset using the same URL Buffer will consume. Do not schedule until available.
    for attempt in range(6):
        try:
            with urlopen(Request(url,method='HEAD'),timeout=20) as r:
                if r.status==200: return url
        except HTTPError as e:
            if e.code!=404: raise
        time.sleep(5)
    raise RuntimeError('Media not publicly available yet; stopped before scheduling')

def run(monitor_only=False):
    config=load(CONFIG,{})
    if not config.get('enabled'):
        raise RuntimeError('Not activated: live connection approval and smoke test required')
    if not config.get('cloudflare_free_plan_confirmed'):
        raise RuntimeError('Cloudflare Free plan confirmation required; no paid fallback')
    now=dt.datetime.now(dt.timezone.utc)
    state=load(STATE,{'journal':{},'history':[]})
    for folder in ('content','media'): (ROOT/'social'/folder).mkdir(exist_ok=True)
    channel_map=channels(config)
    posts=fetch_posts(channel_map,now)
    # Refresh past reservations as well as upcoming slots during monitor-only runs.
    remote_by_id={p['id']:p for p in posts}
    for entry in state['journal'].values():
        remote=remote_by_id.get(entry.get('id'))
        if remote:
            entry.update(status=remote['status'],url=remote.get('externalLink'))
    problems=[]
    for post in posts:
        if post['status']=='error': problems.append(f'Buffer publishing error: {post["id"]}')
        if post.get('dueAt') and parsed(post['dueAt'])<now-dt.timedelta(minutes=45):
            if post['status'] in ('scheduled','sending'): problems.append(f'Publication overdue: {post["id"]}')
            if post['status']=='sent' and not post.get('externalLink'): problems.append(f'Public URL missing: {post["id"]}')
    counts={s:sum(p['channelId']==c['id'] and p['status'] in ('scheduled','sending') for p in posts) for s,c in channel_map.items()}
    generated=0
    if not monitor_only:
        for day,kind,service,timestamp in target_slots(now,config['lookahead_days']):
            key=f'{day}/{kind}/{service}'
            found=[p for p in posts if same_slot(p,config[service],timestamp)]
            if found:
                if len(found)>1: problems.append('Multiple posts at '+key)
                state['journal'][key]={'status':found[0]['status'],'id':found[0]['id'],'dueAt':timestamp.isoformat(),'url':found[0].get('externalLink')}
                continue
            previous=state['journal'].get(key)
            if previous:
                # Attempt was journaled before mutation. API may have accepted it despite a timeout.
                problems.append('Unresolved prior attempt; manual check required: '+key)
                continue
            if counts[service]>=config['queue_limit']:
                problems.append('Queue full: '+service); continue
            content_path=ROOT/'social/content'/f'{day}-{kind}.json'
            if content_path.exists():
                content=load(content_path,{})
            else:
                if generated>=2: continue  # Per-run hard limit; no infinite AI retries.
                content=generate(config,day,kind,state['history'])
                generated+=1
                content_path.write_text(json.dumps(content,ensure_ascii=False,indent=2),encoding='utf-8')
                state['history']=(state['history']+[content['body']])[-180:]
            folder=ROOT/'social/media'/f'{day}-{kind}'
            media=render(content,folder,kind)
            media_url=checkpoint_media(state,config,media)
            payload={'text':content['body']+'\n\n'+FOOTER,'channelId':config[service],
                'assets':[{('video' if kind=='reel' else 'image'):{'url':media_url}}],
                'schedulingType':'automatic','mode':'customScheduled','dueAt':timestamp.isoformat(),
                'needsApproval':False,'aiAssisted':True}
            if service=='instagram': payload['metadata']={'instagram':{'type':kind,'shouldShareToFeed':True,'isAiGenerated':True}}
            state['journal'][key]={'status':'attempting','dueAt':timestamp.isoformat()}
            persist(state)
            try:
                result=graphql(CREATE,{'input':payload})['createPost']
                if not result.get('post'): raise RuntimeError('Buffer rejected scheduling; inspect channel limits/scopes')
                post=result['post']; state['journal'][key]={'status':post['status'],'id':post['id'],'dueAt':post['dueAt'],'url':post.get('externalLink')}
                posts.append(post); counts[service]+=1
                persist(state)
            except Exception:
                # The remote 'attempting' record survives interruption; no blind reposting.
                problems.append('Scheduling outcome needs reconciliation: '+key)
                break
    state['last_check']=now.isoformat(); persist(state)
    if problems: raise RuntimeError('; '.join(problems))
    print('Publication status checked; missing upcoming slots processed.')

def diagnose():
    """Read-only Buffer check and one bounded AI generation; never schedule."""
    config=load(CONFIG,{})
    if not config.get('cloudflare_free_plan_confirmed'):
        raise RuntimeError('Free plan not confirmed')
    channel_map=channels(config)
    posts=fetch_posts(channel_map,dt.datetime.now(dt.timezone.utc))
    print('Verified channels:', ', '.join(channel_map))
    print('Remote post counts:', {s:sum(p['channelId']==c['id'] for p in posts) for s,c in channel_map.items()})
    content=generate(config,dt.datetime.now(JST).date()+dt.timedelta(days=3),'post',[])
    print('AI content validation passed:',content['title'])
    folder=ROOT/'social/media/diagnostic'
    media=render(content,folder,'post')
    print('Image rendering passed:',media.name)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--monitor-only',action='store_true'); parser.add_argument('--diagnose',action='store_true')
    args=parser.parse_args()
    if args.diagnose: diagnose()
    else: run(args.monitor_only)
