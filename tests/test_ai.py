import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from desk.ai import AIPolicy, AIProviders, evaluate, token_questions, validate_jev
from desk.core import Desk, Rules
from test_desk import NOW, sample
from test_preflight import TOKEN, evidence


def response(questions, chosen=None):
    answers = {}
    for key,q in questions.items():
        if q['type']=='noul':
            answers[key] = dict(type='noul',noul=.95)
        else:
            pick = chosen or next(iter(q['criteria']))
            answers[key] = dict(type='choice',choice=pick,confidence=.9,
                                probabilities={k:float(k==pick) for k in q['criteria']})
    return dict(model='jev-test-version',answers=answers,usage=dict(input_tokens=100,output_tokens=20))


class ProviderTests(unittest.TestCase):
    def test_jev_bad_answers_rejected(self):
        questions = token_questions()
        for mutation in ('nan','bool','missing','unknown','sum','winner','confidence','usage'):
            with self.subTest(mutation=mutation):
                reply = response(questions)
                if mutation=='nan': reply['answers']['evidence_supports_entry']['noul'] = float('nan')
                if mutation=='bool': reply['answers']['evidence_supports_entry']['noul'] = True
                if mutation=='missing': reply['answers'].pop('shape')
                if mutation=='unknown': reply['answers']['shape']['probabilities']['invented'] = 0
                if mutation=='sum': reply['answers']['shape']['probabilities']['fading'] = .5
                if mutation=='winner': reply['answers']['shape']['choice'] = 'fading'
                if mutation=='confidence': reply['answers']['shape']['confidence'] = 2
                if mutation=='usage': reply['usage']['input_tokens'] = -1
                with self.assertRaises(ValueError): validate_jev(reply,questions)

    @patch.dict('os.environ',{'TYPESAFE_API_KEY':'private-test-key','XAI_API_KEY':'another-test-key'})
    def test_official_jev_contract_and_secret_not_recorded(self):
        recorder,http = Mock(),Mock()
        http.request.return_value = (response(token_questions()),NOW)
        provider = AIProviders(recorder,http=http)
        result = provider.judge(dict(token=TOKEN),token_questions())
        self.assertEqual(result['model'],'jev-test-version')
        self.assertEqual(http.request.call_args.args[0],'https://api.typesafe.ai/v1/systemone')
        self.assertEqual(http.request.call_args.kwargs['payload']['model'],'jev-latest')
        self.assertNotIn('private-test-key',str(recorder.call_args_list))
        self.assertEqual(provider.calls,1)

    @patch.dict('os.environ',{'XAI_API_KEY':'test'})
    def test_grok_structured_contract_has_no_execution_tools(self):
        http,recorder = Mock(),Mock()
        parsed = dict(token=TOKEN,verdict='consider',evidence_fields=['observations'],missing_fields=[])
        http.request.return_value = (dict(model='grok-version',choices=[dict(finish_reason='stop',message=dict(content=json.dumps(parsed)))],usage={'prompt_tokens':100}),NOW)
        result = AIProviders(recorder,http=http).analyze(dict(token=TOKEN,observations=[1,2]))
        self.assertEqual(result['model'],'grok-version')
        self.assertEqual(http.request.call_args.args[0],'https://api.x.ai/v1/chat/completions')
        payload = http.request.call_args.kwargs['payload']
        self.assertEqual(payload['response_format']['type'],'json_schema')
        self.assertTrue(payload['response_format']['json_schema']['strict'])
        self.assertNotIn('tools',payload)
        self.assertEqual(payload['max_tokens'],700)

    @patch.dict('os.environ',{'XAI_API_KEY':'test'})
    def test_grok_rejects_invented_evidence_and_wrong_mint(self):
        state = dict(token=TOKEN,observations=[1,2],social_evidence=None)
        for field,token,finish in [('social_evidence',TOKEN,'stop'),('observations','wrong','stop'),('observations',TOKEN,'length')]:
            http = Mock()
            parsed = dict(token=token,verdict='consider',evidence_fields=[field],missing_fields=[])
            http.request.return_value = (dict(model='grok-test',choices=[dict(finish_reason=finish,message=dict(content=json.dumps(parsed)))]),NOW)
            with self.assertRaises(ValueError): AIProviders(Mock(),http=http).analyze(state)

    @patch.dict('os.environ',{},clear=True)
    def test_missing_credentials_and_budget_do_not_call_network(self):
        http = Mock()
        provider = AIProviders(Mock(),http=http)
        with self.assertRaises(ValueError): provider.ready()
        with self.assertRaises(ValueError): provider.judge({},token_questions())
        self.assertFalse(http.request.called)
        provider.calls = provider.policy.max_calls
        with self.assertRaises(ValueError): provider.judge({},token_questions())
        self.assertFalse(http.request.called)


class PaperAITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.desk = Desk(Path(self.tmp.name)/'paper.sqlite',replace(Rules(),require_preflight=True,ticket=5,max_top10_owner_pct=60))
        self.snap = sample(token=TOKEN,preflight=evidence(),holders=evidence()['holders'])
        self.providers = Mock(calls=3)
        self.providers.analyze.return_value = dict(model='grok-test',analysis=dict(token=TOKEN,verdict='consider',
            evidence_fields=['observations'],missing_fields=[]))
        self.providers.judge.side_effect = lambda state,questions:response(questions)

    def tearDown(self):
        self.desk.close()
        self.tmp.cleanup()

    def seed(self, token=TOKEN):
        run = self.desk.run('history',NOW-10)
        s = copy.deepcopy(self.snap)
        s.update(token=token,observed_at=NOW-10)
        self.desk.record(run,s,NOW-10,enter=False)

    def cycle(self, snapshots=None):
        run = self.desk.run('ai-paper',NOW)
        with patch('desk.ai.time.time',return_value=NOW):
            result = evaluate(self.desk,run,snapshots or [self.snap],self.providers)
        return run,result

    def test_two_stage_pick_produces_paper_fill_with_audit_link(self):
        self.seed()
        run,result = self.cycle()
        self.assertEqual(result['selected_token'],TOKEN)
        self.assertEqual(len(self.desk.open_positions()),1)
        snapshot = json.loads(self.desk.db.execute('SELECT data FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()[0])
        decision = self.desk.db.execute('SELECT stage FROM ai_decisions WHERE id=?',(snapshot['ai_decision_id'],)).fetchone()
        self.assertEqual(decision[0],'pick')
        self.assertEqual(self.desk.db.execute('SELECT state FROM ai_cycles WHERE run_id=?',(run,)).fetchone()[0],'complete')
        self.assertFalse(result['ready_for_live'])
        self.assertIsNone(self.desk.db.execute("SELECT name FROM sqlite_master WHERE name='live_orders'").fetchone())

    def test_selected_entry_refresh_after_ai_latency(self):
        self.seed()
        refreshed=copy.deepcopy(self.snap)
        refreshed['observed_at']=NOW+60
        for part in ('mint','holders'):refreshed['preflight'][part]['observed_at']=NOW+60
        refreshed['holders']['observed_at']=NOW+60
        for leg in ('buy','sell'):refreshed['preflight']['quotes'][leg]['observed_at']=NOW+60
        refresh=Mock(return_value=refreshed)
        clock=[NOW]
        def judge(state,questions):
            if 'worth_trading' in questions: clock[0]=NOW+60
            return response(questions)
        self.providers.judge.side_effect=judge
        run=self.desk.run('ai-paper',NOW)
        with patch('desk.ai.time.time',side_effect=lambda:clock[0]):
            result=evaluate(self.desk,run,[self.snap],self.providers,refresh_selected=refresh)
        self.assertEqual(result['paper_buys'],1)
        self.assertEqual(refresh.call_count,1)

    def test_selected_refresh_failure_cannot_create_fill(self):
        self.seed();run=self.desk.run('ai-paper',NOW)
        with patch('desk.ai.time.time',return_value=NOW):
            result=evaluate(self.desk,run,[self.snap],self.providers,refresh_selected=Mock(side_effect=RuntimeError('failed')))
        self.assertEqual(result['paper_buys'],0)
        self.assertIsNone(result['selected_token'])

    def test_cold_start_warms_up_without_ai_or_fill(self):
        _,result = self.cycle()
        self.assertIsNone(result['selected_token'])
        self.assertFalse(self.providers.analyze.called)
        self.assertEqual(self.desk.open_positions(),[])

    def test_absolute_gate_can_reject_relative_winner(self):
        self.seed()
        def judge(state,questions):
            reply = response(questions)
            if 'worth_trading' in questions: reply['answers']['worth_trading']['noul'] = .3
            return reply
        self.providers.judge.side_effect = judge
        _,result = self.cycle()
        self.assertIsNone(result['selected_token'])
        self.assertEqual(self.desk.open_positions(),[])

    def test_provider_failure_blocks_every_entry_and_does_not_log_secret(self):
        self.seed()
        self.providers.judge.side_effect = RuntimeError('secret-auth-token')
        _,result = self.cycle()
        self.assertEqual(result['state'],'blocked')
        self.assertEqual(self.desk.open_positions(),[])
        logs = str([tuple(row) for row in self.desk.db.execute('SELECT * FROM ai_decisions')])
        self.assertNotIn('secret-auth-token',logs)

    def test_deterministic_rejection_never_reaches_ai(self):
        self.seed()
        self.snap['liquidity'] = 1
        self.cycle()
        self.assertFalse(self.providers.analyze.called)
        self.assertEqual(self.desk.open_positions(),[])

    def test_early_rejection_does_not_invent_unperformed_checks(self):
        self.snap.pop('preflight')
        self.snap['holders'] = None
        self.snap['early_rejection_reasons'] = ['pair_age_out_of_range']
        run,result = self.cycle()
        row = self.desk.db.execute('SELECT data FROM ai_decisions WHERE run_id=?',(run,)).fetchone()
        data = json.loads(row[0])
        self.assertEqual(data['reasons'],['pair_age_out_of_range'])
        self.assertTrue(data['preflight_skipped'])
        self.assertFalse(self.providers.analyze.called)
        self.assertEqual(result['paper_buys'],0)

    def test_expired_market_after_ai_cannot_fill(self):
        self.seed()
        def analyze(state):
            self.snap['observed_at'] = NOW-1000
            return self.providers.analyze.return_value
        self.providers.analyze.side_effect = analyze
        self.cycle()
        self.assertEqual(self.desk.open_positions(),[])

    def test_none_option_rejects_all_candidates(self):
        self.seed()
        self.providers.judge.side_effect = lambda state,q:response(q,'NONE' if 'best' in q else None)
        _,result = self.cycle()
        self.assertIsNone(result['selected_token'])
        self.assertEqual(self.desk.open_positions(),[])

    def test_low_confidence_prevents_shortlisting(self):
        self.seed()
        def judge(state,q):
            reply = response(q)
            reply['answers']['shape']['confidence'] = .2
            return reply
        self.providers.judge.side_effect = judge
        _,result = self.cycle()
        self.assertEqual(result['shortlisted'],0)
        self.assertEqual(result['paper_buys'],0)
        self.assertEqual(self.providers.judge.call_count,1)

    def test_grok_reject_prevents_jev_call(self):
        self.seed()
        self.providers.analyze.return_value['analysis']['verdict'] = 'reject'
        _,result = self.cycle()
        self.assertFalse(self.providers.judge.called)
        self.assertEqual(result['paper_buys'],0)


class ObservationHistoryTests(unittest.TestCase):
    def test_judge_history_spans_the_hour_not_the_last_three_feed_ticks(self):
        from desk.ai import state_for
        from desk.core import encode
        from desk.discovery_feed import init as init_feed
        with tempfile.TemporaryDirectory() as d:
            desk = Desk(Path(d) / 'paper.sqlite', Rules())
            try:
                init_feed(desk)
                current = sample(now=NOW)
                with desk.db:
                    for age in range(100, 3001, 100):
                        s = sample(price=1 + age / 3000, now=NOW - age)
                        desk.db.execute('INSERT INTO launch_observations VALUES (?,?,?,?)', (s['token'], s['pair'], s['observed_at'], encode(s)))
                seen = [NOW - o['observed_at'] for o in state_for(desk, current)['observations']]
                self.assertEqual(seen, [3000, 2000, 1000, 0])
            finally:
                desk.close()
