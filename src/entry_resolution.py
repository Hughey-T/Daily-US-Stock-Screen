"""Immutable automatic next-session-open resolution for pinned generations."""
from __future__ import annotations
import hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Callable
import pandas as pd
from src.integrity import digest,resolve_next_session_open,verify_manifest

def _write_new(path:Path,payload:dict)->None:
 path.parent.mkdir(parents=True,exist_ok=True);data=(json.dumps(payload,indent=2,allow_nan=False)+"\n").encode()
 try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
 except FileExistsError:
  if path.read_bytes()!=data:raise ValueError("immutable entry resolution conflict")
  return
 with os.fdopen(fd,'wb') as handle:handle.write(data)

def resolve_generation_entries(manifest_path:Path,root:Path,downloader:Callable[[str],pd.DataFrame],observed_at:str|None=None)->tuple[Path,dict[str,Any]]:
 manifest,snapshot=verify_manifest(manifest_path);snapshot_path=manifest_path.parent/manifest['snapshot_path'];phase2=json.loads((snapshot_path.parent/snapshot['files']['phase2_artifact']['path']).read_text())
 observed_at=observed_at or datetime.now(timezone.utc).isoformat();rows=[]
 candidates=[r for r in phase2['processed_rows'] if r['treatment'] in {'web_research','comparison_only'}]
 for row in sorted(candidates,key=lambda item:item['route_candidate_id']):
  try:frame=downloader(row['ticker'])
  except Exception:frame=pd.DataFrame()
  resolved=resolve_next_session_open(frame,row['first_tradable_at'],observed_at)
  rows.append({'entity_id':row['entity_id'],'route_candidate_id':row['route_candidate_id'],'ticker':row['ticker'],**resolved,'timezone':'America/New_York','resolved_at':observed_at})
 canonical=json.dumps(rows,sort_keys=True,separators=(',',':'));entry_id='entry_'+digest(snapshot['generation_id'],canonical)
 payload={'entry_resolution_schema_version':'2.0','entry_resolution_id':entry_id,'source_generation_id':snapshot['generation_id'],'source_snapshot_id':snapshot['snapshot_id'],'source_snapshot_path':snapshot_path.relative_to(root).as_posix(),'generated_at':observed_at,'records':rows}
 index_path=root/'docs/entry-resolutions/index.json';index={'index_schema_version':'2.0','generations':{}}
 if index_path.exists():index=json.loads(index_path.read_text())
 existing=index['generations'].get(snapshot['generation_id'])
 if existing:
  existing_path=root/existing['path'];existing_payload=json.loads(existing_path.read_text())
  if all(item['status']=='resolved' for item in existing_payload['records']) and not all(item['status']=='resolved' for item in rows):return existing_path,existing_payload
 path=root/'docs/entry-resolutions'/snapshot['generation_id']/f'{entry_id}.json';_write_new(path,payload)
 index['generations'][snapshot['generation_id']]={'path':path.relative_to(root).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'entry_resolution_id':entry_id,'source_snapshot_id':snapshot['snapshot_id'],'record_count':len(rows),'created_at':observed_at}
 index_path.parent.mkdir(parents=True,exist_ok=True);index_path.write_text(json.dumps(index,indent=2)+'\n')
 return path,payload
