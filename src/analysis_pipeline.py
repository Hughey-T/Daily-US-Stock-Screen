"""Machine-readable Phase 2/3 processing with route separation."""
from __future__ import annotations
from typing import Any
from src.integrity import make_entity_id, make_route_candidate_id, select_comparisons

TREATMENTS={"web_research_candidate","comparison_candidate","not_selected","data_artifact","unprocessable"}

def process_all_rows(event_rows: list[dict[str, Any]], quiet_rows: list[dict[str, Any]], market_data_date: str, config_hash: str, web_limit: int=15) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    processed=[]
    for source, rows in (("event_anomaly",event_rows),("quiet_drift",quiet_rows)):
        for position,row in enumerate(rows,1):
            ticker=str(row.get("ticker","")).upper()
            entity=make_entity_id(market_data_date,ticker,config_hash)
            quality=row.get("data_quality_status","passed")
            if not ticker: treatment="unprocessable"
            elif quality=="data_artifact": treatment="data_artifact"
            elif position <= web_limit: treatment="web_research_candidate"
            else: treatment="comparison_candidate"
            processed.append({**row,"entity_id":entity,"route_candidate_id":make_route_candidate_id(entity,source),"ticker":ticker,"source_dataset":source,"original_rank":row.get("rank",position),"market_data_date":market_data_date,"entry_price_planned":"next_session_open","quantitative_processing_status":"processed","treatment":treatment,"treatment_reason":"deterministic route rank and data quality","data_quality_status":quality})
    audit={}
    for source in ("event_anomaly","quiet_drift"):
        rows=[r for r in processed if r["source_dataset"]==source]
        counts={name:sum(r["treatment"]==name for r in rows) for name in TREATMENTS}
        if sum(counts.values()) != len(rows) or any(r["quantitative_processing_status"]!="processed" for r in rows):
            raise ValueError(f"Phase 2 accounting failed for {source}")
        audit[source]={"total":len(rows),**counts,"unprocessed":0}
    return processed,audit

def phase3_matches(processed: list[dict[str,Any]], maximum: int=15):
    finals=[r for r in processed if r["treatment"]=="web_research_candidate"][:maximum]
    pool=[r for r in processed if r["treatment"]=="comparison_candidate"]
    return finals,*select_comparisons(finals,pool)
