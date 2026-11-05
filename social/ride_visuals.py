"""Real-photo editorial images. No generated rooms or text-only fallback."""
import datetime as dt
import os
import json
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageOps

VERSION='photo-first-editorial-v3-history'
DISCLAIMER_EN='Property photographs; current availability on enquiry.'
DISCLAIMER_YUE='物業實景照片，最新放售情況請查詢。'

HISTORIC_USES = {
    # Nov 4 was a manually replaced FIVE-photo carousel, not a single-date rotation.
    '2026-11-04': ['photo-first-source-%02d.jpg'%i for i in range(1,6)],
    '2026-11-05': ['photo-first-source-01.jpg','city-06.jpg'],
}

def asset_pool(base):
    paths=sorted(p for p in (base/'assets').glob('*.jpg') if p.name.startswith(('photo-first-source-','foret-','proud-','city-','classy-')))
    if not paths: raise FileNotFoundError('Actual photo library missing')
    for path in paths:
        with Image.open(path) as im:
            if min(im.size)<800: raise ValueError('Photo source too small: '+path.name)
            im.verify()
    return paths

def usage_history(base,ignore=None):
    uses={dt.date.fromisoformat(k):set(v) for k,v in HISTORIC_USES.items()}
    for path in sorted(base.glob('*/visual.json')):
        if ignore and path.parent.name==ignore: continue
        data=json.loads(path.read_text(encoding='utf8'))
        day=dt.date.fromisoformat(data['scheduled_for'])
        names=data.get('photos') or [data.get('photo')]
        uses.setdefault(day,set()).update(n for n in names if n)
    return uses

def choose_photos(base,day,kind,count):
    import hashlib
    pool=asset_pool(base);uses=usage_history(base,str(day)+'-'+kind)
    # Include ALL photos used by posts, carousels and reels, including same-day slots.
    recent=[d for d in uses if d<=day]
    previous=max((d for d in recent if d<day),default=None)
    blocked=set(uses.get(day,set()))
    if previous is not None: blocked.update(uses[previous])
    candidates=[p for p in pool if p.name not in blocked]
    if len(candidates)<count:
        raise ValueError('Not enough unused photos; stop instead of repeating adjacent posts')
    def priority(path):
        last=max((d for d,names in uses.items() if d<=day and path.name in names),default=dt.date.min)
        tie=hashlib.sha256((str(day)+'/'+kind+'/'+path.name).encode()).hexdigest()
        return (last,tie)
    return sorted(candidates,key=priority)[:count]

def get_font(size,serif=False):
    env='SOCIAL_SERIF_FONT' if serif else 'SOCIAL_FONT'
    default='/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf' if serif else '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
    return ImageFont.truetype(os.environ.get(env,default),size)

def wrap_title(text,font,max_width,max_lines=2):
    # English wraps at word boundaries; long CJK titles wrap by characters.
    words=text.upper().split() if text.isascii() else list(text)
    sep=' ' if text.isascii() else ''
    lines=[];line=''
    for word in words:
        candidate=line+sep+word if line else word
        if font.getlength(candidate)>max_width and line:
            lines.append(line);line=word
        else:line=candidate
    if line:lines.append(line)
    return lines if len(lines)<=max_lines and all(font.getlength(x)<=max_width for x in lines) else None

def frame(photo,title,zh,target,size=(1080,1350)):
    w,h=size
    with Image.open(photo) as original:
        image=ImageOps.fit(ImageOps.exif_transpose(original).convert('RGB'),size,method=Image.Resampling.LANCZOS).convert('RGBA')
    overlay=Image.new('RGBA',size);d=ImageDraw.Draw(overlay)
    for y in range(h):
        alpha=max(max(0,1-y/(h*.3))*82,max(0,(y-h*.46)/(h*.54))**.8*246)
        d.line((0,y,w,y),fill=(5,10,14,int(alpha)))
    image=Image.alpha_composite(image,overlay);d=ImageDraw.Draw(image)
    d.text((64,55),'RE/MAX RIDE',font=get_font(29),fill='white')
    d.line((64,108,240,108),fill='#d3b77a',width=3)
    d.text((64,128),'TOKYO RESIDENCES',font=get_font(21),fill='#e7ddc8')
    if not isinstance(title,str) or not isinstance(zh,str):raise ValueError('Titles must be strings')
    lines=None
    for px in range(78,55,-2):
        f=get_font(px,True);lines=wrap_title(title,f,950)
        if lines:break
    if not lines:raise ValueError('Headline too long for a readable photo layout')
    top=int(h*.67)
    d.multiline_text((60,top),'\n'.join(lines),font=f,fill='#fff8e9',spacing=3)
    zf=get_font(43);zlines=wrap_title(zh,zf,950)
    if not zlines:raise ValueError('Chinese headline too long')
    zy=top+len(lines)*(px+6)+28
    d.multiline_text((64,zy),'\n'.join(zlines),font=zf,fill='#e2c78a',spacing=6)
    footer=h-150
    if zy+len(zlines)*52>footer-12:raise ValueError('Headline overlaps footer')
    d.line((64,footer,1016,footer),fill='#a38b59',width=1)
    d.text((64,footer+27),'Explore residences · Link in bio',font=get_font(26),fill='#f4eee4')
    d.text((64,footer+78),'Real property photos · Availability on enquiry',font=get_font(20),fill='#b1b2a8')
    image.convert('RGB').save(target,quality=94,subsampling=0)
    return Path(target)

def render_post(c,folder,base):
    folder.mkdir(parents=True,exist_ok=True)
    day=dt.date.fromisoformat(folder.name[:10])
    photo=choose_photos(base,day,'post',1)[0]
    (folder/'visual.json').write_text(json.dumps({'scheduled_for':str(day),'kind':'post','photo':photo.name,'selection':'actual-usage-history-no-adjacent-reuse','version':VERSION},indent=2),encoding='utf-8')
    return frame(photo,c['title_en'],c['title_yue'],folder/'photo-first.jpg')

def render_reel(c,folder,base):
    folder.mkdir(parents=True,exist_ok=True)
    day=dt.date.fromisoformat(folder.name[:10]);photos=choose_photos(base,day,'reel',4);offset=0
    (folder/'visual.json').write_text(json.dumps({'scheduled_for':str(day),'kind':'reel','photos':[photos[(offset+i)%len(photos)].name for i in range(4)],'version':VERSION},indent=2),encoding='utf-8')
    # Fixed visual-led headlines avoid displaying long generated advice over rooms.
    headlines=[('SPACE. LIGHT. TOKYO.','空間・光線・東京'),('A DIFFERENT VIEW','換個角度，睇東京。'),('THE DETAILS MATTER','生活細節，值得留意。'),('FIND YOUR PLACE','搵到你嘅東京生活。')]
    clips=[];ffmpeg=os.environ.get('SOCIAL_FFMPEG','ffmpeg')
    for i,(title,zh) in enumerate(headlines):
        picture=frame(photos[(offset+i)%len(photos)],title,zh,folder/f'photo-scene-{i}.jpg',(1080,1920))
        clip=folder/f'photo-clip-{i}.mp4';clips.append(clip)
        subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-loop','1','-i',str(picture),'-t','5','-r','30','-vf','format=yuv420p','-an','-c:v','libx264','-preset','fast','-crf','23',str(clip)],check=True)
    listing=folder/'photo-concat.txt';listing.write_text(''.join("file '"+p.name+"'\n" for p in clips),encoding='utf-8')
    target=folder/'reel.mp4'
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(listing),'-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000','-t','20','-c:v','copy','-c:a','aac','-movflags','+faststart',str(target)],check=True)
    for clip in clips:clip.unlink()
    listing.unlink()
    return target

