import unittest
from desk.post_policy import size,soft_reasons,pick_passed
from desk.ai import validate_jev

class PostPolicyTests(unittest.TestCase):
    def test_fee_floor_kelly_cap_and_partial_cut(self):
        self.assertEqual(size(50,50,.8,1,100000)['dollars'],0)
        full=size(10000,10000,.8,1,100000)
        self.assertEqual(full['dollars'],600)
        self.assertEqual(size(10000,10000,.8,1,100000,partial=True)['dollars'],360)
        self.assertEqual(size(10000,10000,.4,1,100000)['dollars'],0)
        self.assertLessEqual(size(10000,10000,.8,1,100000,locked_cash=9900)['dollars'],100)
    def test_shape_and_absolute_pick(self):
        market={'shape':{'choice':'crowd','probabilities':{'crowd':.55}},'liquidity_fits_ticket':{'noul':.6},'momentum_already_spent':{'noul':.6}}
        chain={'authority_risk':{'choice':'renounced'},'concentration_is_exit_risk':{'noul':.55},'dev_still_loaded':{'noul':.55}}
        self.assertEqual(soft_reasons(market,chain),[])
        market['shape']['choice']='one_buyer';self.assertIn('shape',soft_reasons(market,chain))
        self.assertFalse(pick_passed({'worth_trading_at_all':{'noul':.59},'best':{'confidence':.9}}))
    def test_weighted_score_validation(self):
        qs={'effort':{'type':'score','criteria':['Minimal','Shipping']}}
        reply={'model':'test','answers':{'effort':{'type':'score','score':.75,'legend':{'0':'Minimal','1':'Shipping'},'probabilities':{'0':.25,'1':.75},'confidence':.5}},'usage':{'input_tokens':1,'output_tokens':1}}
        self.assertEqual(validate_jev(reply,qs)['effort']['score'],.75)
        reply['answers']['effort']['score']=1
        with self.assertRaises(ValueError):validate_jev(reply,qs)

    def test_runner_holds_past_profit_and_time_until_volume_fades(self):
        import tempfile
        from pathlib import Path
        from desk.core import Desk,Rules
        from test_desk import sample,NOW
        with tempfile.TemporaryDirectory() as d:
            desk=Desk(Path(d)/'paper.sqlite',Rules(exit_policy='post_volume',minimum_fee_usd=.95,fee_bps=45,fixed_fee_usd=0))
            try:
                run=desk.run('test',NOW);desk.record(run,sample(),NOW)
                self.assertEqual(float(desk.db.execute("SELECT fee FROM fills WHERE side='buy'").fetchone()[0]),.95)
                desk.record(run,sample(price=1.6,now=NOW+86401),NOW+86401,enter=False)
                self.assertEqual(len(desk.open_positions()),1)
                desk.record(run,sample(price=1.5,now=NOW+86402,volume_6h=100,volume_24h=40000),NOW+86402,enter=False)
                self.assertEqual(len(desk.open_positions()),0)
            finally:desk.db.close()
