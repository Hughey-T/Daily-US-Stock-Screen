"""Mechanical GitHub persistence proof; never trusts caller booleans."""
from __future__ import annotations
import hashlib,json
from typing import Callable,Any

def verify_github_prediction(entry:dict[str,Any],repository:str,branch:str,commit_sha:str,fetch:Callable[[str],bytes])->dict[str,Any]:
 if not repository or not branch or not commit_sha:raise ValueError('repository, branch, and commit SHA are required')
 base='https://raw.githubusercontent.com';commit_url=f'{base}/{repository}/{commit_sha}/{entry["path"]}';branch_url=f'{base}/{repository}/{branch}/{entry["path"]}';index_url=f'{base}/{repository}/{commit_sha}/docs/predictions/index-v2.json'
 commit_bytes=fetch(commit_url);branch_bytes=fetch(branch_url);index=json.loads(fetch(index_url))
 if commit_bytes!=branch_bytes:raise ValueError('pushed branch does not expose the verified commit bytes')
 actual=hashlib.sha256(commit_bytes).hexdigest()
 if actual!=entry['sha256']:raise ValueError('GitHub prediction hash mismatch')
 remote=next((item for item in index['predictions'] if item['prediction_run_id']==entry['prediction_run_id']),None)
 if remote!=entry:raise ValueError('GitHub prediction index entry mismatch')
 bundle=json.loads(commit_bytes)
 checks=(bundle['prediction_schema_version']==entry['schema_version'],bundle['source_snapshot_id']==entry['source_snapshot_id'],len(bundle['candidate_assessments'])+len(bundle['horizon_forecasts'])==entry['record_count'],bundle['generated_at']==entry['created_at'])
 if not all(checks):raise ValueError('GitHub prediction metadata mismatch')
 return {'state':'integrity_verified','repository':repository,'branch':branch,'commit_sha':commit_sha,'file_url':commit_url,'branch_url':branch_url,'sha256':actual,'index_url':index_url,'schema_version':entry['schema_version'],'source_snapshot_id':entry['source_snapshot_id'],'record_count':entry['record_count'],'created_at':entry['created_at']}
