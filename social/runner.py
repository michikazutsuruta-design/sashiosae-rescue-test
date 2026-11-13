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
          '\n運営：株式会社ライドエステート\n#差押えレスキュー #不動産相談'
          '\nhttps://tinyurl.com/SASHIOSAERESCUE')

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
        'status': ['scheduled','sending','sent','error','draft']}}
    result, after = [], None
    for _ in range(10):
        page = graphql(POSTS, {'input': args, 'after': after})['posts']
        result.extend(e['node'] for e in (page['edges'] or []))
        if not page['pageInfo']['hasNextPage']:
            return result
        after = page['pageInfo']['endCursor']
    raise RuntimeError('Too many posts to reconcile safely')

def normalized_site_links(text):
    # Public destinations approved by the owner; do not modify other links.
    return re.sub(r'https://www\.remaxride-tsuruta\.com(?:/en)?/?',
                  'https://tinyurl.com/RIDEESTATE',text).replace(
        'https://michikazutsuruta-design.github.io/sashiosae-rescue-test/',
        'https://tinyurl.com/SASHIOSAERESCUE')

def refresh_scheduled_links(posts,state,save,channel_ids,now):
    entries={v.get('id'):v for v in state['journal'].values() if v.get('id')}
    for post in posts:
        if post['channelId'] not in channel_ids or post['id'] not in entries: continue
        entry=entries[post['id']]
        if entry.get('link_update')=='attempting':
            approved=('https://tinyurl.com/RIDEESTATE' if post['channelId']=='6ae6e299e29c69a1712300e4' else 'https://tinyurl.com/SASHIOSAERESCUE')
            if post['status']=='scheduled' and approved in post['text'] and normalized_site_links(post['text'])==post['text']:
                entry['link_update']='complete'
                if 'caption' in entry:entry['caption']=post['text']
                save(state)
            else:
                raise RuntimeError('Uncertain link update requires reconciliation: '+post['id'])
        if post['status']!='scheduled': continue
        stamp=parsed(post.get('dueAt'))
        if not stamp or stamp<=now+dt.timedelta(minutes=10): continue
        text=normalized_site_links(post['text'])
        if text==post['text']:
            if 'caption' in entry and entry['caption']!=text:
                entry['caption']=text;save(state)
            continue
        entry['link_update']='attempting';save(state)
        query='''mutation Edit($input: EditPostInput!) { editPost(input:$input) {
          ... on PostActionSuccess { post { id channelId dueAt status text externalLink } }
          ... on MutationError { message } } }'''
        result=graphql(query,{'input':{'id':post['id'],'text':text}})['editPost']
        updated=result.get('post')
        if not updated or updated['id']!=post['id'] or updated['channelId']!=post['channelId'] or parsed(updated['dueAt'])!=stamp or updated['text']!=text:
            raise RuntimeError('Link edit outcome requires reconciliation: '+post['id'])
        post.update(updated)
        if 'caption' in entry:entry['caption']=text
        entry['link_update']='complete';save(state)
        print('Updated scheduled website link; post identity and time preserved.')

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

def _generate_once(config, day, kind, history):
    topic = config['topics'][(day.toordinal()+(3 if kind=='reel' else 0))%len(config['topics'])]
    instruction = ('日本語の不動産相談窓口の短い投稿を作成。対象テーマの相談準備・情報整理だけを扱う。'
        '法律・制度・期限・費用・結果・金融判断を説明しない。恐怖訴求、架空事例、個人名、数値、URL、ハッシュタグは禁止。'
        '相談の効果や停止を約束しない。個人番号や口座情報、画像などの送信を求めない。'
        '本文は三十〜百七十文字、見出しは十八文字以下で、相談者に語りかける短く自然な言葉にする。事務作業名の羅列は禁止。例：家のこと、一人で抱えない。届いた通知、まずは整理。、スライド四枚は各四十二文字以下。'
        'JSONのみ返す: {"title":"短い見出し","body":"本文","slides":["一枚目","二枚目","三枚目","四枚目"]}。'
        '最後のスライドはプロフィールの公式サイトへの案内。フッターやCTAはプログラムが付ける。/no_think')
    url = f'https://api.cloudflare.com/client/v4/accounts/{os.environ["CLOUDFLARE_ACCOUNT_ID"]}/ai/run/{config["model"]}'
    schema={'type':'object','additionalProperties':False,'properties':{
        'title':{'type':'string','minLength':2,'maxLength':18},
        'body':{'type':'string','minLength':30,'maxLength':170},
        'slides':{'type':'array','minItems':4,'maxItems':4,'items':{'type':'string','minLength':2,'maxLength':42}}},
        'required':['title','body','slides']}
    raw = request_json(url, {'messages':[{'role':'system','content':instruction},
        {'role':'user','content':json.dumps({'theme':topic,'format':kind,'avoid':history[-12:]},ensure_ascii=False)}],
        'max_tokens':1800,'temperature':0.65,'response_format':{'type':'json_schema','json_schema':schema}}, os.environ['CLOUDFLARE_API_TOKEN'])
    if not raw.get('success',False):
        raise RuntimeError('AI generation failed; no paid fallback')
    result = raw['result']
    text = result.get('response') or result.get('choices',[{}])[0].get('message',{}).get('content','')
    if isinstance(text,dict):
        return validate(text,history)
    text = re.sub(r'<think>.*?</think>','',text,flags=re.S).strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$','',text).strip()
    return validate(json.loads(text),history)


# Prewritten preparation-only copy goes through exactly the same checks as AI copy.
SAFE_DRAFTS = (
    ('相談前の気持ちをメモ', '住まいのことで気になっていることを、今の言葉で書き留めてみましょう。うまく説明できない気持ちも、そのまま相談の入口になります。'),
    ('空き家の今を振り返る', '空き家について相談する前に、普段どのように使われているかを思い出してみましょう。気がかりな場所や、これから話したいことを手元のメモに残せます。'),
    ('聞きたいことを手元に', '相談の途中で聞きたかったことを忘れないように、質問を手元に置いておくと安心です。答えが分からない項目は、無理に埋めずに残しておきましょう。'),
    ('家族と話した内容は', '住まいについて家族と話した内容を振り返ってみませんか。それぞれが気にしていることや、まだ話し合えていないことを分けてメモすると、相談で伝える内容を考えやすくなります。'),
    ('書類の置き場所から', '届いた書類がどこにあるか、落ち着いて確かめてみましょう。見つかった書類と探している書類を手元で分けておくと、相談前の確認を進めやすくなります。'),
    ('相談で伝えたい希望', 'これからの暮らしで大切にしたいことを考えてみませんか。住まいの悩みと合わせて、ご自身の希望も手元に書き留めておくと、相談で話したいことが見えてきます。'),
    ('分からないままでも', '何が分からないのかを言葉にするのが難しいときもあります。今ひっかかっている言葉や、気持ちを短くメモするところから、相談の準備を始めてみましょう。'),
    ('話したい順番を考える', '相談で最初に話したいことは何でしょうか。気になっていることを書き出した後、先に聞いておきたい内容に目印を付けると、ご自身の考えを振り返れます。'),
)

def generate(config, day, kind, history):
    for _ in range(2):
        try:
            return _generate_once(config, day, kind, history)
        except (ValueError, TypeError, KeyError):
            print('Draft did not pass checks; trying another safe draft.')
    start = (day.toordinal() + (3 if kind == 'reel' else 0)) % len(SAFE_DRAFTS)
    for offset in range(len(SAFE_DRAFTS)):
        title, body = SAFE_DRAFTS[(start + offset) % len(SAFE_DRAFTS)]
        draft = {'title': title, 'body': body,
                 'slides': [title, '分かるところからメモ', '聞きたいことを整理', 'プロフィールの公式サイトへ']}
        try:
            checked = validate(draft, history)
        except ValueError:
            continue
        print('Using validated preparation-only fallback copy.')
        return checked
    raise ValueError('No distinct validated copy available; no post scheduled')

def wrapped(text, limit):
    return '\n'.join(text[i:i+limit] for i in range(0,len(text),limit))

VISUAL_VERSION = 'editorial-scenes-v4'

def render(content, folder, kind):
    """Original scene illustrations, chosen by topic; no stock or customer data."""
    from PIL import Image, ImageDraw, ImageFont
    # Keep the existing asset prerequisite: configuration errors must stop output.
    if not (ROOT/'social/assets/house-illustration-v2.png').exists():
        raise RuntimeError('Housing illustration missing; no text-card fallback')
    folder.mkdir(parents=True, exist_ok=True)
    font_path = os.environ.get('SOCIAL_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    match = re.match(r'(\d{4}-\d{2}-\d{2})', folder.name)
    day = dt.date.fromisoformat(match[1]) if match else dt.date(2026,11,4)
    text = content['title'] + content.get('body','')
    if any(w in text for w in ('空き家','相続')): topic = 0
    elif any(w in text for w in ('通知','書類','差出人')): topic = 1
    elif any(w in text for w in ('家族','話し合')): topic = 2
    elif any(w in text for w in ('質問','聞きたい','不明')): topic = 3
    elif any(w in text for w in ('フォーム','相談先')): topic = 4
    else: topic = 5
    palettes = [('#103e48','#225b60','#f0d7a6','#e9b565'),
                ('#243652','#405577','#eedac0','#edaa81'),
                ('#3d3850','#625371','#f0dcc4','#d3ac80'),
                ('#164b51','#367574','#e3d7b2','#efba62'),
                ('#223a56','#416b88','#e9dabd','#79bdbc'),
                ('#4b3a39','#80645b','#f0d5b0','#dba474')]
    height = 1920 if kind == 'reel' else 1350
    def font(size): return ImageFont.truetype(font_path,size)
    def frame(headline, index):
        scene = (topic+index) % 6
        bg, light, paper, accent = palettes[(topic+day.toordinal()+index)%6]
        im = Image.new('RGB',(1080,height),bg)
        d = ImageDraw.Draw(im)
        # A subtle full-bleed gradient, rather than a white text card.
        import math
        a=tuple(bytes.fromhex(bg[1:])); b=tuple(bytes.fromhex(light[1:]))
        for y in range(height):
            mix=(y/height)*0.8
            d.line((0,y,1080,y),fill=tuple(int(a[k]*(1-mix)+b[k]*mix) for k in range(3)))
        from PIL import ImageChops
        glow=ImageChops.invert(Image.radial_gradient('L').resize(im.size)).point(lambda v:int(v*.60))
        im=Image.composite(Image.new('RGB',im.size,accent),im,glow)
        im=Image.blend(im,Image.effect_noise(im.size,18).convert('RGB'),0.035)
        d=ImageDraw.Draw(im)
        d.text((66,48),'差押えレスキュー',font=font(38),fill=paper)
        labels=['住まいのこれから','届いた書類','家族と話す','聞きたいこと','相談の入口','相談前のメモ']
        d.text((68,112),labels[topic],font=font(27),fill=accent)
        rows=[]; line=''
        for ch in headline.replace('、','、\n').replace('。','。\n'):
            if ch=='\n' or (line and d.textlength(line+ch,font=font(91))>944):
                rows.append(line); line='' if ch=='\n' else ch
            else: line+=ch
        if line: rows.append(line)
        if len(rows)>3:
            rows=[]; line=''
            for ch in headline:
                if line and d.textlength(line+ch,font=font(73))>944: rows.append(line);line=ch
                else: line+=ch
            if line: rows.append(line)
            size=73
        else: size=91
        if len(rows)>4: raise ValueError('Headline too long')
        for n,row in enumerate(rows): d.text((64,171+n*(size+14)),row,font=font(size),fill=paper)
        # Illustration canvas: individual objects and human scenes occupy most of lower image.
        art=Image.new('RGBA',(1080,740)); p=ImageDraw.Draw(art)
        ink='#182d3b'; shadow='#102d39'; cream=paper
        def rr(box, fill, radius=20, outline=None,width=3):
            p.rounded_rectangle(box,radius,fill=fill,outline=outline,width=width)
        def sheet(x,y,w=270,h=330):
            rr((x+12,y+14,x+w+12,y+h+14),shadow)
            rr((x,y,x+w,y+h),cream,12)
            for z in range(4): p.line((x+30,y+75+z*42,x+w-34,y+75+z*42),fill='#b5ad9b',width=9)
            p.rectangle((x+30,y+25,x+112,y+39),fill=accent)
        def plant(x,y):
            p.line((x,y,x,y-185),fill='#a6b5a0',width=9)
            for j in range(3):
                p.ellipse((x-75,y-200+j*45,x+5,y-140+j*45),fill='#83a59c')
                p.ellipse((x,y-230+j*45,x+75,y-170+j*45),fill='#aac3ae')
            p.polygon([(x-70,y-15),(x+70,y-15),(x+48,y+120),(x-48,y+120)],fill=accent)
        def person(x,y,shirt,flip=False):
            # Stylised people are illustrative, never presented as customers.
            p.ellipse((x-48,y-105,x+48,y-9),fill='#dbac8b')
            p.pieslice((x-51,y-119,x+51,y-26),180,360,fill=ink)
            rr((x-82,y,x+82,y+225),shirt,50)
            p.line((x-55,y+65,x+(-130 if flip else 130),y+167),fill='#dbac8b',width=36)
        p.ellipse((65,588,1020,725),fill=shadow)
        if scene==0:
            # A house with a path and garden, viewed as a whole, not the old background.
            p.ellipse((44,10,782,641),fill=light)
            p.polygon([(140,330),(485,75),(825,330)],fill=accent)
            rr((202,320,765,635),cream,6)
            p.rectangle((438,413,540,635),fill=ink)
            for x in (259,593):
                rr((x,385,x+105,490),light,4)
                p.line((x+52,385,x+52,490),fill=cream,width=7)
                p.line((x,438,x+105,438),fill=cream,width=7)
            p.polygon([(438,635),(540,635),(680,740),(300,740)],fill='#b4b9aa')
            plant(895,501)
        elif scene==1:
            # Letters, a dated blank document and a folder; no invented official notice.
            rr((80,220,1000,660),'#896d59',55)
            rr((115,281,533,622),accent,14)
            sheet(394,77,355,429)
            p.polygon([(155,410),(585,410),(585,651),(155,651)],fill='#e5c69b')
            p.line([(155,410),(370,550),(585,410)],fill='#b28a67',width=6)
            p.ellipse((816,405,966,555),fill=cream)
            p.ellipse((842,431,940,529),fill='#66554a')
        elif scene==2:
            # Two people at a shared table.
            p.ellipse((132,30,950,680),fill=light)
            person(310,290,accent)
            person(760,290,'#98b5b1',True)
            rr((135,465,945,537),'#b68e70',25)
            p.rectangle((200,534,228,716),fill='#b68e70')
            p.rectangle((850,534,878,716),fill='#b68e70')
            rr((421,405,647,471),cream,6)
            p.ellipse((153,36,469,184),fill=cream)
            p.polygon([(290,175),(342,175),(312,220)],fill=cream)
            for x in (229,300,371):p.ellipse((x,93,x+18,111),fill=light)
        elif scene==3:
            # Question notebook with tangible check marks and pencil.
            rr((190,100,899,661),accent,28)
            rr((165,73,510,631),cream,12)
            rr((517,73,864,631),'#dfcfb2',12)
            for y in (210,310,410,510):
                rr((219,y,261,y+42),None,5,light,5)
                p.line([(225,y+20),(238,y+31),(258,y+8)],fill=light,width=6)
                p.line((289,y+22,447,y+22),fill='#aaab9c',width=9)
                p.line((562,y+22,797,y+22),fill='#b0a797',width=9)
            p.line((735,135,943,583),fill=accent,width=30)
            p.polygon([(931,579),(957,568),(958,629)],fill=cream)
        elif scene==4:
            # Phone with a simple fictional form, no names/contact details.
            plant(197,455)
            rr((355,50,746,685),ink,45)
            rr((375,76,726,657),cream,29)
            rr((479,89,623,105),ink,9)
            for y in (202,303,404):rr((411,y,688,y+65),'#c1cbbf',10)
            rr((411,526,688,595),light,18)
            p.line([(511,558),(535,579),(587,542)],fill=cream,width=12)
            p.ellipse((775,171,985,381),fill=accent)
            p.line([(823,276),(862,315),(941,229)],fill=ink,width=15)
        else:
            # A quiet writing desk, warm light, notebook and pencil.
            rr((78,326,1010,678),'#9a7a62',40)
            sheet(242,209,356,381)
            p.line((644,252,807,564),fill=accent,width=26)
            p.polygon([(794,563),(819,551),(826,604)],fill=cream)
            p.line((834,327,878,126),fill=cream,width=18)
            p.polygon([(775,154),(937,154),(904,58),(818,58)],fill=accent)
            p.ellipse((781,327,956,357),fill=cream)
            plant(127,214)
        # Alternating mirrored composition keeps objects apart from a fixed headline.
        if scene in (0,1,5) and (day.toordinal()+index)%2:
            art=art.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        art_y=height-900
        if kind=='reel':
            art=art.resize((1080,900),Image.Resampling.LANCZOS); art_y=height-1080
        im.paste(art,(0,art_y),art)
        d=ImageDraw.Draw(im)
        d.rounded_rectangle((64,height-167,1016,height-70),radius=27,fill=accent)
        cta='プロフィールの公式サイトへ'
        d.text(((1080-d.textlength(cta,font=font(43)))/2,height-148),cta,font=font(43),fill=ink)
        d.text((69,height-52),'場面・人物・住宅はイメージイラスト',font=font(24),fill=paper)
        path=folder/f'{index}.png'; im.save(path)
        return path
    if kind=='post':return frame(content['title'],0)
    cards=[content['title']]+content.get('slides',[])[1:4]
    if len(cards)!=4:raise ValueError('Four reel scenes required')
    for i,headline in enumerate(cards):frame(headline,i)
    playlist=folder/'concat.txt'
    playlist.write_text(''.join(f"file '{i}.png'\nduration 4\n" for i in range(4))+"file '3.png'\n")
    output=folder/'reel.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(playlist),
        '-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000','-t','16','-r','30',
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
    refresh_scheduled_links(posts,state,persist,{c['id'] for c in channel_map.values()},now)
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
                state['journal'][key]={**state['journal'].get(key,{}),'status':found[0]['status'],'id':found[0]['id'],'dueAt':timestamp.isoformat(),'url':found[0].get('externalLink')}
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



