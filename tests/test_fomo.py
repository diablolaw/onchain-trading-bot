import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch,Mock
from desk.fomo import setup,Bridge,parse_metrics,enrich
from desk.core import vet,screening_market,Rules
from test_preflight import TOKEN,key
from test_desk import NOW,sample

class FomoTests(unittest.TestCase):
    def test_batch_failure_preserves_other_results_and_reports_safe_reason(self):
        from desk.fomo import collect_batches
        bridge=Mock()
        bridge.tokens.side_effect=[{'first':{'price':1}},RuntimeError('FOMO market request failed: fomo_http_429'),{'last':{'price':2}}]
        result,errors=collect_batches(bridge,[key(i) for i in range(41)])
        self.assertEqual(set(result),{'first','last'})
        self.assertEqual([len(c.args[0]) for c in bridge.tokens.call_args_list],[20,20,1])
        self.assertEqual(errors,[dict(batch=1,requested=20,reason='FOMO market request failed: fomo_http_429')])

    def test_local_pairing_config_owner_only_and_reused(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'bridge.json';a=setup(path)
            self.assertEqual(a,setup(path));self.assertEqual(path.stat().st_mode & 0o777,0o600)
            path.chmod(0o644)
            with self.assertRaises(ValueError):setup(path)

    def test_identity_numeric_missing_and_age(self):
        row=dict(address=TOKEN,networkId=1399811149,marketCap=100000,liquidity=50000,volume24=90000,createdAt=(NOW-3600)*1000,holders=100)
        m=parse_metrics(TOKEN,row,NOW);self.assertEqual(m['created_at'],NOW-3600)
        self.assertIsNone(m['price'])
        for bad in (dict(row,address=key(1)),dict(row,networkId=1),dict(row,holders=1.5)):
            with self.assertRaises(ValueError):parse_metrics(TOKEN,bad,NOW)

    def test_enrichment_keeps_pair_risk_and_execution_measurements(self):
        s=sample(token=TOKEN);m=dict(token=TOKEN,observed_at=NOW,liquidity=99999,volume_24h=123456,market_cap=100000,created_at=NOW-3600)
        result=enrich(s,m)
        self.assertEqual(result['volume_24h'],s['volume_24h'])
        self.assertEqual(result['liquidity'],s['liquidity'])
        self.assertEqual(screening_market(result)['volume_24h'],123456)
        with self.assertRaises(ValueError):enrich(s,dict(m,token=key(1)))
        stale=enrich(s,dict(m,observed_at=NOW-1000))
        self.assertIn('fomo_evidence_stale_or_unbound',vet(stale,Rules(),NOW))

    def test_authenticated_bridge_returns_metrics_without_credentials(self):
        # Ephemeral test port; production remains fixed loopback only.
        with patch('desk.fomo.PORT',0):bridge=Bridge(dict(key='a'*64,port=0)).start()
        base='http://127.0.0.1:'+str(bridge.server.server_port)
        def call(path,data=None,key='a'*64):
            req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Authorization':'Bearer '+key})
            with urlopen(req,timeout=2) as r:return json.load(r)
        try:
            with self.assertRaises(HTTPError):call('/jobs',key='bad')
            error=[];output=[]
            def work():
                try:output.append(bridge.tokens([TOKEN],timeout=2))
                except Exception as e:error.append(e)
            t=threading.Thread(target=work);t.start()
            jobs=call('/jobs')['jobs'];self.assertEqual(len(jobs),1)
            call('/result',dict(id=jobs[0]['id'],rows=[dict(address=TOKEN,networkId=1399811149,liquidity=50000)]))
            t.join(3);self.assertFalse(error);self.assertEqual(output[0][TOKEN]['liquidity'],50000)
            self.assertNotIn('authorization',json.dumps(output).lower())
            with self.assertRaises(HTTPError):call('/result',dict(id='unknown',rows=[]))
        finally:bridge.close()

    def test_timeout_cleans_pending_requests(self):
        with patch('desk.fomo.PORT',0):bridge=Bridge(dict(key='a'*64,port=0))
        try:
            with self.assertRaises(RuntimeError):bridge.tokens([TOKEN],timeout=.001)
            self.assertEqual(bridge.pending,{});self.assertEqual(bridge.jobs,[])
        finally:bridge.server.server_close()
