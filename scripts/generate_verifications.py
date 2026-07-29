#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import yfinance as yf
from scripts.validate_v2_records import validate_verification_data
from src.records import build_verification_bundle,persist_verification_bundle
from src.verification import generate_verification_records,market_data_hash

def main():
 index_path=ROOT/'docs/predictions/index-v2.json'
 if not index_path.exists():print('no schema 2.0 predictions');return
 for entry in json.loads(index_path.read_text())['predictions']:
  path=ROOT/entry['path'];prediction=json.loads(path.read_text());entry_artifact=json.loads((ROOT/prediction['entry_resolution_path']).read_text());entries={r['route_candidate_id']:r for r in entry_artifact['records']}
  tickers={a['ticker'] for a in prediction['candidate_assessments']}|{'SPY'}|{f['benchmark_symbol'] for f in prediction['horizon_forecasts']}
  frames={ticker:yf.download(ticker,period='5y',interval='1d',auto_adjust=False,actions=True,progress=False,threads=False,multi_level_index=False) for ticker in sorted(tickers)}
  records=generate_verification_records(prediction,frames,entry_records=entries);dates=[max(frame.index) for frame in frames.values() if not frame.empty];cutoff=(max(dates).tz_localize('UTC') if getattr(max(dates),'tzinfo',None) is None else max(dates).tz_convert('UTC')).isoformat() if dates else datetime.now(timezone.utc).isoformat();source_hash=market_data_hash(frames);bundle=build_verification_bundle(path,records,ROOT,cutoff,source_hash);persist_verification_bundle(bundle,ROOT,validate_verification_data)
  print(f"verified {entry['prediction_run_id']}: {len(records)} records")
if __name__=='__main__':main()
