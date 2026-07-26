#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import yfinance as yf
from src.entry_resolution import resolve_generation_entries

def download(ticker):
 return yf.download(ticker,period='10d',interval='1d',auto_adjust=False,actions=True,progress=False,threads=False,multi_level_index=False)
def main():
 if not (ROOT/'docs/manifest.json').exists():
  print('no schema 2.0 manifest; nothing to resolve');return
 path,payload=resolve_generation_entries(ROOT/'docs/manifest.json',ROOT,download)
 print(f"entry resolution {payload['entry_resolution_id']}: {path.relative_to(ROOT)}")
if __name__=='__main__':main()
