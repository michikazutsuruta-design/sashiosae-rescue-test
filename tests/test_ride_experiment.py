import copy,datetime as dt,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'social'))
import ride_experiment as e
BASE=Path(__file__).resolve().parents[1]/'social/ride'
class ExperimentChecks(unittest.TestCase):
 def setUp(self):
  self.plan=e.load_plan(BASE);self.catalog={p['id']:p for p in json.loads((BASE/'property-catalog.json').read_text(encoding='utf8'))}
 def test_exact_finite_mix(self):
  e.check_plan(self.plan,self.catalog)
  self.assertEqual(sum(x['format']=='carousel' for x in self.plan['days'].values()),4)
 def test_wrong_mix_rejected(self):
  self.plan['days']['2026-11-18']['format']='carousel'
  with self.assertRaises(ValueError):e.check_plan(self.plan,self.catalog)
 def test_adjacent_property_rejected(self):
  self.plan['days']['2026-11-19']['property_id']=self.plan['days']['2026-11-18']['property_id']
  with self.assertRaises(ValueError):e.check_plan(self.plan,self.catalog)
 def test_no_current_availability_claim(self):
  self.catalog['foretseine']['availability']='available'
  with self.assertRaises(ValueError):e.check_plan(self.plan,self.catalog)
 def test_finite_dispatch(self):
  zone=dt.timezone(dt.timedelta(hours=9))
  self.assertTrue(e.applies(BASE,dt.datetime(2026,11,18,9,tzinfo=zone),'build'))
  self.assertFalse(e.applies(BASE,dt.datetime(2026,12,2,9,tzinfo=zone),'build'))
  self.assertFalse(e.applies(BASE,dt.datetime(2026,11,17,22,tzinfo=zone),'threads'))
 def test_property_photos_exist(self):
  for p in self.catalog.values():
   for photo in p['photos']:self.assertTrue((BASE/photo['file']).is_file())
