import os
import sys
import uuid
from contextlib import contextmanager
import unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'social'))
import ride_visuals as v

@contextmanager
def scratch():
    # Keep rendering evidence under the checkout; restricted Windows temp ACLs may deny access.
    path=Path(__file__).resolve().parents[1]/'.test-artifacts'/uuid.uuid4().hex
    path.mkdir(parents=True)
    yield path

class PhotoFirstChecks(unittest.TestCase):
    def test_photo_frame_and_readable_title_bounds(self):
        base=Path(__file__).resolve().parents[1]/'social/ride'
        photos=v.asset_pool(base)
        with scratch() as tmp:
            for size in [(1080,1350),(1080,1920)]:
                path=Path(tmp)/f'{size[1]}.jpg'
                v.frame(photos[0],'A DIFFERENT POINT OF VIEW','換個角度，睇東京。',path,size)
                with Image.open(path) as im:
                    self.assertEqual(im.size,size)
                    # A real photograph, rather than a flat cream text card, must occupy the upper half.
                    colors=im.crop((100,200,980,600)).getcolors(880*400)
                    self.assertGreater(len(colors),1000)

    def test_missing_photo_never_falls_back_to_white_graphic(self):
        with scratch() as tmp:
            with self.assertRaises(FileNotFoundError):v.asset_pool(Path(tmp))

    def test_reject_unreadable_headline(self):
        base=Path(__file__).resolve().parents[1]/'social/ride'
        with scratch() as tmp:
            with self.assertRaises(ValueError):
                v.frame(v.asset_pool(base)[0],'UNBROKEN'+('X'*200),'東京',Path(tmp)/'bad.jpg')

if __name__=='__main__':unittest.main()
