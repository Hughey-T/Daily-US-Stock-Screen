from __future__ import annotations
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from src.analysis_pipeline import process_all_rows,build_phase3,select_research_set
from src.entry_resolution import resolve_generation_entries
from src.github_persistence import verify_github_prediction
from src.integrity import IntegrityError,verify_snapshot,market_session_timestamps,resolve_next_session_open
from src.records import build_prediction_bundle,persist_prediction_bundle,build_verification_bundle,persist_verification_bundle
from src.screen import save_daily_snapshot,_write_latest_manifest
from src.verification import generate_verification_records,market_data_hash
from scripts.validate_v2_records import ValidationError,validate_manifest,validate_prediction_data,validate_prediction,validate_verification_data,validate_verification

class SelectionQualityTests(unittest.TestCase):
 def processed(self,event,quiet):return process_all_rows(event,quiet,'2026-07-23','a'*64,first_tradable_at='2026-07-24T13:30:00+00:00')
 def test_combined_limit_order_independence_and_minority_route(self):
  event=[{'ticker':f'E{i}','rank':i,'signal_score':30-i,'sector':f'S{i%6}'} for i in range(1,25)]
  quiet=[{'ticker':'Q1','rank':1,'tail_distance':.9,'selection_bucket':'global_tail','sector':'Q'},{'ticker':'Q2','rank':2,'tail_distance':.8,'selection_bucket':'global_tail','sector':'Q2'}]
  rows=self.processed(event,quiet);a=select_research_set(rows);b=select_research_set(list(reversed(rows)))
  self.assertLessEqual(len(a),15);self.assertEqual({r['route_candidate_id'] for r in a},{r['route_candidate_id'] for r in b});self.assertIn('quiet_drift',{r['source_dataset'] for r in a})
 def test_low_quality_does_not_fill_and_zero_route_works(self):
  rows=self.processed([{'ticker':'LOW','signal_score':0}],[]);self.assertEqual(select_research_set(rows),[])
 def test_theme_cap_and_duplicate_entity(self):
  events=[{'ticker':f'T{i}','signal_score':10-i/10,'sector':'ONE'} for i in range(8)]
  quiet=[{'ticker':'T0','tail_distance':.9,'selection_bucket':'global_tail','sector':'TWO'}]
  selected=select_research_set(self.processed(events,quiet));self.assertLessEqual(sum(r.get('sector')=='ONE' for r in selected),4);self.assertEqual(len({r['entity_id'] for r in selected}),len(selected))
 def test_comparison_match_dimensions_and_shortage(self):
  result=build_phase3(self.processed([{'ticker':'A','signal_score':9,'industry':'Software','sector':'Tech','market_cap_band':'large','liquidity_band':'high'},{'ticker':'B','signal_score':8,'industry':'Software','sector':'Tech','market_cap_band':'large','liquidity_band':'high'}],[]))
  self.assertTrue(result['comparison_records'][0]['comparison_match']['industry_match'])
  shortage=build_phase3(self.processed([{'ticker':'ONLY','signal_score':9}],[]));self.assertEqual(shortage['shortage_records'][0]['selection_evaluation'],'not_evaluable')

class PipelineFixture(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.docs=self.root/'docs';self.docs.mkdir();self.status=self.docs/'latest.json';self.event=self.docs/'latest.csv';self.quiet=self.docs/'quiet_drift.csv'
  self.event.write_text('rank,ticker,signal_score,sector,sector_etf\n1,E1,9,Tech,XLK\n2,E2,8,Tech,XLK\n');self.quiet.write_text('rank,ticker,tail_distance,selection_bucket,sector,sector_etf\n1,Q1,.9,global_tail,Finance,XLF\n2,Q2,.8,global_tail,Finance,XLF\n')
  self.status_data={'status':'success','generated_at':'2026-07-24T00:00:00+00:00','market_data_date':'2026-07-23','market_data_cutoff_at':'2026-07-23T20:00:00+00:00','first_tradable_at':'2026-07-24T13:30:00+00:00','config_version':'test','config_hash':'a'*64,'corporate_action_reconciliation':{'status':'reconciled','unreconciled_tickers':[]}}
  self.status.write_text(json.dumps(self.status_data));self.patch=patch.multiple('src.screen',STATUS_PATH=self.status,LATEST_CSV=self.event,QUIET_DRIFT_CSV=self.quiet);self.patch.start()
 def tearDown(self):self.patch.stop();self.tmp.cleanup()
 def snapshot(self,code='fixture-code'):
  path=save_daily_snapshot('2026-07-23',code_version=code);data=json.loads(path.read_text());data['_path']=path.relative_to(self.root).as_posix();return path,data
 def price_frame(self,open_price=100,periods=30):
  index=pd.bdate_range('2026-07-24',periods=periods);close=pd.Series([float(open_price+i) for i in range(periods)],index=index)
  return pd.DataFrame({'Open':close,'High':close+2,'Low':close-2,'Close':close,'Adj Close':close,'Volume':1000,'Stock Splits':0.,'Dividends':0.},index=index)
 def entry(self,path):
  return resolve_generation_entries(self.docs/'manifest.json',self.root,lambda ticker:self.price_frame(),observed_at='2026-07-24T15:00:00+00:00')
 def research(self,phase3,mode='normal'):
  result={}
  for i,row in enumerate(phase3['final_research_set']):
   applicable=mode=='normal' and i==0
   result[row['entity_id']]={'research_status':'ready_for_deep_dive' if applicable else ('conditional' if mode=='partial' else 'unresolved'),'investment_stance':'bullish_candidate' if applicable else ('neutral_candidate' if mode=='partial' else 'not_assessed'),'situation_type':'fundamental' if applicable else 'unknown','research_priority':'high' if applicable else 'low','valuation_status':'range_estimated' if applicable else 'not_started','estimated_fundamental_value_change':{'low':.1,'base':.2,'high':.3} if applicable else {},'primary_thesis':'Primary-source-supported demand thesis.' if applicable else 'No directional thesis established.','invalidation_condition':'Revenue evidence contradicts the thesis.','research_completeness':'complete' if applicable else ('partial' if mode=='partial' else 'incomplete'),'thesis_confidence':'medium' if applicable else 'low','verified_facts':[{'statement':'Filed revenue increased.','source_url':'https://example.com/filing','published_at':'2026-07-23T18:00:00+00:00'}] if applicable else [],'company_claims':[{'statement':'Management expects growth.','source_url':'https://example.com/ir','published_at':'2026-07-23T18:00:00+00:00'}] if applicable else [],'inferences':[{'statement':'Demand may persist.','basis':'Filing and IR evidence.'}] if applicable else [],'forecast_applicable':applicable,'forecasts':[{'horizon':21,'absolute_direction':'up','sector_relative_direction':'outperform','industry_relative_direction':'unavailable','confidence':'medium','probability':.65,'benchmark_type':'sector_etf','benchmark_symbol':row.get('sector_etf') or 'SPY','benchmark_quality':'low'}] if applicable else []}
  return result
 def bundle(self,mode='normal'):
  path,snapshot=self.snapshot();phase3=json.loads((path.parent/'phase3.json').read_text());entry_path,entry=self.entry(path);research=self.research(phase3,mode);bundle=build_prediction_bundle(phase3,snapshot,research,entry,entry_path.relative_to(self.root).as_posix(),hashlib.sha256(entry_path.read_bytes()).hexdigest(),'run-1','prompt-v2','b'*64,'fixture-model','fixture-code');return path,entry_path,entry,bundle

class GenerationArtifactTests(PipelineFixture):
 def test_ci_code_identity_is_injected(self):
  with patch.dict('os.environ',{'GITHUB_SHA':'constant-ci'}):
   one,_=self.snapshot(code='one');two,_=self.snapshot(code='two')
  self.assertNotEqual(one,two)
 def test_latest_json_changes_generation_and_identical_bytes_are_idempotent(self):
  first,_=self.snapshot();again,_=self.snapshot();self.assertEqual(first,again)
  self.status_data['generated_at']='2026-07-24T00:01:00+00:00';self.status.write_text(json.dumps(self.status_data));changed,_=self.snapshot();self.assertNotEqual(first,changed)
  with self.assertRaisesRegex(RuntimeError,'older generation'):_write_latest_manifest(first,json.loads(first.read_text()))
 def test_all_artifact_hashes_and_semantics_are_verified(self):
  path,_=self.snapshot();validate_manifest(self.docs/'manifest.json',self.root)
  phase2=path.parent/'phase2.json';phase2.write_text(phase2.read_text().replace('"unprocessed": 0','"unprocessed": 1',1))
  with self.assertRaisesRegex(IntegrityError,'hash mismatch'):verify_snapshot(path)
 def test_artifact_semantic_failure_even_with_updated_hash(self):
  path,_=self.snapshot();phase2=path.parent/'phase2.json';data=json.loads(phase2.read_text());data['audit']['event_anomaly']['unprocessed']=1;phase2.write_text(json.dumps(data));snap=json.loads(path.read_text());snap['files']['phase2_artifact']['sha256']=hashlib.sha256(phase2.read_bytes()).hexdigest();path.write_text(json.dumps(snap))
  with self.assertRaisesRegex(IntegrityError,'accounting invalid'):verify_snapshot(path)
 def test_artifact_path_traversal(self):
  path,_=self.snapshot();snap=json.loads(path.read_text());snap['files']['phase2_artifact']['path']='../phase2.json';path.write_text(json.dumps(snap))
  with self.assertRaisesRegex(IntegrityError,'traversal'):verify_snapshot(path)
 def test_phase3_duplicate_entity_semantics(self):
  path,_=self.snapshot();artifact=path.parent/'phase3.json';data=json.loads(artifact.read_text());data['final_research_set'].append(data['final_research_set'][0]);artifact.write_text(json.dumps(data));snap=json.loads(path.read_text());snap['files']['phase3_artifact']['sha256']=hashlib.sha256(artifact.read_bytes()).hexdigest();snap['files']['phase3_artifact']['record_count']=len(data['final_research_set']);path.write_text(json.dumps(snap))
  with self.assertRaisesRegex(IntegrityError,'entity constraint'):verify_snapshot(path)

class ResearchPersistenceVerificationTests(PipelineFixture):
 def test_research_driven_prediction_and_local_state(self):
  _,_,_,bundle=self.bundle('normal');self.assertEqual(bundle['horizon_forecasts'][0]['absolute_direction'],'up');self.assertEqual(bundle['horizon_forecasts'][0]['forecast_probability'],.65);self.assertTrue(bundle['candidate_assessments'][0]['verified_facts'])
  path,receipt=persist_prediction_bundle(bundle,self.root,validate_prediction_data);self.assertEqual(receipt['state'],'indexed_local');validate_prediction(path,self.root)
 def test_missing_research_is_rejected_and_partial_has_no_forecast(self):
  path,snapshot=self.snapshot();phase3=json.loads((path.parent/'phase3.json').read_text());entry_path,entry=self.entry(path)
  with self.assertRaisesRegex(ValueError,'research is required'):build_prediction_bundle(phase3,snapshot,{},entry,entry_path.relative_to(self.root).as_posix(),hashlib.sha256(entry_path.read_bytes()).hexdigest(),'bad','p','b'*64,'m','c')
  _,_,_,partial=self.bundle('partial');self.assertEqual(partial['horizon_forecasts'],[]);self.assertTrue(all(a['record_group']!='forecast' for a in partial['candidate_assessments'] if a['record_group']!='comparison_only'))
 def test_entry_resolution_pending_resolved_and_idempotent(self):
  self.snapshot();pending_path,pending=resolve_generation_entries(self.docs/'manifest.json',self.root,lambda ticker:pd.DataFrame(),observed_at='2026-07-24T12:00:00+00:00');self.assertTrue(all(r['status']=='pending' for r in pending['records']))
  resolved_path,resolved=self.entry(None);again,_=self.entry(None);self.assertEqual(resolved_path,again);self.assertTrue(all(r['status']=='resolved' for r in resolved['records']))
  unavailable_path,retained=resolve_generation_entries(self.docs/'manifest.json',self.root,lambda ticker:(_ for _ in ()).throw(OSError('provider')),observed_at='2026-07-24T15:01:00+00:00');self.assertEqual(resolved_path,unavailable_path);self.assertTrue(all(r['status']=='resolved' for r in retained['records']))
 def test_verification_calculation_lifecycle_and_indexes(self):
  _,entry_path,entry,bundle=self.bundle('normal');prediction,_=persist_prediction_bundle(bundle,self.root,validate_prediction_data);frames={a['ticker']:self.price_frame() for a in bundle['candidate_assessments']};frames.update({'SPY':self.price_frame(200),'XLK':self.price_frame(150),'XLF':self.price_frame(150)})
  entries={r['route_candidate_id']:r for r in entry['records']};records=generate_verification_records(bundle,frames,entry_records=entries);forecast=next(r for r in records if r['evaluation_group']=='directional_forecast');self.assertEqual(forecast['outcome_status'],'active');self.assertIsNotNone(forecast['price_return']);self.assertIsNotNone(forecast['total_shareholder_return']);self.assertIsNotNone(forecast['mfe'])
  source_hash=market_data_hash(frames);vb=build_verification_bundle(prediction,records,self.root,'2026-09-01T22:00:00+00:00',source_hash);vp=persist_verification_bundle(vb,self.root,validate_verification_data);validate_verification(vp,self.root)
  short={key:value.iloc[:5] for key,value in frames.items()};not_due=generate_verification_records(bundle,short,entry_records=entries);self.assertEqual(next(r for r in not_due if r['evaluation_group']=='directional_forecast')['outcome_status'],'not_yet_due')
  lifecycle=generate_verification_records(bundle,frames,lifecycle={bundle['candidate_assessments'][0]['ticker']:'cash_acquisition_completed'},entry_records=entries);self.assertEqual(next(r for r in lifecycle if r['evaluation_group']=='directional_forecast')['outcome_status'],'cash_acquisition_completed')
  action_frame=self.price_frame();action_frame.iloc[10:,action_frame.columns.get_loc('Close')]/=2;action_frame.iloc[10:,action_frame.columns.get_loc('Open')]/=2;action_frame.iloc[10:,action_frame.columns.get_loc('High')]/=2;action_frame.iloc[10:,action_frame.columns.get_loc('Low')]/=2;action_frame.iloc[10,action_frame.columns.get_loc('Stock Splits')]=2;action_frame.iloc[5,action_frame.columns.get_loc('Dividends')]=1;action_frame['Adj Close']=action_frame['Close']
  action_frames=dict(frames);action_frames[bundle['candidate_assessments'][0]['ticker']]=action_frame;action_result=generate_verification_records(bundle,action_frames,entry_records=entries);self.assertIsNotNone(next(r for r in action_result if r['evaluation_group']=='directional_forecast')['price_return'])
 def test_github_proof_requires_remote_bytes_not_boolean(self):
  _,_,_,bundle=self.bundle('normal');path,_=persist_prediction_bundle(bundle,self.root,validate_prediction_data);entry=json.loads((self.docs/'predictions/index-v2.json').read_text())['predictions'][0];repo='Hughey-T/Daily-US-Stock-Screen';branch='codex-rtcqnj';sha='abc123';base='https://raw.githubusercontent.com';mapping={f'{base}/{repo}/{sha}/{entry["path"]}':path.read_bytes(),f'{base}/{repo}/{branch}/{entry["path"]}':path.read_bytes(),f'{base}/{repo}/{sha}/docs/predictions/index-v2.json':(self.docs/'predictions/index-v2.json').read_bytes()}
  proof=verify_github_prediction(entry,repo,branch,sha,lambda url:mapping[url]);self.assertEqual(proof['state'],'integrity_verified');mapping[f'{base}/{repo}/{branch}/{entry["path"]}']=b'wrong'
  with self.assertRaises(ValueError):verify_github_prediction(entry,repo,branch,sha,lambda url:mapping[url])

class CalendarTests(unittest.TestCase):
 def test_holiday_early_close_and_dst(self):
  close,next_open=market_session_timestamps('2026-11-27');self.assertIn('18:00:00',close);self.assertIn('2026-11-30',next_open)
  winter,_=market_session_timestamps('2026-01-05');summer,_=market_session_timestamps('2026-07-06');self.assertNotEqual(winter.split('T')[1][:2],summer.split('T')[1][:2])
 def test_arrived_but_missing_open_is_unavailable(self):
  result=resolve_next_session_open(pd.DataFrame(),'2026-07-24T13:30:00+00:00','2026-07-24T15:00:00+00:00');self.assertEqual(result['status'],'unavailable')
