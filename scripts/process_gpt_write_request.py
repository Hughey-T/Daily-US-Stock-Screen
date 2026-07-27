#!/usr/bin/env python3
"""Process and recover trusted GPT Issue write requests."""
from __future__ import annotations
import argparse,json,os,subprocess,sys,time,urllib.error,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.github_persistence import verify_github_prediction
from src.write_request import (WriteRequestError,audit_ledger_write,finalize_receipt,load_ledger,mark_verification_attempt,
 prepare_write,receipt_hash,safe_failure)
STATE=Path(os.environ.get('RUNNER_TEMP','/tmp'))/'gpt-write-state.json';REPOSITORY='Hughey-T/Daily-US-Stock-Screen'
RETRY_HTTP={404,429,502,503,504}
class RemoteTransientError(RuntimeError):pass

def _write_state(value):STATE.write_text(json.dumps(value,indent=2)+'\n')
def _output(status):
 path=os.environ.get('GITHUB_OUTPUT')
 if path:
  with open(path,'a') as out:out.write(f'status={status}\n')
def fetch_with_retry(url,opener=urllib.request.urlopen,sleep=time.sleep,attempts=5):
 request=urllib.request.Request(url,headers={'User-Agent':'Daily-US-Stock-Screen-writer/2.0'});last=None
 for attempt in range(attempts):
  try:
   with opener(request,timeout=30) as response:return response.read()
  except urllib.error.HTTPError as exc:
   if exc.code not in RETRY_HTTP:raise
   last=exc
  except (urllib.error.URLError,TimeoutError,OSError) as exc:last=exc
  if attempt+1<attempts:sleep(min(2**attempt,16))
 raise RemoteTransientError('remote endpoint remained unavailable') from last

def prepare(event_path):
 event=json.loads(event_path.read_text())
 try:result=prepare_write(event,ROOT)
 except WriteRequestError as exc:
  title=str(event.get('issue',{}).get('title',''));rid=title.split()[-1] if title.startswith('[GPT-WRITE-V2] ') else None
  _write_state({'status':'failed','issue_number':event.get('issue',{}).get('number'),'failure':safe_failure(rid,exc.code)});_output('failed');raise SystemExit(2)
 status=result['status'];receipt=result['receipt'];_write_state({'status':status,'issue_number':receipt['issue_number'],'request_id':receipt['request_id'],'receipt':receipt,'index_entry':result['index_entry']});_output(status)

def _first_commit(path,current):
 try:
  value=subprocess.run(['git','log','--format=%H','--diff-filter=A','--',path],cwd=ROOT,check=True,capture_output=True,text=True).stdout.splitlines()
  return value[-1] if value else current
 except (subprocess.SubprocessError,OSError):return current

def _verify_one(item,commit_sha):
 proof=verify_github_prediction(item['index_entry'],REPOSITORY,'main',commit_sha,fetch_with_retry)
 receipt=item['receipt']
 if receipt['write_request_status']=='pending_remote_verification':
  path=ROOT/'docs/write-requests/receipts'/f"{receipt['request_id']}.json";live=json.loads(path.read_text());live['first_written_commit_sha']=_first_commit(live['prediction_path'],commit_sha);path.write_text(json.dumps(live,indent=2)+'\n')
  receipt=finalize_receipt(ROOT,receipt['request_id'],commit_sha,proof)
 return proof,receipt

def remote_verify(commit_sha):
 state=json.loads(STATE.read_text())
 try:proof,receipt=_verify_one(state,commit_sha)
 except RemoteTransientError:
  if state['status'] in {'prepared_new','resume_pending'}:mark_verification_attempt(ROOT,state['request_id'])
  state.update({'status':'pending_remote_verification','receipt':json.loads((ROOT/'docs/write-requests/receipts'/f"{state['request_id']}.json").read_text()),'retryable':True});_write_state(state);raise SystemExit(3)
 except Exception:
  if state['status'] in {'prepared_new','resume_pending'}:receipt=mark_verification_attempt(ROOT,state['request_id'],terminal_code='REMOTE_INTEGRITY_MISMATCH')
  else:receipt=state['receipt']
  state.update({'status':'failed_terminal','receipt':receipt,'failure':safe_failure(state.get('request_id'),'REMOTE_INTEGRITY_MISMATCH')});_write_state(state);raise SystemExit(4)
 state.update({'status':'finalized' if state['status']!='verified_replay' else 'verified_replay','proof':proof,'receipt':receipt});_write_state(state)

def verify_receipt(commit_sha):
 state=json.loads(STATE.read_text());receipt=state['receipt'];path=f"docs/write-requests/receipts/{receipt['request_id']}.json";base=f'https://raw.githubusercontent.com/{REPOSITORY}'
 try:commit_bytes=fetch_with_retry(f'{base}/{commit_sha}/{path}');branch_bytes=fetch_with_retry(f'{base}/main/{path}')
 except RemoteTransientError:
  state.update({'status':'pending_remote_verification','retryable':True});_write_state(state);raise SystemExit(3)
 remote=json.loads(commit_bytes)
 if commit_bytes!=branch_bytes or remote.get('write_request_status')!='integrity_verified' or remote.get('final_receipt_sha256')!=receipt_hash(remote):
  state.update({'status':'failed_terminal','failure':safe_failure(receipt['request_id'],'RECEIPT_REMOTE_MISMATCH')});_write_state(state);raise SystemExit(4)
 state.update({'status':'verified','receipt':remote});_write_state(state)

def recover(commit_sha):
 ledger=load_ledger(ROOT/'docs/write-requests/ledger.json');states=[]
 for ledger_entry in ledger['requests']:
  receipt_path=ROOT/ledger_entry['receipt_path']
  if not receipt_path.is_file():raise WriteRequestError('RECEIPT_LEDGER_MISMATCH')
  original=json.loads(receipt_path.read_text())
  if original.get('write_request_status')!='pending_remote_verification':continue
  try:item=audit_ledger_write(ROOT,ledger_entry)
  except WriteRequestError as exc:
   receipt=mark_verification_attempt(ROOT,original['request_id'],terminal_code=exc.code);states.append({'status':'failed_terminal','issue_number':receipt['issue_number'],'request_id':receipt['request_id'],'receipt':receipt,'failure':safe_failure(receipt['request_id'],exc.code)});continue
  try:proof,receipt=_verify_one(item,commit_sha);states.append({'status':'finalized','issue_number':receipt['issue_number'],'request_id':receipt['request_id'],'receipt':receipt,'proof':proof})
  except RemoteTransientError:
   receipt=mark_verification_attempt(ROOT,item['receipt']['request_id']);states.append({'status':'pending_remote_verification','issue_number':receipt['issue_number'],'request_id':receipt['request_id'],'receipt':receipt,'retryable':True})
  except Exception:
   receipt=mark_verification_attempt(ROOT,item['receipt']['request_id'],terminal_code='REMOTE_INTEGRITY_MISMATCH');states.append({'status':'failed_terminal','issue_number':receipt['issue_number'],'request_id':receipt['request_id'],'receipt':receipt,'failure':safe_failure(receipt['request_id'],'REMOTE_INTEGRITY_MISMATCH')})
 # Already-final receipts are included so a prior final-receipt fetch failure can finish notification.
 for entry in ledger['requests']:
  receipt=json.loads((ROOT/entry['receipt_path']).read_text())
  if receipt.get('write_request_status')=='integrity_verified':states.append({'status':'verified_replay','issue_number':receipt['issue_number'],'request_id':receipt['request_id'],'receipt':receipt})
 _write_state({'status':'recovery','items':states});_output('recovery')

def verify_recovery(commit_sha):
 state=json.loads(STATE.read_text())
 for item in state['items']:
  if item['status'] in {'finalized','verified_replay'}:
   if item['status']=='verified_replay':
    index=json.loads((ROOT/'docs/predictions/index-v2.json').read_text());item['index_entry']=next(x for x in index['predictions'] if x['prediction_run_id']==item['receipt']['prediction_run_id']);_verify_one(item,commit_sha)
   previous=STATE;_write_state(item)
   try:verify_receipt(commit_sha);verified=json.loads(STATE.read_text());item.update(verified)
   finally:_write_state(state)
 _write_state(state)

def _github(method,path,payload=None):
 token=os.environ.get('GITHUB_TOKEN')
 if not token:raise RuntimeError('workflow token unavailable')
 data=json.dumps(payload).encode() if payload is not None else None;req=urllib.request.Request(f'https://api.github.com{path}',data=data,method=method,headers={'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'Daily-US-Stock-Screen-writer/2.0'})
 with urllib.request.urlopen(req,timeout=30) as response:return json.loads(response.read() or b'{}')
def _report_item(item):
 number=item.get('issue_number')
 if not isinstance(number,int):return
 issue=_github('GET',f'/repos/{REPOSITORY}/issues/{number}')
 if issue.get('state')=='closed':return
 status=item.get('status')
 if os.environ.get('WORKFLOW_JOB_STATUS')=='failure' and status=='finalized':status='pending_remote_verification'
 if status in {'verified','verified_replay'}:
  r=item['receipt'];protocol={k:r.get(k) for k in ('write_request_status','request_id','prediction_run_id','prediction_path','prediction_sha256','index_path','verified_commit_sha','completed_at')};body='Prediction persistence was remotely integrity-verified.\n\n```json\n'+json.dumps(protocol,indent=2)+'\n```';close=True
 elif status=='pending_remote_verification':
  protocol={'write_request_status':'pending_remote_verification','request_id':item.get('request_id'),'retryable':True};body='Repository bytes are written; remote integrity verification is pending and automatic recovery will retry.\n\n```json\n'+json.dumps(protocol,indent=2)+'\n```';close=False
 elif status=='prepared_new':
  protocol={'write_request_status':'not_written','request_id':item.get('request_id'),'retryable':False};body='The request did not reach the repository-written state.\n\n```json\n'+json.dumps(protocol,indent=2)+'\n```';close=True
 else:
  protocol=item.get('failure') or safe_failure(item.get('request_id'),'WORKFLOW_FAILURE');body='Prediction persistence reached a terminal integrity failure.\n\n```json\n'+json.dumps(protocol,indent=2)+'\n```';close=True
 _github('POST',f'/repos/{REPOSITORY}/issues/{number}/comments',{'body':body})
 if close:_github('PATCH',f'/repos/{REPOSITORY}/issues/{number}',{'state':'closed'})
def report():
 state=json.loads(STATE.read_text()) if STATE.exists() else {}
 for item in state.get('items',[state]):_report_item(item)

def main():
 p=argparse.ArgumentParser();subs=p.add_subparsers(dest='command',required=True);q=subs.add_parser('prepare');q.add_argument('--event',type=Path,required=True)
 for name in ('remote-verify','verify-receipt','recover','verify-recovery'):q=subs.add_parser(name);q.add_argument('--commit',required=True)
 subs.add_parser('report');args=p.parse_args()
 {'prepare':lambda:prepare(args.event),'remote-verify':lambda:remote_verify(args.commit),'verify-receipt':lambda:verify_receipt(args.commit),'recover':lambda:recover(args.commit),'verify-recovery':lambda:verify_recovery(args.commit),'report':report}[args.command]()
if __name__=='__main__':main()
