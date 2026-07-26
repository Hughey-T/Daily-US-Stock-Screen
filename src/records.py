"""Schema 2.0 research-driven producers and append-only local persistence."""
from __future__ import annotations
import hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from src.integrity import ACTION_CLASS_MAPPING_VERSION,display_action_class,make_prediction_id,neutral_threshold

def _sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def _write_new(path:Path,payload:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True);data=(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode()
    try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
    except FileExistsError:
        if path.read_bytes()!=data:raise ValueError(f"immutable record already exists with different content: {path}")
        return
    with os.fdopen(fd,"wb") as handle:handle.write(data)

def _assessment(row:dict[str,Any],research:dict[str,Any])->dict[str,Any]:
    required={"research_status","investment_stance","situation_type","research_priority","valuation_status","primary_thesis","invalidation_condition","research_completeness","thesis_confidence","verified_facts","company_claims","inferences"}
    missing=sorted(required-set(research))
    if missing:raise ValueError(f"structured Phase 4/5 research missing fields for {row['ticker']}: {missing}")
    value=research.get("estimated_fundamental_value_change",{})
    record={"entity_id":row["entity_id"],"route_candidate_id":row["route_candidate_id"],"ticker":row["ticker"],"source_dataset":row["source_dataset"],"record_group":"forecast" if research.get("forecast_applicable",False) else "monitor_only","prediction_applicability":"forecast" if research.get("forecast_applicable",False) else "monitor_only","research_status":research["research_status"],"investment_stance":research["investment_stance"],"situation_type":research["situation_type"],"research_priority":research["research_priority"],"valuation_status":research["valuation_status"],"estimated_fundamental_value_change_low":value.get("low"),"estimated_fundamental_value_change_base":value.get("base"),"estimated_fundamental_value_change_high":value.get("high"),"display_action_class":"","action_class_mapping_version":ACTION_CLASS_MAPPING_VERSION,"primary_thesis":research["primary_thesis"],"invalidation_condition":research["invalidation_condition"],"research_completeness":research["research_completeness"],"thesis_confidence":research["thesis_confidence"],"verified_facts":research["verified_facts"],"company_claims":research["company_claims"],"inferences":research["inferences"],"estimation_method":research.get("estimation_method"),"estimation_limitations":research.get("estimation_limitations",[])}
    record["display_action_class"]=display_action_class(record);return record

def build_prediction_bundle(phase3:dict[str,Any],snapshot:dict[str,Any],research_results:dict[str,dict[str,Any]],entry_resolution:dict[str,Any],entry_resolution_path:str,entry_resolution_sha256:str,run_id:str,prompt_version:str,prompt_hash:str,model_name:str,code_version:str)->dict[str,Any]:
    if entry_resolution.get("source_generation_id")!=snapshot["generation_id"]:raise ValueError("entry resolution generation mismatch")
    entries={r["route_candidate_id"]:r for r in entry_resolution.get("records",[])};assessments=[];forecasts=[]
    for row in phase3["final_research_set"]:
        research=research_results.get(row["entity_id"])
        if research is None:raise ValueError(f"structured Phase 4/5 research is required for {row['ticker']}")
        assessment=_assessment(row,research);assessments.append(assessment);entry=entries.get(row["route_candidate_id"])
        forecast_specs=research.get("forecasts",[])
        if assessment["record_group"]=="forecast" and not forecast_specs:raise ValueError(f"forecast-applicable research has no forecasts: {row['ticker']}")
        if forecast_specs and (not entry or entry.get("status")!="resolved"):raise ValueError(f"resolved immutable entry is required for forecast: {row['ticker']}")
        for spec in forecast_specs:
            horizon=int(spec["horizon"]);timestamp=entry["entry_price_timestamp"]
            forecasts.append({"prediction_id":make_prediction_id(row["route_candidate_id"],horizon,run_id,prompt_version,timestamp),"entity_id":row["entity_id"],"route_candidate_id":row["route_candidate_id"],"horizon":horizon,"absolute_direction":spec["absolute_direction"],"sector_relative_direction":spec["sector_relative_direction"],"industry_relative_direction":spec.get("industry_relative_direction","unavailable"),"absolute_neutral_threshold":neutral_threshold(horizon,"absolute"),"relative_neutral_threshold":neutral_threshold(horizon,"relative"),"forecast_confidence":spec["confidence"],"forecast_probability":float(spec["probability"]),"first_tradable_at":entry["first_tradable_at"],"entry_price_timestamp":timestamp,"entry_price_type":"next_session_open","entry_price":float(entry["entry_price"]),"timezone":"America/New_York","benchmark_type":spec.get("benchmark_type","sector_etf" if row.get("sector_etf") else "SPY"),"benchmark_symbol":spec.get("benchmark_symbol",row.get("sector_etf") or "SPY"),"benchmark_quality":spec.get("benchmark_quality","low"),"prediction_applicability":"forecast"})
    for row in phase3["comparison_records"]:
        assessments.append({"entity_id":row["entity_id"],"route_candidate_id":row["route_candidate_id"],"ticker":row["ticker"],"source_dataset":row["source_dataset"],"record_group":"comparison_only","prediction_applicability":"comparison_only","research_status":"excluded","investment_stance":"not_assessed","situation_type":"unknown","research_priority":"none","valuation_status":"not_started","estimated_fundamental_value_change_low":None,"estimated_fundamental_value_change_base":None,"estimated_fundamental_value_change_high":None,"display_action_class":"E","action_class_mapping_version":ACTION_CLASS_MAPPING_VERSION,"primary_thesis":"Comparison control; no investment thesis assessed.","invalidation_condition":"Not applicable to comparison control.","research_completeness":"incomplete","thesis_confidence":"low","verified_facts":[],"company_claims":[],"inferences":[],"estimation_method":None,"estimation_limitations":["comparison_only"],"comparison_for":row["comparison_for"]})
    return {"prediction_schema_version":"2.0","prediction_run_id":run_id,"source_snapshot_id":snapshot["snapshot_id"],"source_generation_id":snapshot["generation_id"],"source_snapshot_path":snapshot["_path"],"entry_resolution_path":entry_resolution_path,"entry_resolution_sha256":entry_resolution_sha256,"entry_resolution_id":entry_resolution["entry_resolution_id"],"prompt_version":prompt_version,"prompt_hash":prompt_hash,"config_version":snapshot["config_version"],"config_hash":snapshot["config_hash"],"model_name":model_name,"model_configuration":{},"code_version":code_version,"generated_at":datetime.now(timezone.utc).isoformat(),"information_cutoff_at":snapshot["market_data_cutoff_at"],"candidate_assessments":assessments,"horizon_forecasts":forecasts}

def persist_prediction_bundle(bundle:dict[str,Any],root:Path,validator)->tuple[Path,dict]:
    run=bundle["prediction_run_id"];receipt_path=root/"docs/predictions/receipts"/f"{run}.json";states=[]
    def receipt(state:str,**extra):
        states.append(state);receipt_path.parent.mkdir(parents=True,exist_ok=True);receipt_path.write_text(json.dumps({"prediction_run_id":run,"state":state,"state_history":states,**extra},indent=2)+"\n")
    receipt("generated");validator(bundle,root);receipt("schema_validated")
    path=root/"docs/predictions/v2"/f"{run}.json";_write_new(path,bundle);receipt("repository_written",path=path.relative_to(root).as_posix())
    index_path=root/"docs/predictions/index-v2.json";index={"index_schema_version":"2.0","predictions":[]}
    if index_path.exists():index=json.loads(index_path.read_text())
    entry={"prediction_run_id":run,"path":path.relative_to(root).as_posix(),"sha256":_sha(path),"schema_version":"2.0","source_snapshot_id":bundle["source_snapshot_id"],"record_count":len(bundle["candidate_assessments"])+len(bundle["horizon_forecasts"]),"created_at":bundle["generated_at"]}
    old=[item for item in index["predictions"] if item["prediction_run_id"]==run]
    if old and old[0]!=entry:raise ValueError("index entry conflicts with immutable prediction")
    if not old:index["predictions"].append(entry);index["predictions"].sort(key=lambda item:item["prediction_run_id"]);index_path.write_text(json.dumps(index,indent=2)+"\n")
    reread=json.loads(index_path.read_text());match=next((item for item in reread["predictions"] if item["prediction_run_id"]==run),None)
    if match!=entry or _sha(root/entry["path"])!=entry["sha256"]:raise ValueError("local prediction index integrity failure")
    receipt("indexed_local",index_entry=entry);return path,json.loads(receipt_path.read_text())

def build_verification_bundle(prediction_path:Path,records:list[dict[str,Any]],root:Path,source_data_cutoff_at:str,source_data_hash:str)->dict[str,Any]:
    prediction=json.loads(prediction_path.read_text());return {"verification_schema_version":"2.0","verification_run_id":"verify_"+prediction["prediction_run_id"]+"_"+source_data_hash[:16],"source_prediction_file":prediction_path.relative_to(root).as_posix(),"source_prediction_sha256":_sha(prediction_path),"source_data_cutoff_at":source_data_cutoff_at,"source_data_sha256":source_data_hash,"generated_at":source_data_cutoff_at,"records":records}
def persist_verification_bundle(bundle:dict[str,Any],root:Path,validator)->Path:
    validator(bundle,root);path=root/"docs/verifications/v2"/f"{bundle['verification_run_id']}.json";_write_new(path,bundle)
    index_path=root/"docs/verifications/index-v2.json";index={"index_schema_version":"2.0","verifications":[]}
    if index_path.exists():index=json.loads(index_path.read_text())
    entry={"verification_run_id":bundle["verification_run_id"],"path":path.relative_to(root).as_posix(),"sha256":_sha(path),"source_prediction_sha256":bundle["source_prediction_sha256"],"record_count":len(bundle["records"]),"created_at":bundle["generated_at"]}
    old=[item for item in index["verifications"] if item["verification_run_id"]==bundle["verification_run_id"]]
    if old and old[0]!=entry:raise ValueError("verification index conflict")
    if not old:index["verifications"].append(entry);index_path.write_text(json.dumps(index,indent=2)+"\n")
    reread=json.loads(index_path.read_text());match=next((item for item in reread["verifications"] if item["verification_run_id"]==bundle["verification_run_id"]),None)
    if match!=entry or _sha(root/entry["path"])!=entry["sha256"]:raise ValueError("local verification index integrity failure")
    return path
