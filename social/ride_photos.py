"""Photo-led Ride Reels. Original website photos, editorial subtitles only."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import textwrap
from urllib.request import Request, urlopen

VERSION = 'website-photos-v1'
DISCLAIMER_EN = 'Website photos for illustration; availability not represented.'
DISCLAIMER_YUE = '網站照片示意，唔代表目前有售。'
SOURCES = [
    ('residence-a.jpg', '790c3d_256453753ab14aac8579dcda2a089c82~mv2.jpg', 'DSC06208.jpg'),
    ('residence-b.jpg', '790c3d_ecea0b9aa38c4aecaa9b255452c87620~mv2.jpg', 'DSC00785.jpg'),
    ('residence-c.jpg', '790c3d_bbc462cde76547f3981f9ba9c543f4a0~mv2.jpg', 'DSC06088.jpg'),
]

def photo_assets(base):
    cache = base/'assets'
    cache.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, asset, original in SOURCES:
        url = ('https://static.wixstatic.com/media/'+asset+
               '/v1/fill/w_491,h_855,al_c,q_85,usm_0.66_1.00_0.01,enc_avif,quality_auto/'+original)
        path = cache/name
        if not path.exists():
            with urlopen(Request(url, headers={'User-Agent':'RideEstateEditorial/1.0'}), timeout=60) as response:
                path.write_bytes(response.read())
        from PIL import Image
        with Image.open(path) as im:
            if im.width < 400 or im.height < 700: raise ValueError('Website photo resolution too low')
            im.verify()
        manifest.append({'file':name, 'url':url})
    (cache/'sources.json').write_text(json.dumps({
        'source_page':'https://www.remaxride-tsuruta.com/property-list',
        'permission':'User authorized website images for Ride social posts, 2026-11-01.',
        'use':'Editorial home-search guidance. No current listing, price or availability representation.',
        'photos':manifest}, ensure_ascii=False, indent=2), encoding='utf8')
    return [cache/name for name, _, _ in SOURCES]

def render_reel(c, folder, base):
    from PIL import Image, ImageDraw, ImageFont
    folder.mkdir(parents=True, exist_ok=True)
    photos = photo_assets(base)
    font = os.environ.get('SOCIAL_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    ffmpeg = os.environ.get('SOCIAL_FFMPEG','ffmpeg')
    try: offset = dt.date.fromisoformat(folder.name[:10]).toordinal() % len(photos)
    except ValueError: offset = 0
    clips = []
    for i in range(4):
        # Text graphics are separate transparent layers; the source photos are unretouched.
        layer = Image.new('RGBA',(1080,1920),(0,0,0,0)); d = ImageDraw.Draw(layer)
        d.rounded_rectangle((54,192,575,284),radius=14,fill=(16,39,56,215))
        d.rounded_rectangle((54,1280,1026,1670),radius=20,fill=(16,39,56,222))
        def write(text,size,x,y,width,color='white'):
            f = ImageFont.truetype(font,size)
            d.multiline_text((x,y),'\n'.join(textwrap.wrap(text,width=width)),font=f,fill=color,spacing=14)
        write('RE/MAX RIDE',48,82,206,40)
        en = c['slides_en'][i] if i < 3 else 'Explore more on our official website'
        yue = c['slides_yue'][i] if i < 3 else '撳個人檔案入面嘅官方網站'
        write(en,43,88,1320,36)
        write(yue,45,88,1485,19)
        # Keep the disclosure above Instagram's bottom caption and controls.
        d.rectangle((54,1690,1026,1800),fill=(16,39,56,215))
        write('Website photos • Availability not represented',25,78,1700,65)
        write(DISCLAIMER_YUE,28,78,1744,35)
        overlay = folder/f'overlay-{i}.png'; layer.save(overlay)
        clip = folder/f'scene-{i}.mp4'; clips.append(clip)
        photo = photos[(offset+i)%len(photos)]
        # Small centered zoom; no generated or altered property features.
        filters = ("[0:v]scale=1120:1992:force_original_aspect_ratio=increase,crop=1120:1992,"
                   "zoompan=z='1+0.00035*on':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
                   "d=150:s=1080x1920:fps=30[photo];[photo][1:v]overlay=0:0:format=auto,format=yuv420p[v]")
        subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-i',str(photo),'-i',str(overlay),
                        '-filter_complex',filters,'-map','[v]','-t','5','-an','-c:v','libx264',
                        '-preset','fast','-crf','23',str(clip)],check=True)
    listing = folder/'photo-concat.txt'
    listing.write_text(''.join("file '"+p.name+"'\n" for p in clips),encoding='utf8')
    target = folder/'reel.mp4'
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(listing),
                    '-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000','-t','20',
                    '-c:v','copy','-c:a','aac','-movflags','+faststart',str(target)],check=True)
    # Temporary scene files are rebuildable and need not be published in the repository.
    for clip in clips: clip.unlink()
    listing.unlink()
    return target
