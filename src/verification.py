"""Outcome calculation from immutable predictions and recorded market data."""
from __future__ import annotations
import hashlib,json
from typing import Any
import pandas as pd
from src.integrity import split_adjusted_prices,calculate_mfe_mae

def market_data_hash(frames:dict[str,pd.DataFrame])->str:
 parts=[]
 for ticker,frame in sorted(frames.items()):parts.append(ticker.encode()+b'\0'+frame.sort_index().to_csv().encode())
 return hashlib.sha256(b''.join(parts)).hexdigest()
def _returns(frame:pd.DataFrame,entry_price:float|None,entry_at:str,horizon:int,direction:str)->dict[str,Any]:
 if frame.empty or not {'Close','High','Low'}.issubset(frame.columns):return {'status':'data_unavailable'}
 work=frame.sort_index().copy();index=pd.to_datetime(work.index,utc=True);start=pd.Timestamp(entry_at)
 positions=[i for i,value in enumerate(index) if value.date()>=start.date()]
 if not positions:return {'status':'data_unavailable'}
 begin=positions[0]
 if len(work)-begin<=horizon:return {'status':'not_yet_due'}
 if entry_price is None:
  if 'Open' not in work or not pd.notna(work['Open'].iloc[begin]):return {'status':'data_unavailable'}
  entry_price=float(work['Open'].iloc[begin])
 adjusted_close=split_adjusted_prices(work);factor=float(adjusted_close.iloc[begin]/float(work['Close'].iloc[begin]));adjusted_entry=entry_price*factor;end=begin+horizon
 price_return=float(adjusted_close.iloc[end]/adjusted_entry-1)
 if 'Adj Close' in work.columns:
  adj=pd.to_numeric(work['Adj Close'],errors='coerce');entry_total=entry_price*float(adj.iloc[begin]/float(work['Close'].iloc[begin]));total_return=float(adj.iloc[end]/entry_total-1)
 else:
  dividends=float(pd.to_numeric(work.get('Dividends',0),errors='coerce').iloc[begin:end+1].fillna(0).sum()) if 'Dividends' in work else 0.0;total_return=float((adjusted_close.iloc[end]+dividends)/adjusted_entry-1)
 future_factor=adjusted_close/work['Close'];highs=(pd.to_numeric(work['High'],errors='coerce')*future_factor).iloc[begin:end+1];lows=(pd.to_numeric(work['Low'],errors='coerce')*future_factor).iloc[begin:end+1]
 mfe,mae=calculate_mfe_mae(adjusted_entry,highs,lows,'up' if direction!='down' else 'down')
 return {'status':'active','price_return':price_return,'total_shareholder_return':total_return,'mfe':mfe,'mae':mae}
def generate_verification_records(prediction:dict[str,Any],frames:dict[str,pd.DataFrame],lifecycle:dict[str,str]|None=None,entry_records:dict[str,dict[str,Any]]|None=None,monitor_outcomes:dict[str,dict[str,Any]]|None=None)->list[dict[str,Any]]:
 lifecycle=lifecycle or {};entry_records=entry_records or {};monitor_outcomes=monitor_outcomes or {};assessments={a['route_candidate_id']:a for a in prediction['candidate_assessments']};records=[]
 for forecast in prediction['horizon_forecasts']:
  assessment=assessments[forecast['route_candidate_id']];ticker=assessment['ticker'];outcome=lifecycle.get(ticker,'active');stock=_returns(frames.get(ticker,pd.DataFrame()),forecast['entry_price'],forecast['entry_price_timestamp'],forecast['horizon'],forecast['absolute_direction'])
  status=outcome if outcome!='active' else stock['status'];base={'record_id':forecast['prediction_id'],'prediction_id':forecast['prediction_id'],'route_candidate_id':forecast['route_candidate_id'],'verification_horizon':forecast['horizon'],'outcome_status':status,'price_return':None,'total_shareholder_return':None,'spy_relative_return':None,'sector_relative_return':None,'industry_relative_return':None,'mfe':None,'mae':None,'evaluation_group':'directional_forecast'}
  if stock['status']=='active':
   spy=_returns(frames.get('SPY',pd.DataFrame()),None,forecast['entry_price_timestamp'],forecast['horizon'],'up');bench=_returns(frames.get(forecast['benchmark_symbol'],pd.DataFrame()),None,forecast['entry_price_timestamp'],forecast['horizon'],'up')
   base.update({'price_return':stock['price_return'],'total_shareholder_return':stock['total_shareholder_return'],'spy_relative_return':stock['price_return']-spy['price_return'] if spy.get('status')=='active' else None,'sector_relative_return':stock['price_return']-bench['price_return'] if forecast['benchmark_type']=='sector_etf' and bench.get('status')=='active' else None,'industry_relative_return':stock['price_return']-bench['price_return'] if forecast['benchmark_type'] in {'industry','peer_basket'} and bench.get('status')=='active' else None,'mfe':stock['mfe'],'mae':stock['mae']})
  records.append(base)
 # Comparison/monitor outcomes are explicit records and never directional hits.
 for assessment in prediction['candidate_assessments']:
  if assessment['record_group']=='forecast':continue
  group='selection_comparison' if assessment['record_group']=='comparison_only' else 'monitor_resolution';outcome=lifecycle.get(assessment['ticker'],'active' if group=='selection_comparison' else 'data_unavailable');base={'record_id':f"{assessment['route_candidate_id']}:21",'prediction_id':None,'route_candidate_id':assessment['route_candidate_id'],'verification_horizon':21,'outcome_status':outcome,'price_return':None,'total_shareholder_return':None,'spy_relative_return':None,'sector_relative_return':None,'industry_relative_return':None,'mfe':None,'mae':None,'evaluation_group':group}
  entry=entry_records.get(assessment['route_candidate_id'])
  if group=='selection_comparison' and entry and entry.get('status')=='resolved':
   result=_returns(frames.get(assessment['ticker'],pd.DataFrame()),entry['entry_price'],entry['entry_price_timestamp'],21,'up');base['outcome_status']=result['status']
   if result['status']=='active':base.update({'price_return':result['price_return'],'total_shareholder_return':result['total_shareholder_return'],'mfe':result['mfe'],'mae':result['mae']})
  if group=='monitor_resolution':base.update(monitor_outcomes.get(assessment['route_candidate_id'],{'cause_resolved':False,'cause_type':'unknown','days_to_resolution':None,'continued':None,'reversed':None,'data_artifact':assessment['research_status']=='data_artifact'}))
  records.append(base)
 final_returns={r['route_candidate_id']:r['price_return'] for r in records if r['evaluation_group']=='directional_forecast' and r['price_return'] is not None}
 comparison_for={a['route_candidate_id']:a.get('comparison_for') for a in prediction['candidate_assessments'] if a['record_group']=='comparison_only'}
 for record in records:
  if record['evaluation_group']=='selection_comparison' and record['price_return'] is not None and final_returns.get(comparison_for.get(record['route_candidate_id'])) is not None:record['selection_value_return']=final_returns[comparison_for[record['route_candidate_id']]]-record['price_return']
 return records
