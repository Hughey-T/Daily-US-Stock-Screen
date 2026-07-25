"""Schema 2.0 prediction/verification producers and append-only persistence."""
from __future__ import annotations
import hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from src.integrity import ACTION_CLASS_MAPPING_VERSION,display_action_class,make_prediction_id,neutral_threshold,verify_snapshot

def _sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def _write_new(path:Path,payload:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode()
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
    except FileExistsError:
        if path.read_bytes()!=data: raise ValueError(f"immutable record already exists with different content: {path}")
        return
    with os.fdopen(fd,"wb") as h:h.write(data)

def build_prediction_bundle(phase3:dict[str,Any],snapshot:dict[str,Any],run_id:str,prompt_version:str,prompt_hash:str,model_name:str,code_version:str,entry_prices:dict[str,dict[str,Any]],horizons=(21,63,126,252))->dict[str,Any]:
    assessments=[]; forecasts=[]
    rows=phase3["final_research_set"]+phase3["comparison_records"]+[r for r in phase3.get("processed_rows",[]) if r["treatment"] in {"data_artifact","unprocessable"}]
    seen=set()
    for row in rows:
        if row["route_candidate_id"] in seen:continue
        seen.add(row["route_candidate_id"])
        if row["treatment"]=="web_research": status,stance,priority,group,app="ready_for_deep_dive","neutral_candidate","medium","forecast","forecast"
        elif row.get("record_group")=="comparison_only" or row["treatment"]=="comparison_only": status,stance,priority,group,app="excluded","not_assessed","none","comparison_only","comparison_only"
        else: status,stance,priority,group,app="data_artifact","not_assessed","none","monitor_only","monitor_only"
        record={"entity_id":row["entity_id"],"route_candidate_id":row["route_candidate_id"],"ticker":row["ticker"],"source_dataset":row["source_dataset"],"record_group":group,"prediction_applicability":app,"research_status":status,"investment_stance":stance,"situation_type":"data_anomaly" if group=="monitor_only" else "unknown","research_priority":priority,"valuation_status":"not_started","estimated_fundamental_value_change_low":None,"estimated_fundamental_value_change_base":None,"estimated_fundamental_value_change_high":None,"display_action_class":"","action_class_mapping_version":ACTION_CLASS_MAPPING_VERSION,"primary_thesis":"Machine-generated record pending primary-source research.","invalidation_condition":"Primary-source research or data integrity invalidates the hypothesis.","research_completeness":"incomplete","thesis_confidence":"low"}
        record["display_action_class"]=display_action_class(record)
        if group=="comparison_only":record["comparison_for"]=row["comparison_for"]
        assessments.append(record)
        entry=entry_prices.get(row["route_candidate_id"])
        if group=="forecast" and entry and entry.get("status")=="resolved":
            for horizon in horizons:
                timestamp=entry["entry_price_timestamp"]
                forecasts.append({"prediction_id":make_prediction_id(row["route_candidate_id"],horizon,run_id,prompt_version,timestamp),"entity_id":row["entity_id"],"route_candidate_id":row["route_candidate_id"],"horizon":horizon,"absolute_direction":"neutral","sector_relative_direction":"neutral","industry_relative_direction":"unavailable","absolute_neutral_threshold":neutral_threshold(horizon,"absolute"),"relative_neutral_threshold":neutral_threshold(horizon,"relative"),"forecast_confidence":"low","forecast_probability":0.55,"first_tradable_at":entry["first_tradable_at"],"entry_price_timestamp":timestamp,"entry_price_type":"next_session_open","entry_price":entry["entry_price"],"timezone":"America/New_York","benchmark_type":"sector_etf" if row.get("sector_etf") else "SPY","benchmark_symbol":row.get("sector_etf") or "SPY","benchmark_quality":"low","prediction_applicability":"forecast"})
    return {"prediction_schema_version":"2.0","prediction_run_id":run_id,"source_snapshot_id":snapshot["snapshot_id"],"source_generation_id":snapshot["generation_id"],"source_snapshot_path":snapshot["_path"],"prompt_version":prompt_version,"prompt_hash":prompt_hash,"config_version":snapshot["config_version"],"config_hash":snapshot["config_hash"],"model_name":model_name,"model_configuration":{},"code_version":code_version,"generated_at":datetime.now(timezone.utc).isoformat(),"information_cutoff_at":snapshot["market_data_cutoff_at"],"candidate_assessments":assessments,"horizon_forecasts":forecasts}

def persist_prediction_bundle(bundle:dict[str,Any],root:Path,validator,confirm_repository_persistence:bool=False)->tuple[Path,dict]:
    run=bundle["prediction_run_id"]; receipt_path=root/"docs/predictions/receipts"/f"{run}.json"
    states=[]
    def receipt(state:str,**extra):
        states.append(state); receipt_path.parent.mkdir(parents=True,exist_ok=True); receipt_path.write_text(json.dumps({"prediction_run_id":run,"state":state,"state_history":states,**extra},indent=2)+"\n")
    receipt("generated"); validator(bundle,root); receipt("schema_validated")
    path=root/"docs/predictions/v2"/f"{run}.json"; _write_new(path,bundle); receipt("repository_written",path=path.relative_to(root).as_posix())
    index_path=root/"docs/predictions/index-v2.json"; index={"index_schema_version":"2.0","predictions":[]}
    if index_path.exists():index=json.loads(index_path.read_text())
    entry={"prediction_run_id":run,"path":path.relative_to(root).as_posix(),"sha256":_sha(path),"schema_version":"2.0","source_snapshot_id":bundle["source_snapshot_id"],"record_count":len(bundle["candidate_assessments"])+len(bundle["horizon_forecasts"]),"created_at":bundle["generated_at"]}
    old=[e for e in index["predictions"] if e["prediction_run_id"]==run]
    if old and old[0]!=entry:raise ValueError("index entry conflicts with immutable prediction")
    if not old:index["predictions"].append(entry);index["predictions"].sort(key=lambda e:e["prediction_run_id"]);index_path.write_text(json.dumps(index,indent=2)+"\n")
    receipt("indexed",index_path=index_path.relative_to(root).as_posix())
    reread=json.loads(index_path.read_text()); match=next((e for e in reread["predictions"] if e["prediction_run_id"]==run),None)
    if match!=entry or _sha(root/entry["path"])!=entry["sha256"]:raise ValueError("prediction index integrity failure")
    if confirm_repository_persistence:
        receipt("committed",index_entry=entry);receipt("pushed",index_entry=entry);receipt("integrity_verified",index_entry=entry)
    return path,json.loads(receipt_path.read_text())

def build_verification_bundle(prediction_path:Path,records:list[dict[str,Any]],root:Path)->dict[str,Any]:
    prediction=json.loads(prediction_path.read_text()); return {"verification_schema_version":"2.0","verification_run_id":"verify_"+prediction["prediction_run_id"],"source_prediction_file":prediction_path.relative_to(root).as_posix(),"source_prediction_sha256":_sha(prediction_path),"generated_at":datetime.now(timezone.utc).isoformat(),"records":records}
def persist_verification_bundle(bundle:dict[str,Any],root:Path,validator)->Path:
    validator(bundle,root); path=root/"docs/verifications/v2"/f"{bundle['verification_run_id']}.json";_write_new(path,bundle);return path
