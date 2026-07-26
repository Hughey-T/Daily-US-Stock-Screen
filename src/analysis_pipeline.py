"""Deterministic Phase 2/3 artifact production used by the daily workflow."""
from __future__ import annotations
from typing import Any
from src.integrity import make_entity_id, make_route_candidate_id, select_comparisons

ROUTES=("event_anomaly","quiet_drift")
FINAL_TREATMENTS={"web_research","comparison_only","not_selected","data_artifact","unprocessable"}
EVENT_MIN_SIGNAL_SCORE=1.0
QUIET_GLOBAL_MIN_TAIL_DISTANCE=0.50
QUIET_COVERAGE_MIN_TAIL_DISTANCE=0.75

def _quality(row: dict[str,Any]) -> str:
    return str(row.get("data_quality_status") or "passed")

def process_all_rows(event_rows:list[dict[str,Any]], quiet_rows:list[dict[str,Any]], market_data_date:str, config_hash:str, artifact_rows:list[dict[str,Any]]|None=None, first_tradable_at:str|None=None)->list[dict[str,Any]]:
    """Process every published row and explicit quarantined ticker, without selection."""
    output=[]
    for source, rows in ((ROUTES[0],event_rows),(ROUTES[1],quiet_rows)):
        for fallback_rank,row in enumerate(rows,1):
            ticker=str(row.get("ticker") or "").strip().upper()
            entity=make_entity_id(market_data_date,ticker,config_hash) if ticker else ""
            quality=_quality(row)
            processable=bool(ticker) and quality=="passed"
            output.append({**row,"entity_id":entity,"route_candidate_id":make_route_candidate_id(entity,source) if entity else "","ticker":ticker,"source_dataset":source,"original_rank":int(row.get("rank") or fallback_rank),"market_data_date":market_data_date,"entry_price_status":"pending","entry_price":None,"first_tradable_at":first_tradable_at,"entry_price_timestamp":None,"entry_price_type":"next_session_open","timezone":"America/New_York","quantitative_processing_status":"processed" if processable else "unprocessable","treatment":"phase3_eligible" if processable else ("data_artifact" if quality=="data_artifact" else "unprocessable"),"treatment_reason":"eligible after route-local quantitative processing" if processable else str(row.get("data_quality_reason") or "missing ticker or required values"),"data_quality_status":quality})
    # Unreconciled securities remain visible as route-neutral data artifacts rather
    # than disappearing from the audit. They cannot enter either ranking.
    for row in artifact_rows or []:
        ticker=str(row["ticker"]).upper(); entity=make_entity_id(market_data_date,ticker,config_hash)
        output.append({**row,"entity_id":entity,"route_candidate_id":make_route_candidate_id(entity,"data_artifact"),"ticker":ticker,"source_dataset":"data_artifact","original_rank":None,"market_data_date":market_data_date,"entry_price_status":"not_applicable","entry_price":None,"first_tradable_at":None,"entry_price_timestamp":None,"entry_price_type":"none","timezone":"America/New_York","quantitative_processing_status":"processed","treatment":"data_artifact","treatment_reason":row.get("data_quality_reason","corporate action unreconciled"),"data_quality_status":"data_artifact"})
    return output

def _selection_key(row:dict[str,Any])->tuple:
    route=row["source_dataset"]
    # global_tail is intentionally stronger than sector coverage. Event anomaly
    # uses its route-local score, never compared numerically with quiet score.
    bucket=str(row.get("selection_bucket") or row.get("selection_reason") or "")
    bucket_priority=0 if route=="event_anomaly" else (0 if bucket=="global_tail" else 1)
    quality_value=abs(float(row.get("tail_distance") or row.get("signal_score") or 0))
    theme=str(row.get("sector") or row.get("industry") or "unknown")
    return (bucket_priority,-quality_value,int(row["original_rank"]),theme,row["ticker"],row["route_candidate_id"])

def _eligible(row:dict[str,Any])->bool:
    if row["treatment"]!="phase3_eligible":return False
    if row["source_dataset"]=="event_anomaly":return float(row.get("signal_score") or 0)>=EVENT_MIN_SIGNAL_SCORE
    distance=abs(float(row.get("tail_distance") or 0));bucket=str(row.get("selection_bucket") or "")
    return distance >= (QUIET_GLOBAL_MIN_TAIL_DISTANCE if bucket=="global_tail" else QUIET_COVERAGE_MIN_TAIL_DISTANCE)

def select_research_set(processed:list[dict[str,Any]], maximum_entities:int=15)->list[dict[str,Any]]:
    """Select at most 15 unique entities with deterministic route diversity."""
    pools={route:sorted([r for r in processed if r["source_dataset"]==route and _eligible(r)],key=_selection_key) for route in ROUTES}
    route_caps={route:(len(rows)-1 if len(rows)>1 else len(rows)) for route,rows in pools.items()}
    route_selected={route:0 for route in ROUTES}
    selected=[]; entities=set(); themes:dict[str,int]={}
    active=[r for r in ROUTES if pools[r]]
    cursor=0
    while active and len(selected)<maximum_entities:
        route=active[cursor%len(active)]; chosen=None
        if route_selected[route]>=route_caps[route]:active.remove(route);cursor=0;continue
        while pools[route]:
            candidate=pools[route].pop(0)
            if candidate["entity_id"] in entities: continue
            theme=str(candidate.get("sector") or candidate.get("industry") or "unknown")
            # Cap a theme at 4; never add weak/duplicative names merely to fill 15.
            if themes.get(theme,0)>=4: continue
            chosen=candidate; break
        if chosen:
            selected.append(chosen); entities.add(chosen["entity_id"])
            route_selected[route]+=1
            theme=str(chosen.get("sector") or chosen.get("industry") or "unknown")
            themes[theme]=themes.get(theme,0)+1
        if not pools[route]:
            active.remove(route); cursor=0
        elif active:
            cursor=(cursor+1)%len(active)
    return selected

def build_phase3(processed:list[dict[str,Any]], maximum_entities:int=15)->dict[str,Any]:
    finals=select_research_set(processed,maximum_entities)
    final_ids={r["route_candidate_id"] for r in finals}
    final_entities={r["entity_id"] for r in finals}
    pool=[r for r in processed if r["source_dataset"] in ROUTES and r["route_candidate_id"] not in final_ids and r["entity_id"] not in final_entities and r["treatment"]=="phase3_eligible"]
    comparisons,shortages=select_comparisons(finals,pool,1)
    comparison_ids={r["route_candidate_id"] for r in comparisons}
    rows=[]
    for row in processed:
        rid=row["route_candidate_id"]
        if rid in final_ids: treatment,reason="web_research","deterministic cross-route research selection"
        elif rid in comparison_ids: treatment,reason="comparison_only","deterministic same-route nearest match"
        elif row["treatment"]=="phase3_eligible": treatment,reason="not_selected","outside deterministic research/comparison set"
        else: treatment,reason=row["treatment"],row["treatment_reason"]
        rows.append({**row,"treatment":treatment,"treatment_reason":reason})
    audit={}
    for route in ROUTES:
        route_rows=[r for r in rows if r["source_dataset"]==route]
        counts={t:sum(r["treatment"]==t for r in route_rows) for t in FINAL_TREATMENTS}
        unprocessed=sum(r["quantitative_processing_status"] not in {"processed","unprocessable"} for r in route_rows)
        if sum(counts.values())!=len(route_rows) or unprocessed:
            raise ValueError(f"Phase 2 accounting failed for {route}")
        audit[route]={"total":len(route_rows),**counts,"unprocessed":unprocessed}
    final_rows=[r for r in rows if r["route_candidate_id"] in final_ids]
    return {"artifact_schema_version":"2.0","final_research_set":final_rows,"comparison_records":comparisons,"shortage_records":shortages,"processed_rows":rows,"audit":audit,"web_research_entity_count":len({r['entity_id'] for r in final_rows})}
