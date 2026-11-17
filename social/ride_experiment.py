"""Finite, owner-approved single-property experiment. Fail closed on unknown writes."""
import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

VERSION='single-property-experiment-v1'
EDIT='''mutation Edit($input:EditPostInput!){editPost(input:$input){... on PostActionSuccess{post{id channelId status dueAt text externalLink}} ... on MutationError{message}}}'''
DELETE='''mutation Delete($input:DeletePostInput!){deletePost(input:$input){__typename}}'''

def load_plan(base):
    return json.loads((base/'experiment-plan.json').read_text(encoding='utf8'))

def applies(base,now,mode):
    if not (base/'experiment-plan.json').exists(): return False
    plan=load_plan(base);day=now.astimezone(dt.timezone(dt.timedelta(hours=9))).date()
    start=dt.date.fromisoformat(plan['start']);end=dt.date.fromisoformat(plan['end'])
    return start<=day<=end or (mode in ('build','photos','diagnose','monitor') and day==start-dt.timedelta(days=1))

def check_plan(plan,catalog):
    days=plan['days'];start=dt.date.fromisoformat(plan['start'])
    if len(days)!=14 or sum(v['format']=='reel' for v in days.values())!=10: raise ValueError('Expected ten reels and four carousels')
    previous=None
    for n in range(14):
        item=days[str(start+dt.timedelta(days=n))];p=catalog[item['property_id']]
        if p['id']==previous: raise ValueError('Adjacent property repetition')
        previous=p['id']
        if len(item['caption'])>500 or 'https://tinyurl.com/RIDEESTATE' not in item['caption']: raise ValueError('Caption/link')
        if p['availability']!='unconfirmed' or p['price_used']: raise ValueError('Unexpected sales claim')
        if len(p['photos'])<4: raise ValueError('Insufficient confirmed same-property photographs')

def picture(source,target,title,subtitle,property_name,size):
    w,h=size
    fontpath=os.environ.get('SOCIAL_FONT','C:/Windows/Fonts/YuGothM.ttc')
    font=lambda n:ImageFont.truetype(fontpath,n)
    with Image.open(source) as original:
        canvas=ImageOps.fit(original.convert('RGB'),size,method=Image.Resampling.LANCZOS).convert('RGBA')
    overlay=Image.new('RGBA',size);d=ImageDraw.Draw(overlay)
    for y in range(int(h*.57),h): d.line((0,y,w,y),fill=(7,16,22,int(240*(y-h*.57)/(h*.43))))
    canvas=Image.alpha_composite(canvas,overlay);d=ImageDraw.Draw(canvas)
    d.text((55,45),'RE/MAX RIDE | TOKYO',font=font(26),fill='white',stroke_width=1,stroke_fill='#17232d')
    def wrapped(text,maxwidth,f):
        lines=[];line=''
        for word in text.split():
            if f.getlength((line+' '+word).strip())>maxwidth and line:lines.append(line);line=word
            else:line=(line+' '+word).strip()
        if line:lines.append(line)
        return '\n'.join(lines)
    y=int(h*.70)
    d.multiline_text((55,y),wrapped(title,970,font(57)),font=font(57),fill='white',spacing=8)
    d.text((58,h-230),subtitle,font=font(35),fill='#e2c78a')
    d.multiline_text((58,h-165),wrapped(property_name,960,font(25)),font=font(25),fill='white',spacing=3)
    d.text((58,h-58),'Property photos | Details & availability on enquiry',font=font(20),fill='#dedede')
    canvas.convert('RGB').save(target,quality=92)

def prepare(base,day,item,p):
    folder=base/'experiment-media'/day;folder.mkdir(parents=True,exist_ok=True)
    # Every photograph in a publication belongs to this catalogued property.
    # Different properties on adjacent days avoid adjacent photo reuse in the experiment.
    selected=[p['photos'][(item.get('photo_offset',0)+i)%len(p['photos'])] for i in range(4)]
    photos=[base/photo['file'] for photo in selected]
    titles=[item['title_en'],p.get('facts') or p['region'].title(),'Ask for the floor plan','See the property details']
    yues=[item['title_yue'],'睇吓周邊同室內','記得確認平面圖','撳個人檔案睇網站']
    frames=[]
    for i in range(4):
        target=folder/f'{i}.jpg'
        picture(photos[i],target,titles[i],yues[i],p['name'],(1080,1920) if item['format']=='reel' else (1080,1350));frames.append(target)
    poster=folder/'poster.jpg'
    picture(photos[0],poster,item['title_en'],item['title_yue'],p['name'],(1080,1350))
    visual={'version':VERSION,'scheduled_for':day,'property_id':p['id'],'source_url':p['source_url'],'photos':[x.name for x in photos],'source_photos':[x['source_url'] for x in selected],'format':item['format']}
    (folder/'visual.json').write_text(json.dumps(visual,indent=2),encoding='utf8')
    if item['format']=='carousel': return frames,poster
    target=folder/'reel.mp4'
    if not target.exists():
        ffmpeg=os.environ.get('SOCIAL_FFMPEG','ffmpeg')
        listing=folder/'frames.txt';listing.write_text(''.join("file '"+x.name+"'\nduration 4\n" for x in frames)+"file '3.jpg'\n",encoding='utf8')
        subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(listing),'-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000','-t','16','-vf','fps=30,format=yuv420p','-c:v','libx264','-preset','fast','-crf','23','-c:a','aac','-movflags','+faststart',str(target)],check=True)
    return [target],poster

def run(r,mode):
    now=dt.datetime.now(dt.timezone.utc);today=now.astimezone(r.shared.JST).date()
    r.load_threads_token();channel=r.accounts();state=r.shared.load(r.STATE,{'journal':{},'history':[]})
    plan=load_plan(r.BASE);catalog={p['id']:p for p in r.shared.load(r.BASE/'property-catalog.json',[])};check_plan(plan,catalog)
    if mode=='diagnose': print('Experiment plan and both Ride accounts verified');return
    exp=state.setdefault('experiment',{'id':plan['id'],'operations':{},'baseline':{'as_of':now.isoformat(),'reach':None,'average_watch_seconds':None,'profile_visits':None,'website_clicks':None,'enquiries':None,'note':'Insights unavailable; missing is not zero. Compare per-post age and medians; not a randomized causal test.'}})
    if any(v.get('status')=='attempting' for v in exp['operations'].values()):raise RuntimeError('Uncertain experiment mutation; reconcile before retry')
    posts=r.shared.fetch_posts({'instagram':channel},now)
    byid={p['id']:p for p in posts}
    for entry in state['journal'].values():
        if entry.get('id') in byid:
            p=byid[entry['id']];entry.update(status=p['status'],url=p.get('externalLink'))
    r.persist(state)
    if mode in ('build','photos'):
        r.refresh_threads_token(state,now)
        horizon=today+dt.timedelta(days=4)
        for day,item in plan['days'].items():
            date=dt.date.fromisoformat(day);stamp=r.shared.due(date,item['hour_jst'])
            if date>horizon or stamp<=now+dt.timedelta(minutes=30):continue
            key=day+'/post/instagram';entry=state['journal'].get(key,{})
            current=[p for p in posts if r.shared.parsed(p.get('dueAt')) and r.shared.parsed(p['dueAt']).astimezone(r.shared.JST).date()==date and p['status']!='draft']
            if any(p['status'] not in ('scheduled',) for p in current):raise RuntimeError('Experiment day has non-scheduled content; inspect '+day)
            if entry.get('experiment_version')==VERSION:
                if len(current)!=1:raise RuntimeError('Experiment day must contain one reservation')
                continue
            if entry.get('status')=='attempting':raise RuntimeError('Unknown previous daily attempt')
            if len(current)>2:raise RuntimeError('Unexpected daily reservations')
            known_ids={v.get('id') for k,v in state['journal'].items() if k.startswith(day+'/')}
            if any(p['id'] not in known_ids for p in current):raise RuntimeError('Unrecognized reservation; stop '+day)
            keep=next((p for p in current if p['id']==entry.get('id')),current[0] if current else None)
            files,poster=prepare(r.BASE,day,item,catalog[item['property_id']])
            r.persist(state)
            sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=r.ROOT,text=True).strip()
            public=lambda path:'https://raw.githubusercontent.com/michikazutsuruta-design/sashiosae-rescue-test/'+sha+'/'+path.relative_to(r.ROOT).as_posix()
            urls=[public(path) for path in files];image=public(poster)
            # Remove only known future duplicates. Published posts are never deleted.
            for p in current:
                if keep and p['id']==keep['id']:continue
                op='delete/'+p['id'];exp['operations'][op]={'status':'attempting','day':day};r.persist(state)
                r.shared.graphql(DELETE,{'input':{'id':p['id']}})
                fresh=r.shared.fetch_posts({'instagram':channel},now)
                if any(x['id']==p['id'] for x in fresh):raise RuntimeError('Deletion not verified')
                exp['operations'][op]['status']='complete'
                for old in state['journal'].values():
                    if old.get('id')==p['id']:old.update(status='cancelled',experiment_note='Replaced by one daily experiment slot')
                posts=[x for x in posts if x['id']!=p['id']];r.persist(state)
            assets=[{('video' if item['format']=='reel' else 'image'):{'url':url}} for url in urls]
            payload={'assets':assets,'text':item['caption'],'aiAssisted':True,'metadata':{'instagram':{'type':'reel' if item['format']=='reel' else 'post','shouldShareToFeed':True,'isAiGenerated':False}},'dueAt':stamp.isoformat(),'mode':'customScheduled'}
            op=('edit/' if keep else 'create/')+day;exp['operations'][op]={'status':'attempting','day':day};r.persist(state)
            if keep:result=r.shared.graphql(EDIT,{'input':{**payload,'id':keep['id']}})['editPost']
            else:result=r.shared.graphql(r.shared.CREATE,{'input':{**payload,'channelId':r.IG,'schedulingType':'automatic','needsApproval':False}})['createPost']
            p=result.get('post')
            if not p or p['channelId']!=r.IG or r.shared.parsed(p['dueAt'])!=stamp or p['text']!=item['caption']:raise RuntimeError('Experiment reservation not verified')
            exp['operations'][op]['status']='complete'
            state['journal'][key]={'id':p['id'],'status':p['status'],'dueAt':p['dueAt'],'url':p.get('externalLink'),'media':urls[0],'assets':urls,'threads_image':image,'caption':item['caption'],'property_id':item['property_id'],'format':item['format'],'experiment_version':VERSION}
            posts=[x for x in posts if x['id']!=p['id']]+[p];r.persist(state)
            print('Verified experiment reservation:',day,item['format'],item['property_id'])
    if mode=='threads':
        day=str(today);stamp=r.shared.due(today,22);key=day+'/post/threads'
        if day not in plan['days'] or not stamp<=now<=stamp+dt.timedelta(hours=2):return
        if key in state['journal']:return
        source=state['journal'].get(day+'/post/instagram',{})
        if not source.get('threads_image') or source.get('experiment_version')!=VERSION:raise RuntimeError('No verified experiment image for Threads')
        recent=r.threads('me/threads?fields=id,text,permalink&limit=100').get('data',[])
        match=next((p for p in recent if p.get('text')==source['caption']),None)
        if match:state['journal'][key]={'id':match['id'],'url':match.get('permalink'),'status':'published'};r.persist(state);return
        state['journal'][key]={'status':'attempting','dueAt':stamp.isoformat()};r.persist(state)
        container=r.threads('me/threads',{'media_type':'IMAGE','image_url':source['threads_image'],'text':source['caption']})
        state['journal'][key]['container_id']=container['id'];r.persist(state)
        for _ in range(6):
            status=r.threads(container['id']+'?fields=status').get('status')
            if status=='FINISHED':break
            if status in ('ERROR','EXPIRED'):raise RuntimeError('Threads failed; do not retry')
            time.sleep(10)
        else:raise RuntimeError('Threads outcome uncertain')
        result=r.threads('me/threads_publish',{'creation_id':container['id']});state['journal'][key]['id']=result['id'];r.persist(state)
        public=r.threads(result['id']+'?fields=id,permalink,text')
        if public.get('text')!=source['caption'] or not public.get('permalink'):raise RuntimeError('Threads verification incomplete')
        state['journal'][key].update(status='published',url=public['permalink']);r.persist(state)
    for key,v in state['journal'].items():
        if v.get('experiment_version')!=VERSION:continue
        stamp=r.shared.parsed(v.get('dueAt'))
        if stamp and stamp<now-dt.timedelta(minutes=45) and v.get('status') not in ('sent','published'):raise RuntimeError('Experiment publication needs review: '+key)
    print('Experiment status verified; missing insight metrics have not been estimated.')
