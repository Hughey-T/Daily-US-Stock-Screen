#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.github_persistence import verify_github_prediction

def fetch(url):
 last=None
 for attempt in range(5):
  try:
   with urlopen(Request(url,headers={'User-Agent':'Daily-US-Stock-Screen-integrity/2.0'}),timeout=30) as response:return response.read()
  except OSError as exc:last=exc;time.sleep(2**attempt)
 raise last
def main():
 p=argparse.ArgumentParser();p.add_argument('--repository',required=True);p.add_argument('--branch',required=True);p.add_argument('--commit',required=True);args=p.parse_args();index=json.loads((ROOT/'docs/predictions/index-v2.json').read_text())
 proofs=[verify_github_prediction(entry,args.repository,args.branch,args.commit,fetch) for entry in index['predictions']];print(json.dumps(proofs,indent=2))
if __name__=='__main__':main()
