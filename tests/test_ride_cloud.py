import sys, unittest, datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'social'))
import ride_runner as r
from test_ride_visuals import PhotoFirstChecks

class RideSafety(unittest.TestCase):
    def test_no_overlap_with_existing_publer_bridge(self):
        now=dt.datetime(2026,11,1,tzinfo=dt.timezone.utc)
        self.assertTrue(all(day>=dt.date(2026,11,4) for _,day,kind in r.slots(now) if kind=='post'))
        self.assertIn(dt.date(2026,11,2),[day for _,day,kind in r.slots(now) if kind=='reel'])

    def test_times_and_reel_days(self):
        for stamp,day,kind in r.slots(dt.datetime(2026,11,4,tzinfo=dt.timezone.utc)):
            self.assertEqual(stamp.astimezone(r.shared.JST).hour,22 if kind=='post' else 16)
            if kind=='reel': self.assertIn(day.weekday(),(0,2,4))

    def test_reject_mandarin_guarantees_and_duplicate(self):
        c={'title_en':'Viewing questions','title_yue':'睇樓前準備','body_en':'Write down your home viewing questions. Visit the official site in our profile.',
           'body_yue':'睇樓之前，寫低你想問嘅問題。撳個人檔案入面嘅官方網站了解更多。',
           'slides_en':['Plan your questions']*4,'slides_yue':['寫低你想問嘅問題']*4}
        self.assertLessEqual(len(r.validate(c,[])),500)
        with self.assertRaises(ValueError): r.validate(c,[c['body_en']])
        c['body_en']='We guarantee profit.'
        with self.assertRaises(ValueError): r.validate(c,[])
        c['body_en']='Prepare questions before a viewing.'; c['body_yue']='歡迎瀏覽網站了解更多住宅資訊。'
        with self.assertRaises(ValueError): r.validate(c,[])





import unittest, tempfile, os, json, datetime as dt
from pathlib import Path
from unittest.mock import patch, MagicMock
import ride_runner as r

class TokenMaintenance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.base=Path(self.tmp.name)
        self.patches=[patch.object(r,'BASE',self.base),patch.object(r,'TOKEN_CACHE',self.base/'threads-token.enc'),patch.dict(os.environ,{'RIDE_THREADS_ACCESS_TOKEN':'test-root-secret-only'})]
        for p in self.patches:p.start()
        r._active_threads_token=None
        self.now=dt.datetime(2026,11,6,tzinfo=dt.timezone.utc)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def response(self,token='test-renewed-token'):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps({'access_token':token,'expires_in':5184000})
        return response
    def test_refresh_survives_fresh_process_without_plaintext(self):
        state={'token_refresh_warning':'old warning'}
        with patch.object(r,'urlopen',return_value=self.response()),patch.object(r,'threads',return_value={'username':'rideestate'}) as api,patch.object(r,'persist') as save:
            r.load_threads_token();r.refresh_threads_token(state,self.now)
            api.assert_called_once_with('me?fields=id,username',token='test-renewed-token')
            save.assert_called_once()
        ciphertext=r.TOKEN_CACHE.read_bytes()
        self.assertNotIn(b'test-renewed-token',ciphertext)
        self.assertNotIn(b'test-root-secret-only',ciphertext)
        self.assertNotIn('token_refresh_warning',state)
        r._active_threads_token=None;r.load_threads_token()
        self.assertEqual(r._active_threads_token,'test-renewed-token')
        with patch.object(r,'urlopen',return_value=self.response('test-second-token')),patch.object(r,'threads',return_value={'username':'rideestate'}),patch.object(r,'persist'):
            r.refresh_threads_token(state,self.now+dt.timedelta(days=8))
        r._active_threads_token=None;r.load_threads_token()
        self.assertEqual(r._active_threads_token,'test-second-token')
    def test_wrong_account_does_not_store_or_clear_warning(self):
        state={}
        with patch.object(r,'urlopen',return_value=self.response()),patch.object(r,'threads',return_value={'username':'other'}),patch.object(r,'persist'):
            r.refresh_threads_token(state,self.now)
        self.assertFalse(r.TOKEN_CACHE.exists());self.assertIn('token_refresh_warning',state)
    def test_tampering_or_changed_root_fails_closed(self):
        r.TOKEN_CACHE.write_bytes(r.token_cipher().encrypt(json.dumps({'account':'rideestate','access_token':'test-only'}).encode()))
        with patch.dict(os.environ,{'RIDE_THREADS_ACCESS_TOKEN':'different-test-root'}):
            with self.assertRaises(RuntimeError):r.load_threads_token()
        r.TOKEN_CACHE.write_bytes(b'corrupted')
        with self.assertRaises(RuntimeError):r.load_threads_token()
    def test_failed_persistence_does_not_activate_new_token(self):
        r.load_threads_token()
        with patch.object(r,'urlopen',return_value=self.response()),patch.object(r,'threads',return_value={'username':'rideestate'}),patch.object(r,'persist',side_effect=RuntimeError('push failed')):
            with self.assertRaises(RuntimeError):r.refresh_threads_token({},self.now)
        self.assertEqual(r._active_threads_token,'test-root-secret-only')




import unittest,datetime as dt
from unittest.mock import patch,Mock
import runner as r
class Links(unittest.TestCase):
 def setUp(self):
  self.now=dt.datetime(2026,11,6,tzinfo=dt.timezone.utc)
  self.p={'id':'owned','channelId':'ride','status':'scheduled','dueAt':'2026-11-06T13:00:00+00:00','text':'Official https://www.remaxride-tsuruta.com/en'}
  self.state={'journal':{'today':{'id':'owned','caption':self.p['text']}}}
 def test_changes_only_text_of_known_future_post(self):
  updated={**self.p,'text':'Official https://tinyurl.com/RIDEESTATE'}
  save=Mock()
  with patch.object(r,'graphql',return_value={'editPost':{'post':updated}}) as api:
   r.refresh_scheduled_links([self.p],self.state,save,{'ride'},self.now)
   self.assertEqual(api.call_args.args[1],{'input':{'id':'owned','text':updated['text']}})
  self.assertEqual(self.state['journal']['today']['caption'],updated['text'])
  self.assertEqual(self.state['journal']['today']['link_update'],'complete')
  self.assertEqual(save.call_count,2)
 def test_unknown_attempt_and_changed_response_stop(self):
  self.state['journal']['today']['link_update']='attempting'
  with patch.object(r,'graphql') as api:
   with self.assertRaises(RuntimeError):r.refresh_scheduled_links([self.p],self.state,Mock(),{'ride'},self.now)
   api.assert_not_called()
  self.state['journal']['today'].pop('link_update')
  with patch.object(r,'graphql',return_value={'editPost':{'post':{**self.p,'dueAt':'2026-11-07T13:00:00+00:00'}}}):
   with self.assertRaises(RuntimeError):r.refresh_scheduled_links([self.p],self.state,Mock(),{'ride'},self.now)
  self.assertEqual(self.state['journal']['today']['link_update'],'attempting')
 def test_other_channel_and_published_posts_are_untouched(self):
  with patch.object(r,'graphql') as api:
   r.refresh_scheduled_links([self.p],self.state,Mock(),{'rescue'},self.now)
   self.p['status']='sent'
   r.refresh_scheduled_links([self.p],self.state,Mock(),{'ride'},self.now)
   api.assert_not_called()
if __name__=='__main__':unittest.main()
