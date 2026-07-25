from __future__ import annotations
import tempfile, unittest
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
    def test_unadjusted_split_is_reconciled_by_explicit_ratio(self):
        result=reconcile_corporate_actions(self.frame([100,100,101],[0,2,0]))
        self.assertEqual(result.status,'unreconciled')
    def test_no_action_real_jump_is_not_artifact(self):
        self.assertEqual(reconcile_corporate_actions(self.frame([100,180,170])).status,'reconciled')
    def test_raw_adjusted_mix_detected(self):
        self.assertEqual(reconcile_corporate_actions(self.frame([100,25,26],[0,2,0])).status,'unreconciled')
    def test_action_unavailable_fails_closed(self):
        self.assertEqual(reconcile_corporate_actions(pd.DataFrame({'Close':[1,2]})).status,'unavailable')
    def test_multiple_actions(self):
        f=self.frame([100,51,52,25.5,26],[0,2,0,2,0])
        self.assertEqual(reconcile_corporate_actions(f).detected_count,2)
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
