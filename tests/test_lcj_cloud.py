import datetime as dt, importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve()
SOURCE=HERE.parents[1]/'social/lcj_runner.py'
if not SOURCE.exists(): SOURCE=HERE.parent/'lcj_runner.py'
spec=importlib.util.spec_from_file_location('lcj',SOURCE)
lcj=importlib.util.module_from_spec(spec);spec.loader.exec_module(lcj)

class Safeguards(unittest.TestCase):
    def test_due_window_jst(self):
        for hour,expected in [(4,False),(5,True),(14,True),(15,False),(23,False)]:
            self.assertEqual(lcj.due(dt.datetime(2026,11,13,hour,tzinfo=dt.timezone.utc)),expected)
    def test_uncertain_outcome_never_resends(self):
        now=dt.datetime(2026,11,13,5,tzinfo=dt.timezone.utc)
        for status in ['preparing','publishing','unknown']:
            state={'journal':{'2026-11-13/instagram':{'status':status}}}
            with patch.object(lcj,'today_posts',return_value=[]),patch.object(lcj,'graph') as call:
                with self.assertRaises(RuntimeError): lcj.publish_one('instagram','x','id',state,now,[],[])
                call.assert_not_called()
    def test_sent_never_resends(self):
        state={'journal':{'2026-11-13/instagram':{'status':'sent'}}}
        with patch.object(lcj,'today_posts') as remote:
            lcj.publish_one('instagram','x','id',state,dt.datetime(2026,11,13,5,tzinfo=dt.timezone.utc),[],[])
            remote.assert_not_called()
    def test_remote_post_blocks_duplicate(self):
        state={'journal':{}}
        with patch.object(lcj,'today_posts',return_value=[{'id':'p','permalink':'https://example.com/p'}]),patch.object(lcj,'persist'),patch.object(lcj,'graph') as mutate:
            lcj.publish_one('instagram','x','id',state,dt.datetime(2026,11,13,5,tzinfo=dt.timezone.utc),[],[])
            self.assertEqual(state['journal']['2026-11-13/instagram']['status'],'observed_external')
            mutate.assert_not_called()
    def test_reviewed_threads_fit(self):
        path=HERE.parents[1]/'social/lcj/topics.json'
        if not path.exists(): path=HERE.parent/'topics.json'
        for topic in json.loads(path.read_text(encoding='utf8')):
            self.assertLessEqual(len(lcj.caption(topic,'threads',dt.date(2026,11,13))),500)
    def test_today_uses_jst(self):
        posts={'data':[{'id':'p','timestamp':'2026-11-12T15:30:00+0000'}]}
        with patch.object(lcj,'graph',return_value=posts):
            self.assertEqual(len(lcj.today_posts('instagram','x','id',dt.date(2026,11,13))),1)

if __name__=='__main__':unittest.main()
