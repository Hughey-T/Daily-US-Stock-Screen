from __future__ import annotations
import json, subprocess, sys, tempfile, unittest
from pathlib import Path
import pandas as pd
from src.analysis_pipeline import process_all_rows, build_phase3
from src.integrity import *

class IntegrityV2Tests(unittest.TestCase):
    def frame(self, closes, splits=None, dividends=None):
        n=len(closes); return pd.DataFrame({"Close":closes,"Stock Splits":splits or [0]*n,"Dividends":dividends or [0]*n},index=pd.date_range('2026-01-01',periods=n))
    def test_ids_are_deterministic_and_routes_are_separate(self):
        e=make_entity_id('2026-01-02','abc','a'*64)
        self.assertEqual(e,make_entity_id('2026-01-02','ABC','a'*64))
        self.assertNotEqual(make_route_candidate_id(e,'event_anomaly'),make_route_candidate_id(e,'quiet_drift'))
    def test_normal_split_and_reverse_split(self):
        for f in (self.frame([100,51,52],[0,2,0]),self.frame([10,49,50],[0,.2,0])):
            self.assertEqual(reconcile_corporate_actions(f).status,'reconciled')
    def test_already_adjusted_split_is_not_transformed_twice(self):
        result=reconcile_corporate_actions(self.frame([100,100,101],[0,2,0]))
        self.assertEqual(result.status,'reconciled')
        self.assertEqual(result.action_details[0]['source_price_basis'],'split_adjusted')
        self.assertEqual(split_adjusted_prices(self.frame([100,100,101],[0,2,0])).tolist(),[100,100,101])
    def test_no_action_real_jump_is_not_artifact(self):
        self.assertEqual(reconcile_corporate_actions(self.frame([100,180,170])).status,'reconciled')
    def test_raw_adjusted_mix_detected(self):
        self.assertEqual(reconcile_corporate_actions(self.frame([100,25,26],[0,2,0])).status,'unreconciled')
    def test_action_unavailable_fails_closed(self):
        self.assertEqual(reconcile_corporate_actions(pd.DataFrame({'Close':[1,2]})).status,'unavailable')
    def test_multiple_actions(self):
        f=self.frame([100,51,52,25.5,26],[0,2,0,2,0])
        self.assertEqual(reconcile_corporate_actions(f).detected_count,2)
    def test_yfinance_1_5_1_saved_split_fixtures(self):
        fixture=json.loads(Path('tests/fixtures/corporate_actions_yfinance_1_5_1.json').read_text())
        for case in fixture['cases']:
            index=pd.bdate_range(end=case['action_date'],periods=2).append(
                pd.bdate_range(start=pd.Timestamp(case['action_date'])+pd.Timedelta(days=1),periods=1))
            frame=pd.DataFrame({'Close':case['prices'],'Dividends':[0,0,0],
                'Stock Splits':[0,case['ratio'],0],'Adj Close':case['prices']},index=index)
            frame.attrs['auto_adjust_requested']=False
            result=reconcile_corporate_actions(frame)
            self.assertEqual(result.status,case['expected_status'],case['ticker'])
            self.assertEqual(result.action_details[0]['source_price_basis'],case['expected_basis'],case['ticker'])
    def test_nontrading_action_date_uses_adjacent_trading_dates(self):
        index=pd.to_datetime(['2026-04-03','2026-04-05','2026-04-06'])
        frame=pd.DataFrame({'Close':[100,float('nan'),51], 'Dividends':[0,0,0],
                            'Stock Splits':[0,2,0]},index=index)
        result=reconcile_corporate_actions(frame)
        self.assertEqual(result.status,'reconciled')
        self.assertEqual(result.action_details[0]['selected_trading_dates'],
                         ['2026-04-03 00:00:00','2026-04-06 00:00:00'])
    def test_dividend_is_not_classified_as_split(self):
        result=reconcile_corporate_actions(self.frame([100,89,90],dividends=[0,10,0]))
        self.assertEqual(result.action_details[0]['reconciliation_method'],'cash_distribution_continuity')
    def test_threshold_categories_and_absolute_limit_are_independent(self):
        good=CorporateActionResult('reconciled',0,0,0,'passed')
        unavailable=CorporateActionResult('unavailable',0,0,0,'not_checked',('corporate_action_data_unavailable',))
        bad=CorporateActionResult('unreconciled',1,0,1,'failed',('d:split_ratio_not_reconciled',))
        self.assertEqual(corporate_action_failure_mode({'a':good,'b':unavailable},.4,.1,0),'failed')
        self.assertEqual(corporate_action_failure_mode({'a':good,'b':unavailable},.6,.1,0),'degraded')
        self.assertEqual(corporate_action_failure_mode({'a':good,'b':bad},.9,.4,5),'failed')
        self.assertEqual(corporate_action_failure_mode({'a':good,'b':good,'c':bad},.9,.9,0),'failed')
    def test_publication_status_contract(self):
        thresholds={'max_unavailable_ratio':.05,'max_unreconciled_ratio':.02,
                    'max_unreconciled_tickers':20}
        valid_success={'status':'success','corporate_action_reconciliation':{
            'status':'reconciled','unavailable_ticker_count':0,'unreconciled_ticker_count':0}}
        valid_degraded={'status':'degraded','corporate_action_reconciliation':{
            'status':'degraded','unavailable_ticker_count':1,'unreconciled_ticker_count':0,
            'unavailable_ratio':.01,'unreconciled_ratio':0,'configured_thresholds':thresholds}}
        validate_corporate_action_publication_status(valid_success)
        validate_corporate_action_publication_status(valid_degraded)
        invalid=[
            {'status':'failed','corporate_action_reconciliation':{'status':'degraded'}},
            {**valid_degraded,'corporate_action_reconciliation':{**valid_degraded['corporate_action_reconciliation'],'unavailable_ratio':.06}},
            {'status':'success','corporate_action_reconciliation':{'status':'degraded','unavailable_ticker_count':1,'unreconciled_ticker_count':0}},
        ]
        for status in invalid:
            with self.assertRaises(IntegrityError):
                validate_corporate_action_publication_status(status)
    def test_check_status_fixture_mode_success_degraded_and_failures(self):
        thresholds={'max_unavailable_ratio':.05,'max_unreconciled_ratio':.02,
                    'max_unreconciled_tickers':20}
        cases=[
            ({'status':'success','corporate_action_reconciliation':{'status':'reconciled'}},0),
            ({'status':'degraded','corporate_action_reconciliation':{'status':'degraded',
              'unavailable_ticker_count':1,'unavailable_ratio':.01,
              'configured_thresholds':thresholds}},0),
            ({'status':'failed','corporate_action_reconciliation':{'status':'degraded'}},1),
            ({'status':'degraded','corporate_action_reconciliation':{'status':'degraded',
              'unavailable_ticker_count':1,'unavailable_ratio':.06,
              'configured_thresholds':thresholds}},1),
            ({'status':'success','corporate_action_reconciliation':{'status':'degraded',
              'unavailable_ticker_count':1}},1),
        ]
        with tempfile.TemporaryDirectory() as directory:
            fixture=Path(directory)/'status.json'
            for payload,expected in cases:
                fixture.write_text(json.dumps(payload))
                result=subprocess.run([sys.executable,'scripts/check_status.py',
                    '--status-policy-fixture',str(fixture)],capture_output=True,text=True)
                self.assertEqual(result.returncode,expected,result.stdout+result.stderr)
    def test_real_format_stock_dividend_is_reconciled(self):
        f=self.frame([100,91,92]); f['Stock Dividends']=[0,.1,0]
        self.assertEqual(reconcile_corporate_actions(f).status,'reconciled')
    def test_timeline_prevents_future_leakage(self):
        with self.assertRaises(IntegrityError): validate_information_timeline('2026-01-03T12:00:00-05:00','2026-01-03T09:30:00-05:00','2026-01-03T09:30:00-05:00')
    def test_mapping_threshold_confidence_and_valuation(self):
        r={'research_status':'ready_for_deep_dive','investment_stance':'bullish_candidate','research_priority':'high'}
        self.assertEqual(display_action_class(r),'A'); self.assertEqual(neutral_threshold(21,'absolute'),.03)
        validate_confidence('medium',.65)
        with self.assertRaises(IntegrityError): validate_confidence('high',.60)
        validate_valuation('range_estimated',1,2,3)
        with self.assertRaises(IntegrityError): validate_valuation('not_started',1,None,None)
    def test_comparison_selection_and_shortage(self):
        final={'route_candidate_id':'f','entity_id':'ef','source_dataset':'event_anomaly','sector':'Tech','original_rank':1}
        control={'route_candidate_id':'c','entity_id':'ec','source_dataset':'event_anomaly','sector':'Tech','original_rank':2}
        selected,short=select_comparisons([final],[control]); self.assertEqual(len(selected),1); self.assertFalse(short)
        _,short=select_comparisons([final],[]); self.assertEqual(short[0]['selection_evaluation'],'not_evaluable')
    def test_returns_mfe_mae_and_lifecycle(self):
        price,total=calculate_returns(100,110,2); self.assertAlmostEqual(price,.1); self.assertAlmostEqual(total,.12)
        mfe,mae=calculate_mfe_mae(100,[105,110],[95,90],'up'); self.assertAlmostEqual(mfe,.1); self.assertAlmostEqual(mae,-.1)
        self.assertIn('cash_acquisition_completed',LIFECYCLE_STATUSES); self.assertIn('delisted',LIFECYCLE_STATUSES)
    def test_phase2_processes_empty_and_full_routes(self):
        rows=process_all_rows([{'ticker':'A'}],[{'ticker':'B'}],'2026-01-02','a'*64)
        result=build_phase3(rows); self.assertEqual(len(rows),2); self.assertEqual(result['audit']['event_anomaly']['unprocessed'],0)
        result=build_phase3(process_all_rows([],[],'2026-01-02','a'*64)); self.assertEqual(result['audit']['quiet_drift']['total'],0)
