import json, re, sys
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path
import pandas as pd
import requests

URL="https://fishdata.kmi.re.kr/front/kmisys/prdlcCttmltDataList.do"
REGIONS={"목포":["목포"],"해남":["해남"],"완도":["완도"]}
ITEMS=["문어","낙지","전복","꽃게","갈치","민어","병어","참돔","농어","우럭","붕장어","갑오징어","오징어","새우","키조개","바지락","굴","멸치"]
HEAD={"User-Agent":"Mozilla/5.0 (compatible; seafood-price-monitor/1.0)","Accept-Language":"ko-KR,ko;q=0.9"}

def num(v):
    s=re.sub(r"[^0-9.\-]","",str(v))
    try:return float(s)
    except:return None

def region(name):
    s=str(name)
    for r,keys in REGIONS.items():
        if any(k in s for k in keys): return r
    return None

def table(html):
    try: tabs=pd.read_html(StringIO(html))
    except Exception: return None
    for t in tabs:
        t.columns=[str(c[-1] if isinstance(c,tuple) else c).strip() for c in t.columns]
        if {"위판일자","산지조합","어종명","중량","금액"}.issubset(set(t.columns)): return t
    return None

def main():
    session=requests.Session(); session.headers.update(HEAD)
    allrows=[]; empty_streak=0
    for page in range(1,121):
        r=session.get(URL,params={"pageIndex":page},timeout=25)
        r.raise_for_status(); t=table(r.text)
        if t is None or t.empty:
            empty_streak+=1
            if empty_streak>=3: break
            continue
        empty_streak=0
        for _,x in t.iterrows():
            reg=region(x.get("산지조합",""))
            item=str(x.get("어종명","")).strip()
            if not reg or not any(k in item for k in ITEMS): continue
            w=num(x.get("중량")); a=num(x.get("금액"))
            if not w or not a or w<=0 or a<=0: continue
            p=a/w
            if p<100 or p>2000000: continue
            ds=re.sub(r"\D","",str(x.get("위판일자","")))
            if len(ds)!=8: continue
            allrows.append({
              "date":f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}","region":reg,
              "union":str(x.get("산지조합","")).strip(),"market":str(x.get("위판장","")).strip(),
              "item":item,"state":str(x.get("어종상태","")).strip(),
              "fishery":str(x.get("어업명","")).strip(),"origin":str(x.get("원산지명","")).strip(),
              "weight":round(w,3),"amount":round(a,0),"kg_price":round(p,1)
            })
        # 최신 공개 페이지는 보통 20건/페이지. 너무 오래된 영역까지 무한 검색하지 않음.
    uniq=[]; seen=set()
    for x in allrows:
        k=tuple(x.items())
        if k not in seen: seen.add(k); uniq.append(x)
    uniq.sort(key=lambda x:(x["date"],x["region"],x["item"]),reverse=True)
    # 너무 큰 파일 방지
    uniq=uniq[:12000]
    if not uniq:
        print("No matching rows; preserving existing data.json",file=sys.stderr)
        return
    kst=timezone(timedelta(hours=9))
    payload={"updated_at":datetime.now(kst).strftime("%Y-%m-%d %H:%M KST"),"source":URL,"rows":uniq}
    Path("data.json").write_text(json.dumps(payload,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    print("rows",len(uniq))

if __name__=="__main__": main()
