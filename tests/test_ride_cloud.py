import sys, unittest, datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'social'))
import ride_runner as r

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

if __name__=='__main__': unittest.main()
