import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from desk.ai import evaluate,AIPolicy
from desk.core import Desk,Rules
from test_desk import NOW,sample
from test_preflight import TOKEN,evidence,order
from desk.quotes import USDC,parse_quote

class Providers:
    calls=0
    def ready(self):pass
    def judge(self,state,questions):
        self.calls+=1;answers={}
        for key,q in questions.items():
            if q['type']=='choice':
                winner=next(iter(q['criteria']))
                answers[key]=dict(type='choice',choice=winner,confidence=.9,probabilities={k:float(k==winner) for k in q['criteria']})
            elif q['type']=='noul':answers[key]=dict(type='noul',noul=.1 if key in ('concentration_is_exit_risk','dev_still_loaded','momentum_already_spent') else .9)
        return dict(model='test',answers=answers,usage={'input_tokens':1,'output_tokens':1})
    def analyze(self,state):raise AssertionError('post workflow must not use the legacy Grok veto')

class PostEvaluateTests(unittest.TestCase):
    @patch('desk.post_evaluate.time.time',return_value=NOW)
    def test_missing_edge_never_buys_but_explicit_inputs_can(self,clock):
        inputs_valid={'probability':.8,'payoff':1,'observed_at':NOW,'source':'test forecast','max_fee_pct':10}
        for inputs,expected,bad_refresh in ((None,0,False),(inputs_valid,1,False),(inputs_valid,0,True)):
            with self.subTest(inputs=inputs),tempfile.TemporaryDirectory() as d:
                rules=Rules(require_preflight=True,max_top10_owner_pct=60,ticket=60,max_exposure=65,max_ticket_liquidity_pct=2,exit_policy='post_volume',minimum_fee_usd=.95,fee_bps=45,fixed_fee_usd=0)
                desk=Desk(Path(d)/'paper.sqlite',rules)
                try:
                    def sized_quotes(ticket):
                        amount=int(ticket*1000000);output=amount*5;minimum=output*99//100
                        return dict(token=TOKEN,quote_currency=USDC,ticket_usdc=str(ticket),buy=parse_quote(order(amount=amount,output=output),USDC,TOKEN,amount,NOW),sell=parse_quote(order(TOKEN,USDC,minimum,amount),TOKEN,USDC,minimum,NOW),worst_quote_roundtrip_loss_pct=1,network_lamports=12000)
                    e=evidence();e['quotes']=sized_quotes(60);s=sample(token=TOKEN,holders=e['holders'],preflight=e)
                    # An oversized exploratory quote must not block AI research.
                    e['quotes']['buy']['price_impact_pct']=8
                    first=desk.run('history',NOW-30);desk.record(first,dict(s,observed_at=NOW-30),NOW-30,enter=False)
                    run=desk.run('post',NOW)
                    s['sizing_evidence']=inputs
                    provider=Providers()
                    def refresh(current):
                        quotes=sized_quotes(desk.rules.ticket)
                        if bad_refresh:quotes['buy']['price_impact_pct']=8
                        return dict(current,preflight=dict(current['preflight'],quotes=quotes))
                    result=evaluate(desk,run,[s],provider,AIPolicy(judgment_profile='post',max_calls=10),refresh_selected=refresh)
                    self.assertEqual(result['state'],'complete')
                    self.assertEqual(result['paper_buys'],expected)
                    self.assertEqual(provider.calls,3)
                    if expected:
                        self.assertEqual(float(desk.open_positions()[0]['cost']),36.95)
                        self.assertEqual(desk.rules.ticket,60)
                finally:desk.db.close()
