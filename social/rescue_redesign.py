"""Replace only verified future Sashiosae reservations; never create or publish posts."""
import datetime as dt
from pathlib import Path
import runner as r

EDIT='''mutation Edit($input: EditPostInput!) { editPost(input:$input) {
 ... on PostActionSuccess {post {id channelId dueAt status}}
 ... on MutationError {message} } }'''

def run():
    config=r.load(r.CONFIG,{})
    channel_map=r.channels(config)
    now=dt.datetime.now(dt.timezone.utc)
    posts={p['id']:p for p in r.fetch_posts(channel_map,now)}
    state=r.load(r.STATE,{'journal':{},'history':[]})
    completed=0
    for key,entry in state['journal'].items():
        if entry.get('visual_update')=='attempting':
            raise RuntimeError('Uncertain prior edit; reconcile before retry: '+key)
        remote=posts.get(entry.get('id'))
        if not remote or remote['status']!='scheduled': continue
        stamp=r.parsed(remote['dueAt'])
        if stamp<=now+dt.timedelta(minutes=20): continue
        if entry.get('visual_version')==r.VISUAL_VERSION: continue
        day,kind,service=key.split('/')
        if remote['channelId']!=config[service]: raise RuntimeError('Wrong reservation account')
        if r.parsed(entry['dueAt'])!=stamp: raise RuntimeError('Reservation time changed')
        content=r.load(r.ROOT/'social/content'/f'{day}-{kind}.json',{})
        if not content:
            content={'title':'家のこと、一人で抱えない。'}
        folder=r.ROOT/'social/media'/f'{day}-{kind}-illustrated-v2'
        media=r.render(content,folder,kind)
        url=r.checkpoint_media(state,config,media)
        entry['visual_update']='attempting'; r.persist(state)
        payload={'id':remote['id'],'assets':[{('video' if kind=='reel' else 'image'):{'url':url}}],
                 'aiAssisted':True}
        if service=='instagram':
            payload['metadata']={'instagram':{'type':kind,'shouldShareToFeed':True,'isAiGenerated':True}}
        result=r.graphql(EDIT,{'input':payload})['editPost'].get('post')
        if (not result or result['id']!=remote['id'] or result['channelId']!=remote['channelId']
            or r.parsed(result['dueAt'])!=stamp or result['status']!='scheduled'):
            raise RuntimeError('Edit outcome requires reconciliation; do not retry '+key)
        entry.update(visual_update='complete',visual_version=r.VISUAL_VERSION,media=url)
        r.persist(state); completed+=1
        print('Updated visual; account/time preserved:',key)
    print('Scheduled visuals replaced:',completed)

if __name__=='__main__': run()
