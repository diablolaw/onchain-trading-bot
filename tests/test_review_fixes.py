import json
import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from desk.core import Desk,Rules,normalize
from desk.discovery_feed import poll,candidates
from desk.supervisor import run,validate_entry_setup
from desk.worldmonitor import project
from desk.cli import monitor
from desk.bench import exclude,reject
from test_desk import sample,NOW
from test_preflight import TOKEN,key

class ReviewFixes(unittest.TestCase):
    def test_silent_worker_is_reaped_before_restart_and_idle_judge_is_exempt(self):
        class Process:
            def __init__(self):self.returncode=None;self.stdout=Mock();self.reaped=False
            def poll(self):return self.returncode
            def terminate(self):self.returncode=-15
            def wait(self,timeout=None):self.reaped=True;return self.returncode
        processes=[];clock=[0];iterations=0
        def popen(cmd,**kwargs):
            if len(processes)==4:
                self.assertTrue(processes[0][1].reaped)
                self.assertIsNone(processes[2][1].poll())
                self.assertIsNone(processes[3][1].poll())
            p=Process();processes.append((cmd,p));return p
        def event(timeout):
            nonlocal iterations
            iterations+=1
            if iterations==2:
                self.assertEqual(len(processes),5)
                self.assertIn('discovery-watch',processes[-1][0])
                raise KeyboardInterrupt()
            clock[0]=301
            raise queue.Empty()
        events=Mock();events.get.side_effect=event
        with patch('desk.supervisor.subprocess.Popen',side_effect=popen),patch('desk.supervisor.threading.Thread'),patch('desk.supervisor.queue.Queue',return_value=events),patch('desk.supervisor.time.monotonic',side_effect=lambda:clock[0]),patch('builtins.print'):
            run(observe_only=True)

    def test_disappeared_search_token_matures_through_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'desk.sqlite',Rules(min_age_hours=.25,max_age_hours=72))
            scanner=Mock()
            pair=dict(chainId='solana',pairAddress=key(1),baseToken=dict(address=TOKEN),pairCreatedAt=1900000,liquidity=dict(usd=20000),volume=dict(h24=50000),priceUsd='1',marketCap=100000)
            scanner.get.return_value=({'pairs':[pair]},2000)
            try:
                poll(desk,['pump'],scanner,now=2000)
                self.assertEqual(candidates(desk,now=2000)[0],[])
                scanner.get.return_value=({'pairs':[]},2901)
                scanner.batch_pairs.return_value={TOKEN:normalize(pair,2901)}
                poll(desk,['pump'],scanner,now=2901)
                self.assertEqual(candidates(desk,now=2901)[0][0]['token'],TOKEN)
                scanner.batch_pairs.assert_called_once_with([TOKEN])
                reject(desk,TOKEN,['owner_concentration'],now=2901)
                self.assertEqual(exclude(desk,[{'token':TOKEN}],now=2902),[])
                self.assertEqual(len(exclude(desk,[{'token':TOKEN}],now=2901+91*60)),1)
            finally:desk.close()

    def test_risk_survives_other_worker_restart_exhaustion(self):
        class Process:
            def __init__(self,cmd):self.scan='discovery-watch' in cmd;self.stopped=False;self.returncode=1 if self.scan else None;self.stdout=Mock()
            def poll(self):return -15 if self.stopped else self.returncode
            def terminate(self):self.stopped=True
            def wait(self,timeout=None):return 0
        processes=[];iterations=0
        def popen(cmd,**kwargs):
            p=Process(cmd);processes.append((cmd,p));return p
        def event(timeout):
            nonlocal iterations
            iterations+=1
            if iterations==4:
                risk=next(p for cmd,p in processes if 'risk' in cmd)
                self.assertFalse(risk.stopped)
                self.assertTrue(next(p for cmd,p in processes if 'ai-watch' in cmd).stopped)
                raise KeyboardInterrupt()
            raise queue.Empty()
        events=Mock();events.get.side_effect=event
        with patch('desk.supervisor.subprocess.Popen',side_effect=popen),patch('desk.supervisor.threading.Thread'),patch('desk.supervisor.queue.Queue',return_value=events),patch('builtins.print'):
            run(observe_only=True)

    def test_entry_setup_forwards_explicit_forecasts_and_preserves_old_cash(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'forecasts.json';path.write_text(json.dumps({TOKEN:dict(probability=.8,payoff=1,source='test',observed_at=time.time())}))
            validate_entry_setup(root,Path(d)/'new.sqlite',path)
            with self.assertRaisesRegex(ValueError,'sizing-file'):validate_entry_setup(root,Path(d)/'new.sqlite',None)
            old=Desk(Path(d)/'old.sqlite',Rules(initial_cash=1000))
            old.close()
            with self.assertRaisesRegex(ValueError,'viable'):validate_entry_setup(root,Path(d)/'old.sqlite',path)

    def test_context_rejects_empty_stale_and_projects_large_payload(self):
        endpoint='/api/intelligence/v1/get-social-velocity'
        self.assertFalse(project(endpoint,{'posts':[],'fetchedAt':0},NOW)[1])
        payload={'posts':[{'id':str(i),'title':'x'*500,'subreddit':'news','url':'https://example.com','velocityScore':1} for i in range(40)],'fetchedAt':NOW}
        result,valid,_,_=project(endpoint,payload,NOW)
        self.assertTrue(valid);self.assertTrue(result['truncated'])
        self.assertLessEqual(len(json.dumps(result,separators=(',',':')).encode()),1024)
        payload['fetchedAt']=NOW-2000
        self.assertFalse(project(endpoint,payload,NOW)[1])

    def test_pending_close_requires_price_but_no_longer_volume(self):
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'desk.sqlite',Rules(exit_policy='post_volume'))
            try:
                run_id=desk.run('test',NOW);desk.record(run_id,sample(),NOW)
                scanner=Mock();scanner.pair.return_value=sample(now=NOW+30,volume_6h=None,volume_24h=None)
                with patch('desk.cli.time.time',return_value=NOW+30):monitor(desk,scanner,run_id)
                self.assertEqual(len(desk.open_positions()),1)
                scanner.pair.return_value=sample(now=NOW+60,volume_6h=None,volume_24h=None)
                with patch('desk.cli.time.time',return_value=NOW+60):monitor(desk,scanner,run_id)
                self.assertEqual(len(desk.open_positions()),0)
            finally:desk.close()

    def test_holder_growth_orders_dossier_slots(self):
        from desk.comparison import rank_before_dossiers
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'desk.sqlite')
            try:
                candidates=[{'token':TOKEN},{'token':key(2)}]
                previous={c['token']:dict(source='fomo:filterTokens',observed_at=NOW,holder_count=100,price=1) for c in candidates}
                rank_before_dossiers(desk,candidates,previous,{})
                current={TOKEN:dict(previous[TOKEN],observed_at=NOW+30,holder_count=110,price=1.5),key(2):dict(previous[key(2)],observed_at=NOW+30,holder_count=150,price=1.1)}
                ranked=rank_before_dossiers(desk,candidates,current,{})
                self.assertEqual(ranked[0]['token'],key(2))
                self.assertTrue(ranked[1]['pre_dossier_ranking']['price_outpaces_holders'])
            finally:desk.close()
