from __future__ import annotations
import copy,hashlib,json,unittest,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from tests.test_v2_e2e import PipelineFixture
from src.write_request import (WriteRequestError,canonical_bytes,finalize_receipt,load_ledger,
 request_id,safe_failure,validate_issue_event,prepare_write)
import yaml

class WriteRequestTests(PipelineFixture):
 def setUp(self):
  super().setUp();self.snapshot_path,self.snapshot_data=self.snapshot();self.entry_path,self.entry_data=self.entry(self.snapshot_path)
  self.phase3=json.loads((self.snapshot_path.parent/'phase3.json').read_text());self.now=datetime(2026,7,24,15,1,tzinfo=timezone.utc)
 def envelope(self):
  candidates=[]
  base=self.research(self.phase3,'normal')
  for row in self.phase3['final_research_set']:
   item=copy.deepcopy(base[row['entity_id']]);item['route_candidate_id']=row['route_candidate_id'];item['falsifier']=item.pop('invalidation_condition');item['valuation_range']=item.pop('estimated_fundamental_value_change');item['valuation_range']={k:item['valuation_range'].get(k) for k in ('low','base','high')};item['monitor_only_reason']=None if item['forecast_applicable'] else 'Evidence is incomplete.';item['comparison_references']=[];item.setdefault('estimation_method',None);item.setdefault('estimation_limitations',[])
   for evidence in item['verified_facts']+item['company_claims']:
    evidence['document_date']='2026-07-23';evidence['information_cutoff_eligible']=True
   candidates.append(item)
  payload={'candidates':candidates};ph=hashlib.sha256(canonical_bytes(payload)).hexdigest();generation=self.snapshot_data['generation_id'];prompt='b'*64
  rid=request_id(generation,prompt,ph)
  return {'request_schema_version':'1.0','operation':'persist_prediction_v2','request_id':rid,'nonce':str(uuid.uuid4()),'created_at':self.now.isoformat(),'expires_at':(self.now+timedelta(minutes=5)).isoformat(),'repository':'Hughey-T/Daily-US-Stock-Screen','source_snapshot_id':self.snapshot_data['snapshot_id'],'source_generation_id':generation,'source_snapshot_path':self.snapshot_path.relative_to(self.root).as_posix(),'prompt_version':'schema-2.0-seven-phase-v3','prompt_hash':prompt,'model_name':'test-model','model_configuration':{},'payload_sha256':ph,'research_payload':payload}
 def issue_event(self,envelope=None):
  envelope=envelope or self.envelope();return {'action':'opened','repository':{'full_name':'Hughey-T/Daily-US-Stock-Screen'},'sender':{'login':'Hughey-T'},'issue':{'number':7,'user':{'login':'Hughey-T'},'author_association':'OWNER','title':'[GPT-WRITE-V2] '+envelope['request_id'],'body':json.dumps(envelope)}}
 def assert_code(self,code,event):
  with self.assertRaises(WriteRequestError) as ctx:validate_issue_event(event,self.root,self.now)
  self.assertEqual(ctx.exception.code,code)
 def test_valid_request_and_server_side_persistence(self):
  req,*_=validate_issue_event(self.issue_event(),self.root,self.now);self.assertEqual(req['operation'],'persist_prediction_v2')
  result=prepare_write(self.issue_event(),self.root,self.now);self.assertFalse(result['replay']);self.assertEqual(result['receipt']['write_request_status'],'pending_remote_verification');self.assertEqual(result['local_receipt_state'],'indexed_local')
  bundle=json.loads((self.root/result['receipt']['prediction_path']).read_text());self.assertEqual(bundle['horizon_forecasts'][0]['absolute_direction'],'up');self.assertNotIn('display_action_class',req['research_payload']['candidates'][0])
 def test_auth_repository_title_json_and_size(self):
  for code,mutate in [('WRONG_REPOSITORY',lambda e:e['repository'].update(full_name='x/y')),('UNAUTHORIZED_ACTOR',lambda e:e['sender'].update(login='attacker')),('UNAUTHORIZED_ASSOCIATION',lambda e:e['issue'].update(author_association='CONTRIBUTOR')),('INVALID_TITLE',lambda e:e['issue'].update(title='bad'))]:
   e=self.issue_event();mutate(e);self.assert_code(code,e)
  e=self.issue_event();e['issue']['body']='{';self.assert_code('INVALID_JSON',e)
  e=self.issue_event();e['issue']['body']='x'*51201;self.assert_code('BODY_TOO_LARGE',e)
 def test_schema_unknown_nonce_hash_and_request_id(self):
  x=self.envelope();x['unknown']=1;self.assert_code('SCHEMA_INVALID',self.issue_event(x))
  x=self.envelope();x['nonce']=str(uuid.uuid1());self.assert_code('INVALID_NONCE',self.issue_event(x))
  x=self.envelope();x['payload_sha256']='0'*64;self.assert_code('PAYLOAD_HASH_MISMATCH',self.issue_event(x))
  x=self.envelope();x['request_id']='gptw_'+'0'*64;self.assert_code('REQUEST_ID_MISMATCH',self.issue_event(x))
 def test_expiry_and_future(self):
  x=self.envelope();x['expires_at']=(self.now+timedelta(minutes=11)).isoformat();self.assert_code('EXPIRY_WINDOW_INVALID',self.issue_event(x))
  x=self.envelope();x['created_at']=(self.now-timedelta(minutes=6)).isoformat();x['expires_at']=(self.now-timedelta(minutes=1)).isoformat();self.assert_code('REQUEST_EXPIRED',self.issue_event(x))
 def test_source_candidate_entry_and_leakage(self):
  x=self.envelope();x['source_snapshot_id']='wrong-id';self.assert_code('SOURCE_IDENTITY_MISMATCH',self.issue_event(x))
  x=self.envelope();x['source_snapshot_path']='docs/generations/../secret';self.assert_code('SCHEMA_INVALID',self.issue_event(x))
  x=self.envelope();x['research_payload']['candidates'][0]['route_candidate_id']='route_'+'0'*64;self._rehash(x);self.assert_code('UNKNOWN_OR_DUPLICATE_CANDIDATE',self.issue_event(x))
  x=self.envelope();x['research_payload']['candidates'][0]['verified_facts'][0]['published_at']='2026-07-25T00:00:00+00:00';self._rehash(x);self.assert_code('FUTURE_INFORMATION_LEAKAGE',self.issue_event(x))
  data=json.loads(self.entry_path.read_text());target=x=self.envelope();rid=x['research_payload']['candidates'][0]['route_candidate_id'];next(r for r in data['records'] if r['route_candidate_id']==rid)['status']='pending';self.entry_path.write_text(json.dumps(data));idx=json.loads((self.root/'docs/entry-resolutions/index.json').read_text());idx['generations'][self.snapshot_data['generation_id']]['sha256']=hashlib.sha256(self.entry_path.read_bytes()).hexdigest();(self.root/'docs/entry-resolutions/index.json').write_text(json.dumps(idx));self.assert_code('ENTRY_UNRESOLVED',self.issue_event(x))
 def test_placeholder_missing_forecast_and_monitor(self):
  x=self.envelope();x['research_payload']['candidates'][0]['primary_thesis']='TODO pending analysis';self._rehash(x);self.assert_code('PLACEHOLDER_RESEARCH',self.issue_event(x))
  x=self.envelope();x['research_payload']['candidates'][0]['forecasts']=[];self._rehash(x);self.assert_code('FORECAST_RESEARCH_INCOMPLETE',self.issue_event(x))
  x=self.envelope();item=x['research_payload']['candidates'][0];item['forecast_applicable']=False;item['forecasts']=[];item['research_status']='unresolved';item['research_completeness']='incomplete';item['monitor_only_reason']='Cause not resolved.';self._rehash(x);validate_issue_event(self.issue_event(x),self.root,self.now)
 def test_replay_nonce_and_conflict(self):
  e=self.issue_event();first=prepare_write(e,self.root,self.now);finalize_receipt(self.root,first['receipt']['request_id'],'a'*40,{'state':'integrity_verified','sha256':first['receipt']['prediction_sha256']},self.now.isoformat());again=prepare_write(e,self.root,self.now);self.assertTrue(again['replay']);self.assertEqual(first['receipt']['prediction_path'],again['receipt']['prediction_path'])
  x=self.envelope();x['nonce']=json.loads(e['issue']['body'])['nonce'];x['prompt_hash']='c'*64;self._rehash(x);self.assert_code('NONCE_REUSED',self.issue_event(x))
  ledger=load_ledger(self.root/'docs/write-requests/ledger.json');ledger['requests'][0]['payload_sha256']='0'*64;(self.root/'docs/write-requests/ledger.json').write_text(json.dumps(ledger));self.assert_code('REQUEST_ID_PAYLOAD_CONFLICT',e)
 def test_remote_finalization_and_sanitized_failure(self):
  result=prepare_write(self.issue_event(),self.root,self.now);proof={'state':'integrity_verified','sha256':result['receipt']['prediction_sha256']};receipt=finalize_receipt(self.root,result['receipt']['request_id'],'a'*40,proof,self.now.isoformat());self.assertEqual(receipt['write_request_status'],'integrity_verified')
  with self.assertRaises(WriteRequestError):finalize_receipt(self.root,result['receipt']['request_id'],'a'*40,{'state':'failed'})
  failure=safe_failure('x','BAD\nsecret=value');self.assertNotIn('secret',json.dumps(failure));self.assertEqual(failure['error_code'],'BAD')
 def test_command_metacharacters_are_data(self):
  x=self.envelope();x['research_payload']['candidates'][0]['primary_thesis']='Evidence supports $(touch /tmp/never-created); this remains plain text.';self._rehash(x);validate_issue_event(self.issue_event(x),self.root,self.now);self.assertFalse(Path('/tmp/never-created').exists())
 def _rehash(self,x):
  x['payload_sha256']=hashlib.sha256(canonical_bytes(x['research_payload'])).hexdigest();x['request_id']=request_id(x['source_generation_id'],x['prompt_hash'],x['payload_sha256'])

if __name__=='__main__':unittest.main()

class WriterWorkflowSecurityTests(unittest.TestCase):
 def test_openapi_surface_and_workflow_trust_boundary(self):
  root=Path(__file__).resolve().parents[1];api=yaml.safe_load((root/'openapi/gpt-write-action.yaml').read_text())
  self.assertEqual(set(api['paths']),{'/repos/Hughey-T/Daily-US-Stock-Screen/issues','/repos/Hughey-T/Daily-US-Stock-Screen/issues/{issue_number}','/repos/Hughey-T/Daily-US-Stock-Screen/issues/{issue_number}/comments'})
  self.assertEqual(sum(method in {'get','post'} for item in api['paths'].values() for method in item),3)
  text=(root/'.github/workflows/process-gpt-write-request.yml').read_text();workflow=yaml.safe_load(text)
  self.assertIn('issues',workflow.get('on',workflow.get(True)))
  permissions=workflow['jobs']['process']['permissions'];self.assertEqual(permissions,{'contents':'write','issues':'write'})
  self.assertNotIn('pull_request_target',text);self.assertNotIn('force',text);self.assertIn('ref: main',text)
  self.assertNotIn('${{ github.event.issue.body',text);self.assertNotIn('${{ github.event.issue.title',text)
