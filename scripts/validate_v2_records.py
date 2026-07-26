#!/usr/bin/env python3
"""Draft-2020-12 schema and cross-record integrity validation for schema 2.0."""
from __future__ import annotations
import argparse,hashlib,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from jsonschema import Draft202012Validator,FormatChecker
from src.integrity import (IntegrityError,display_action_class,make_prediction_id,neutral_threshold,validate_confidence,validate_information_timeline,validate_valuation,verify_manifest,verify_snapshot)
SCHEMAS=ROOT/'schemas/v2.0'
class ValidationError(IntegrityError):pass

def _load(path:Path):
    try:return json.loads(path.read_text(encoding='utf-8'),parse_constant=lambda x:(_ for _ in ()).throw(ValueError(f'non-finite {x}')))
    except (OSError,json.JSONDecodeError,ValueError) as exc:raise ValidationError(f'{path}: invalid strict JSON: {exc}') from exc
def _schema(name:str):return _load(SCHEMAS/name)
def _reject_nonfinite(value):
    if isinstance(value,float) and not math.isfinite(value):raise ValidationError('NaN/Infinity is forbidden')
    if isinstance(value,dict):
        for item in value.values():_reject_nonfinite(item)
    elif isinstance(value,list):
        for item in value:_reject_nonfinite(item)
def _validate_schema(instance,name,location):
    _reject_nonfinite(instance)
    errors=sorted(Draft202012Validator(_schema(name),format_checker=FormatChecker()).iter_errors(instance),key=lambda e:list(e.path))
    if errors:raise ValidationError(f"{location}: schema violation at {list(errors[0].path)}: {errors[0].message}")
def validate_manifest(path:Path,root:Path=ROOT):
    data=_load(path);_validate_schema(data,'manifest.schema.json',path);manifest,snapshot=verify_manifest(path);validate_snapshot(path.parent/data['snapshot_path']);return manifest,snapshot
def validate_snapshot(path:Path):
    data=_load(path);_validate_schema(data,'snapshot.schema.json',path);verified=verify_snapshot(path)
    for key,schema in (("phase2_artifact","phase2_artifact.schema.json"),("phase3_artifact","phase3_artifact.schema.json")):
        artifact=_load(path.parent/data["files"][key]["path"]);_validate_schema(artifact,schema,path.parent/data["files"][key]["path"])
    return verified
def validate_prediction_data(data:dict,root:Path=ROOT):
    _validate_schema(data,'prediction_bundle.schema.json','prediction bundle')
    snapshot_path=(root/data['source_snapshot_path']).resolve()
    if root.resolve() not in snapshot_path.parents:raise ValidationError('source snapshot escapes repository')
    snapshot=validate_snapshot(snapshot_path)
    if (data['source_snapshot_id'],data['source_generation_id'])!=(snapshot['snapshot_id'],snapshot['generation_id']):raise ValidationError('source snapshot identity mismatch')
    entry_relative=Path(data['entry_resolution_path'])
    if entry_relative.is_absolute() or '..' in entry_relative.parts:raise ValidationError('entry resolution path traversal')
    entry_path=(root/entry_relative).resolve()
    if root.resolve() not in entry_path.parents or not entry_path.is_file() or hashlib.sha256(entry_path.read_bytes()).hexdigest()!=data['entry_resolution_sha256']:raise ValidationError('entry resolution hash/path mismatch')
    entry=_load(entry_path);_validate_schema(entry,'entry_resolution.schema.json',entry_path)
    if entry['entry_resolution_id']!=data['entry_resolution_id'] or entry['source_generation_id']!=data['source_generation_id']:raise ValidationError('entry resolution identity mismatch')
    assessments={};entity_classes={}
    groups={'forecast':'forecast','comparison_only':'comparison_only','monitor_only':'monitor_only'}
    for row in data['candidate_assessments']:
        rid=row['route_candidate_id']
        if rid in assessments:raise ValidationError('duplicate route_candidate_id')
        if groups[row['record_group']]!=row['prediction_applicability']:raise ValidationError('record_group and prediction_applicability mismatch')
        if row['display_action_class']!=display_action_class(row):raise ValidationError('display action class mismatch')
        validate_valuation(row['valuation_status'],row['estimated_fundamental_value_change_low'],row['estimated_fundamental_value_change_base'],row['estimated_fundamental_value_change_high'])
        if row['record_group']=='comparison_only' and not row.get('comparison_for'):raise ValidationError('comparison_for is required')
        assessments[rid]=row;entity_classes.setdefault(row['entity_id'],set()).add(row['display_action_class'])
    if any(len(v)>1 for v in entity_classes.values()):raise ValidationError('entity has conflicting primary display classes')
    ids=set()
    for row in data['horizon_forecasts']:
        assessment=assessments.get(row['route_candidate_id'])
        if not assessment or assessment['record_group']!='forecast' or assessment['entity_id']!=row['entity_id']:raise ValidationError('assessment and forecast reference mismatch')
        validate_information_timeline(data['information_cutoff_at'],row['first_tradable_at'],row['entry_price_timestamp']);validate_confidence(row['forecast_confidence'],row['forecast_probability'])
        if row['absolute_neutral_threshold']!=neutral_threshold(row['horizon'],'absolute') or row['relative_neutral_threshold']!=neutral_threshold(row['horizon'],'relative'):raise ValidationError('neutral threshold mismatch')
        expected=make_prediction_id(row['route_candidate_id'],row['horizon'],data['prediction_run_id'],data['prompt_version'],row['entry_price_timestamp'])
        if row['prediction_id']!=expected or expected in ids:raise ValidationError('prediction ID invalid or duplicate')
        ids.add(expected)
    finals={r for r,a in assessments.items() if a['record_group']=='forecast'};matched={a.get('comparison_for') for a in assessments.values() if a['record_group']=='comparison_only'}
    if finals-matched:raise ValidationError('final candidate lacks comparison_for record')
    return data

def validate_prediction(path:Path,root:Path=ROOT):return validate_prediction_data(_load(path),root)
def validate_verification_data(data:dict,root:Path=ROOT):
    _validate_schema(data,'verification_bundle.schema.json','verification bundle')
    relative=Path(data['source_prediction_file'])
    if relative.is_absolute() or '..' in relative.parts:raise ValidationError('source prediction path traversal')
    source=(root/relative).resolve()
    if root.resolve() not in source.parents or not source.is_file():raise ValidationError('source prediction is unavailable')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=data['source_prediction_sha256']:raise ValidationError('source prediction hash mismatch')
    prediction=validate_prediction(source,root);forecasts={r['prediction_id']:r for r in prediction['horizon_forecasts']};assessments={r['route_candidate_id']:r for r in prediction['candidate_assessments']}
    seen=set()
    for row in data['records']:
        group=row['evaluation_group'];assessment=assessments.get(row['route_candidate_id'])
        if not assessment:raise ValidationError('verification assessment reference mismatch')
        if group=='directional_forecast':
            forecast=forecasts.get(row['prediction_id'])
            if not forecast or forecast['route_candidate_id']!=row['route_candidate_id'] or forecast['horizon']!=row['verification_horizon'] or assessment['record_group']!='forecast':raise ValidationError('verification forecast/horizon mismatch')
        elif group=='selection_comparison' and (row['prediction_id'] is not None or assessment['record_group']!='comparison_only'):raise ValidationError('comparison verification group mismatch')
        elif group=='monitor_resolution' and (row['prediction_id'] is not None or assessment['record_group']!='monitor_only'):raise ValidationError('monitor verification group mismatch')
        if group=='monitor_resolution' and any(key not in row for key in ('cause_resolved','cause_type','days_to_resolution','continued','reversed','data_artifact')):raise ValidationError('monitor resolution fields are required')
        if row['record_id'] in seen:raise ValidationError('duplicate verification record_id')
        seen.add(row['record_id'])
    return data
def validate_verification(path:Path,root:Path=ROOT):return validate_verification_data(_load(path),root)
def validate_index(root:Path=ROOT):
    path=root/'docs/predictions/index-v2.json'
    if not path.exists():return 0
    data=_load(path)
    if set(data)!={'index_schema_version','predictions'} or data['index_schema_version']!='2.0' or not isinstance(data['predictions'],list):raise ValidationError('invalid schema 2.0 index')
    seen=set()
    for entry in data['predictions']:
        required={'prediction_run_id','path','sha256','schema_version','source_snapshot_id','record_count','created_at'}
        if set(entry)!=required or entry['prediction_run_id'] in seen:raise ValidationError('invalid or duplicate index entry')
        record_path=(root/entry['path']).resolve()
        if root.resolve() not in record_path.parents or not record_path.is_file():raise ValidationError('index path unavailable')
        bundle=validate_prediction(record_path,root)
        if hashlib.sha256(record_path.read_bytes()).hexdigest()!=entry['sha256'] or entry['schema_version']!='2.0' or bundle['source_snapshot_id']!=entry['source_snapshot_id'] or len(bundle['candidate_assessments'])+len(bundle['horizon_forecasts'])!=entry['record_count'] or bundle['generated_at']!=entry['created_at']:raise ValidationError('index metadata/hash mismatch')
        seen.add(entry['prediction_run_id'])
    return len(seen)
def validate_entry_resolutions(root:Path=ROOT):
    path=root/'docs/entry-resolutions/index.json'
    if not path.exists():return 0
    data=_load(path);count=0
    for generation,entry in data.get('generations',{}).items():
        artifact_path=root/entry['path'];artifact=_load(artifact_path);_validate_schema(artifact,'entry_resolution.schema.json',artifact_path)
        if artifact['source_generation_id']!=generation or artifact['entry_resolution_id']!=entry['entry_resolution_id'] or hashlib.sha256(artifact_path.read_bytes()).hexdigest()!=entry['sha256'] or len(artifact['records'])!=entry['record_count']:raise ValidationError('entry resolution index mismatch')
        count+=1
    return count
def validate_verification_index(root:Path=ROOT):
    path=root/'docs/verifications/index-v2.json'
    if not path.exists():return 0
    data=_load(path);seen=set()
    for entry in data.get('verifications',[]):
        artifact=root/entry['path'];bundle=validate_verification(artifact,root)
        if entry['verification_run_id'] in seen or hashlib.sha256(artifact.read_bytes()).hexdigest()!=entry['sha256'] or bundle['source_prediction_sha256']!=entry['source_prediction_sha256'] or len(bundle['records'])!=entry['record_count'] or bundle['generated_at']!=entry['created_at']:raise ValidationError('verification index mismatch')
        seen.add(entry['verification_run_id'])
    return len(seen)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('paths',nargs='*');args=parser.parse_args();count=0
    try:
        manifest=ROOT/'docs/manifest.json'
        if manifest.exists():validate_manifest(manifest);count+=1
        paths=[Path(p) for p in args.paths] if args.paths else list((ROOT/'docs/predictions/v2').glob('*.json'))+list((ROOT/'docs/verifications/v2').glob('*.json'))
        for p in paths:
            (validate_verification if 'verifications' in p.parts else validate_prediction)(p);count+=1
        count+=validate_index(ROOT)+validate_entry_resolutions(ROOT)+validate_verification_index(ROOT)
    except (ValidationError,IntegrityError,KeyError,ValueError) as exc:print(f'schema 2.0 validation failed: {exc}',file=sys.stderr);raise SystemExit(1)
    print(f'schema 2.0 validation succeeded: {count} manifest/record artifacts')
if __name__=='__main__':main()
