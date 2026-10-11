import tempfile
import unittest
import time
from pathlib import Path
from desk.core import Desk
from desk.social import request,jobs,submit
from test_preflight import TOKEN

class SocialTests(unittest.TestCase):
    def test_project_handle_binding_and_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'desk.sqlite')
            try:
                self.assertIsNone(request(desk,TOKEN,'https://x.com/project/status/1'))
                self.assertEqual(jobs(desk.db)[0]['handle'],'project')
                data=dict(token=TOKEN,handle='project',observed_at=time.time(),source='grok_bot_x_plugin',x_account={'handle':'project','recent_posts':[]})
                submit(desk.db,data)
                self.assertIsNotNone(request(desk,TOKEN,'project'))
                # Fresh evidence is not queued again; evidence past half its hour is.
                self.assertEqual(jobs(desk.db),[])
                desk.db.execute('UPDATE social_evidence SET observed_at=?',(time.time()-2000,))
                self.assertIsNotNone(request(desk,TOKEN,'project'))
                self.assertEqual(jobs(desk.db)[0]['handle'],'project')
                with self.assertRaises(ValueError):submit(desk.db,dict(data,handle='imposter'))
                with self.assertRaises(ValueError):submit(desk.db,dict(data,observed_at=time.time()-4000))
            finally:desk.db.close()
