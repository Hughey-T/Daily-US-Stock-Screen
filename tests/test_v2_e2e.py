from __future__ import annotations
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from jsonschema.exceptions import ValidationError as JsonSchemaError
from src.analysis_pipeline import process_all_rows,build_phase3,select_research_set
from src.integrity import *
from src.records import build_prediction_bundle,persist_prediction_bundle,build_verification_bundle,persist_verification_bundle
from src.screen import save_daily_snapshot
from scripts.validate_v2_records import (ValidationError,validate_manifest,validate_prediction_data,validate_prediction,validate_verification_data,validate_verification,_validate_schema)

class SelectionTests(unittest.TestCase):
 def rows(self):
  event=[{'ticker':f'E{i}','rank':i,'signal_score':20-i,'sector':'S'+str(i%5),'sector_etf':'XLK'} for i in range(1,21)]
  quiet=[{'ticker':f'Q{i}','rank':i,'tail_distance':1-i/100,'selection_bucket':'global_tail' if i<8 else 'sector_coverage','sector':'Q'+str(i%5),'sector_etf':'XLF'} for i in range(1,21)]
  return process_all_rows(event,quiet,'2026-07-23','a'*64)
 def test_max_15_order_independent_and_route_diverse(self):
  rows=self.rows(); a=select_research_set(rows,15); b=select_research_set(list(reversed(rows)),15)
  self.assertLessEqual(len({r['entity_id'] for r in a}),15);self.assertEqual({r['route_candidate_id'] for r in a},{r['route_candidate_id'] for r in b});self.assertEqual({r['source_dataset'] for r in a},{'event_anomaly','quiet_drift'})
 def test_duplicate_entity_is_researched_once(self):
  rows=process_all_rows([{'ticker':'SAME','signal_score':9},{'ticker':'E2','signal_score':8}],[{'ticker':'SAME','tail_distance':1,'selection_bucket':'global_tail'},{'ticker':'Q2','tail_distance':.9,'selection_bucket':'global_tail'}],'2026-07-23','a'*64)
  selected=select_research_set(rows,15);self.assertEqual(sum(r['ticker']=='SAME' for r in selected),1)
 def test_comparison_shortage_is_not_evaluable(self):
  result=build_phase3(process_all_rows([{'ticker':'ONLY','signal_score':9}],[],'2026-07-23','a'*64));self.assertEqual(result['shortage_records'][0]['selection_evaluation'],'not_evaluable')

class GenerationAndE2ETests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.docs=self.root/'docs';self.docs.mkdir()
  self.status=self.docs/'latest.json';self.event=self.docs/'latest.csv';self.quiet=self.docs/'quiet_drift.csv'
  self.event.write_text('rank,ticker,signal_score,sector,sector_etf\n1,E1,9,Tech,XLK\n2,E2,8,Tech,XLK\n',encoding='utf-8')
  self.quiet.write_text('rank,ticker,tail_distance,selection_bucket,sector,sector_etf\n1,Q1,.9,global_tail,Finance,XLF\n2,Q2,.8,global_tail,Finance,XLF\n',encoding='utf-8')
  self.status_data={'status':'success','generated_at':'2026-07-24T00:00:00+00:00','market_data_date':'2026-07-23','market_data_cutoff_at':'2026-07-23T20:00:00+00:00','first_tradable_at':'2026-07-24T13:30:00+00:00','config_version':'test','config_hash':'a'*64,'corporate_action_reconciliation':{'status':'reconciled','unreconciled_tickers':[]}}
  self.status.write_text(json.dumps(self.status_data),encoding='utf-8')
  self.patches=patch.multiple('src.screen',STATUS_PATH=self.status,LATEST_CSV=self.event,QUIET_DRIFT_CSV=self.quiet);self.patches.start()
 def tearDown(self):self.patches.stop();self.tmp.cleanup()
 def snapshot(self):
  path=save_daily_snapshot('2026-07-23');data=json.loads(path.read_text());data['_path']=path.relative_to(self.root).as_posix();return path,data
 def test_idempotent_and_same_date_new_generation(self):
  p1,_=self.snapshot();p2,_=self.snapshot();self.assertEqual(p1,p2)
  self.event.write_text(self.event.read_text().replace(',9,',',10,'));p3,_=self.snapshot();self.assertNotEqual(p1,p3);self.assertTrue(p1.exists())
 def test_same_source_new_code_creates_generation(self):
  with patch.dict('os.environ',{'CODE_VERSION':'one'}):p1,_=self.snapshot()
  with patch.dict('os.environ',{'CODE_VERSION':'two'}):p2,_=self.snapshot()
  self.assertNotEqual(p1,p2)
 def test_partial_generation_is_rejected(self):
  path,_=self.snapshot();path.unlink()
  with self.assertRaisesRegex(RuntimeError,'partial generation'):save_daily_snapshot('2026-07-23')
 def test_manifest_hash_and_traversal(self):
  path,_=self.snapshot();validate_manifest(self.docs/'manifest.json',self.root)
  m=json.loads((self.docs/'manifest.json').read_text());m['snapshot_sha256']='0'*64;(self.docs/'manifest.json').write_text(json.dumps(m))
  with self.assertRaises(IntegrityError):validate_manifest(self.docs/'manifest.json',self.root)
  m['snapshot_path']='../escape.json';(self.docs/'manifest.json').write_text(json.dumps(m))
  with self.assertRaises((IntegrityError,ValidationError)):validate_manifest(self.docs/'manifest.json',self.root)
 def test_mutable_latest_divergence_is_detected(self):
  self.snapshot();self.event.write_text(self.event.read_text().replace('E1','OTHER'))
  with self.assertRaisesRegex(IntegrityError,'diverges'):validate_manifest(self.docs/'manifest.json',self.root)
 def test_prediction_verification_persistence_e2e(self):
  path,snapshot=self.snapshot();phase3=json.loads((path.parent/'phase3.json').read_text());phase2=json.loads((path.parent/'phase2.json').read_text());self.assertEqual(phase2['audit']['event_anomaly']['unprocessed'],0)
  entries={r['route_candidate_id']:{'status':'resolved','first_tradable_at':'2026-07-24T13:30:00+00:00','entry_price_timestamp':'2026-07-24T13:30:00+00:00','entry_price':100.0} for r in phase3['final_research_set']}
  bundle=build_prediction_bundle(phase3,snapshot,'run-1','prompt-v2','b'*64,'fixture-model','fixture-sha',entries,horizons=(21,))
  validate=lambda data,root:validate_prediction_data(data,root);prediction,receipt=persist_prediction_bundle(bundle,self.root,validate,confirm_repository_persistence=True);self.assertEqual(receipt['state'],'integrity_verified');validate_prediction(prediction,self.root)
  records=[{'record_id':f['prediction_id'],'prediction_id':f['prediction_id'],'route_candidate_id':f['route_candidate_id'],'verification_horizon':f['horizon'],'outcome_status':'active','price_return':.1,'total_shareholder_return':.11,'spy_relative_return':.05,'sector_relative_return':.04,'industry_relative_return':None,'mfe':.15,'mae':-.03,'evaluation_group':'directional_forecast'} for f in bundle['horizon_forecasts']]
  vb=build_verification_bundle(prediction,records,self.root);vp=persist_verification_bundle(vb,self.root,lambda d,r:validate_verification_data(d,r));validate_verification(vp,self.root);self.assertEqual(hashlib.sha256(prediction.read_bytes()).hexdigest(),vb['source_prediction_sha256'])
  index=json.loads((self.docs/'predictions/index-v2.json').read_text());index['predictions'][0]['sha256']='0'*64;(self.docs/'predictions/index-v2.json').write_text(json.dumps(index))
  with self.assertRaises(ValueError):persist_prediction_bundle(bundle,self.root,validate,confirm_repository_persistence=True)
 def test_negative_entry_and_unknown_property_fail_schema(self):
  path,snapshot=self.snapshot();phase3=json.loads((path.parent/'phase3.json').read_text())
  entries={r['route_candidate_id']:{'status':'resolved','first_tradable_at':'2026-07-24T13:30:00+00:00','entry_price_timestamp':'2026-07-24T13:30:00+00:00','entry_price':-1} for r in phase3['final_research_set']}
  bundle=build_prediction_bundle(phase3,snapshot,'bad','p','b'*64,'m','c',entries,(21,))
  with self.assertRaises(ValidationError):validate_prediction_data(bundle,self.root)
  bundle['unknown']=1
  with self.assertRaises(ValidationError):validate_prediction_data(bundle,self.root)
 def test_schema_rejects_missing_bad_datetime_hash_and_nonfinite(self):
  path,snapshot=self.snapshot();phase3=json.loads((path.parent/'phase3.json').read_text());entries={r['route_candidate_id']:{'status':'resolved','first_tradable_at':'2026-07-24T13:30:00+00:00','entry_price_timestamp':'2026-07-24T13:30:00+00:00','entry_price':1} for r in phase3['final_research_set']}
  base=build_prediction_bundle(phase3,snapshot,'formats','p','b'*64,'m','c',entries,(21,))
  for mutate in (lambda d:d.pop('prompt_hash'),lambda d:d.__setitem__('generated_at','not-a-date'),lambda d:d.__setitem__('config_hash','bad'),lambda d:d['horizon_forecasts'][0].__setitem__('entry_price',float('nan'))):
   data=json.loads(json.dumps(base));mutate(data)
   with self.assertRaises(ValidationError):validate_prediction_data(data,self.root)

class CalendarAndCorporateTests(unittest.TestCase):
 def test_early_close_and_next_session(self):
  close,next_open=market_session_timestamps('2026-11-27');self.assertIn('18:00:00',close);self.assertIn('2026-11-30',next_open)
 def test_dst_sessions_have_real_utc_offsets(self):
  winter,_=market_session_timestamps('2026-01-05');summer,_=market_session_timestamps('2026-07-06');self.assertNotEqual(winter.split('T')[1][:2],summer.split('T')[1][:2])
 def test_entry_pending_resolved_and_unavailable(self):
  import pandas as pd
  first='2026-07-24T13:30:00+00:00'; frame=pd.DataFrame({'Open':[101.]},index=[pd.Timestamp('2026-07-24')])
  self.assertEqual(resolve_next_session_open(frame,first,'2026-07-24T12:00:00+00:00')['status'],'pending')
  self.assertEqual(resolve_next_session_open(frame,first,'2026-07-24T14:00:00+00:00')['status'],'resolved')
  self.assertEqual(resolve_next_session_open(pd.DataFrame(),first,'2026-07-24T14:00:00+00:00')['status'],'unavailable')
 def test_special_distribution_reconciliation(self):
  import pandas as pd
  f=pd.DataFrame({'Close':[100,81,82],'Stock Splits':[0,0,0],'Dividends':[0,20,0]});r=reconcile_corporate_actions(f);self.assertEqual(r.status,'reconciled');self.assertAlmostEqual(r.action_details[0]['distribution_amount'],20)
 def test_stock_dividend_reconciliation(self):
  import pandas as pd
  f=pd.DataFrame({'Close':[100,91,92],'Stock Splits':[0,0,0],'Stock Dividends':[0,.1,0],'Dividends':[0,0,0]});r=reconcile_corporate_actions(f);self.assertEqual(r.status,'reconciled');self.assertEqual(r.action_details[0]['action_type'],'stock_dividend')
 def test_provider_wide_failure(self):
  unavailable=CorporateActionResult('unavailable',0,0,0,'not_checked',('missing',),())
  self.assertEqual(corporate_action_failure_mode({'A':unavailable,'B':unavailable},.05,.02,20),'failed')
