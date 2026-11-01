import datetime as dt
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('runner',Path(__file__).resolve().parents[1]/'social/runner.py')
r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)

class SocialCloudTests(unittest.TestCase):
    def test_weekly_slots_and_jst(self):
        now=dt.datetime(2026,11,1,0,tzinfo=dt.timezone.utc)
        slots=r.target_slots(now,7)
        regular=[s for s in slots if s[1]=='post']
        reels=[s for s in slots if s[1]=='reel']
        self.assertEqual(len(regular),14)
        self.assertEqual(len(reels),3)
        self.assertEqual({s[3].astimezone(r.JST).hour for s in regular},{21})
        self.assertEqual({s[3].astimezone(r.JST).hour for s in reels},{15})
        self.assertTrue(all(s[2]=='instagram' for s in reels))

    def test_existing_slot_blocks_reposting(self):
        stamp=r.due(dt.date(2026,11,2),21)
        existing={'channelId':'ig','dueAt':'2026-11-02T12:00:00Z','status':'scheduled'}
        self.assertTrue(r.same_slot(existing,'ig',stamp))
        existing['status']='sent'
        self.assertTrue(r.same_slot(existing,'ig',stamp))
        self.assertFalse(r.same_slot(existing,'threads',stamp))

    def content(self):
        return {'title':'相談前のメモ','body':'相談で伝えたいことを、分かる範囲でメモしてみましょう。まだ分からないことも、質問として書き留めておくと振り返りやすくなります。',
                'slides':['相談前にメモを','困りごとを整理','質問をまとめる','プロフィールの公式サイトへ']}

    def test_unapproved_claim_rejected(self):
        item=self.content(); item['body']+='必ず解決します。'
        with self.assertRaises(ValueError): r.validate(item,[])

    def test_duplicate_content_rejected(self):
        item=self.content()
        with self.assertRaises(ValueError): r.validate(item,[item['body']])

    def test_valid_content_and_caption_fit_threads(self):
        item=self.content(); r.validate(item,[])
        self.assertLess(len(item['body']+'\n\n'+r.FOOTER),500)

    def test_four_short_slides_only(self):
        item=self.content(); item['slides'].append('追加')
        with self.assertRaises(ValueError): r.validate(item,[])

    def test_structured_ai_response(self):
        item=self.content()
        with patch.dict(r.os.environ,{'CLOUDFLARE_ACCOUNT_ID':'example','CLOUDFLARE_API_TOKEN':'example'}), patch.object(r,'request_json',return_value={'success':True,'result':{'response':item}}):
            self.assertEqual(r.generate({'topics':['相談準備'],'model':'example'},dt.date(2026,11,4),'post',[]),item)

if __name__=='__main__': unittest.main()
