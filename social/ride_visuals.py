"""Real-photo editorial images. No generated rooms or text-only fallback."""
import datetime as dt
import os
import json
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageOps

VERSION='photo-first-editorial-v2'
DISCLAIMER_EN='Property photographs; current availability on enquiry.'
DISCLAIMER_YUE='物業實景照片，最新放售情況請查詢。'

def asset_pool(base):
    paths=[base/'assets'/f'photo-first-source-{i:02d}.jpg' for i in range(1,6)]
    for path in paths:
        with Image.open(path) as im:
            if min(im.size)<800:raise ValueError('Photo source too small: '+path.name)
            im.verify()
    return paths

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
    photos=asset_pool(base)
    day=dt.date.fromisoformat(folder.name[:10])
    photo=photos[day.toordinal()%len(photos)]
    (folder/'visual.json').write_text(json.dumps({'scheduled_for':str(day),'kind':'post','photo':photo.name,'reuse_interval_days':len(photos),'version':VERSION},indent=2),encoding='utf-8')
    return frame(photo,c['title_en'],c['title_yue'],folder/'photo-first.jpg')

def render_reel(c,folder,base):
    folder.mkdir(parents=True,exist_ok=True)
    photos=asset_pool(base);day=dt.date.fromisoformat(folder.name[:10]);offset=(day.toordinal()+2)%len(photos)
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
