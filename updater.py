import json, re
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

KAMIS_URL = "https://www.kamis.or.kr/customer/price/wholesale/catalogue.do"
KMI_URL = "https://fishdata.kmi.re.kr/front/kmisys/prdlcCttmltDataList.do"

ITEMS = ["문어","낙지","전복","꽃게","갈치","민어","병어","참돔","농어","우럭","붕장어","장어","갑오징어","오징어","새우","키조개","바지락","굴","멸치","고등어","명태","김","미역","다시마"]
REGIONS = {"목포":["목포"], "해남":["해남"], "완도":["완도"]}
HEADERS = {"User-Agent":"Mozilla/5.0 (compatible; seafood-price-monitor/2.0)", "Accept-Language":"ko-KR,ko;q=0.9"}

def clean_num(v):
    s = re.sub(r"[^0-9.\-]", "", str(v))
    if not s or s == "-":
        return None
    try:
        return float(s)
    except Exception:
        return None

def flat_cols(df):
    out=[]
    for c in df.columns:
        if isinstance(c, tuple):
            parts=[str(x).strip() for x in c if str(x).strip() and str(x).strip().lower()!="nan"]
            out.append(" ".join(parts))
        else:
            out.append(str(c).strip())
    df=df.copy()
    df.columns=out
    return df

def unit_to_kg(unit):
    s=str(unit).replace(" ","").lower()
    m=re.search(r"(\d+(?:\.\d+)?)kg", s)
    if m:
        return float(m.group(1))
    m=re.search(r"(\d+(?:\.\d+)?)g", s)
    if m:
        return float(m.group(1))/1000
    if s == "kg" or s.startswith("1kg"):
        return 1.0
    return None

def kamis_wholesale(session):
    today=datetime.now(timezone(timedelta(hours=9))).date()
    errors=[]
    for back in range(0,12):
        day=today-timedelta(days=back)
        try:
            r=session.get(KAMIS_URL, params={
                "action":"daily",
                "itemcategorycode":"600",
                "regday":day.isoformat()
            }, timeout=25)
            r.raise_for_status()
            tables=pd.read_html(StringIO(r.text))
        except Exception as e:
            errors.append(f"{day}:{e}")
            continue

        target=None
        for t in tables:
            t=flat_cols(t)
            cols=set(t.columns)
            if any("품목" in c for c in cols) and any("단위" in c for c in cols) and any("등급" in c for c in cols):
                target=t
                break
        if target is None or target.empty:
            continue

        item_col=next((c for c in target.columns if "품목" in c), None)
        kind_col=next((c for c in target.columns if "품종" in c), None)
        unit_col=next((c for c in target.columns if "단위" in c), None)
        rank_col=next((c for c in target.columns if "등급" in c), None)
        cur_col=next((c for c in target.columns if "당일" in c), None)
        prev_col=next((c for c in target.columns if "1일전" in c), None)
        if prev_col is None:
            prev_col=next((c for c in target.columns if "1주일전" in c), None)
        if not item_col or not cur_col:
            continue

        rows=[]
        for _,x in target.iterrows():
            item=str(x.get(item_col,"")).strip()
            if not item or item.lower()=="nan":
                continue
            if not any(k in item for k in ITEMS):
                continue
            unit=str(x.get(unit_col,"")).strip() if unit_col else ""
            current=clean_num(x.get(cur_col))
            previous=clean_num(x.get(prev_col)) if prev_col else None
            if current is None:
                continue
            kg=unit_to_kg(unit)
            rows.append({
                "date":day.isoformat(),
                "item":item,
                "kind":str(x.get(kind_col,"")).strip() if kind_col else "",
                "unit":unit,
                "rank":str(x.get(rank_col,"")).strip() if rank_col else "",
                "price":current,
                "previous_price":previous,
                "kg_price":round(current/kg,1) if kg and kg>0 else None,
                "previous_kg_price":round(previous/kg,1) if kg and previous is not None and kg>0 else None,
                "source":"KAMIS 중도매인 판매가격"
            })
        if rows:
            return rows, day.isoformat(), errors
    return [], None, errors

def detect_region(name):
    s=str(name)
    for region,keys in REGIONS.items():
        if any(k in s for k in keys):
            return region
    return None

def kmi_table(html):
    try:
        tables=pd.read_html(StringIO(html))
    except Exception:
        return None
    for t in tables:
        t=flat_cols(t)
        if all(any(key in c for c in t.columns) for key in ["위판일자","산지조합","어종명","중량","금액"]):
            return t
    return None

def find_col(cols, key):
    return next((c for c in cols if key in c), None)

def kmi_local(session):
    rows=[]
    errors=[]
    empty=0
    for page in range(1,61):
        try:
            r=session.get(KMI_URL, params={"pageIndex":page}, timeout=25)
            r.raise_for_status()
            t=kmi_table(r.text)
        except Exception as e:
            errors.append(f"page{page}:{e}")
            empty+=1
            if empty>=3:
                break
            continue
        if t is None or t.empty:
            empty+=1
            if empty>=3:
                break
            continue
        empty=0
        cols=t.columns
        c_date=find_col(cols,"위판일자")
        c_union=find_col(cols,"산지조합")
        c_market=find_col(cols,"위판장")
        c_item=find_col(cols,"어종명")
        c_state=find_col(cols,"어종상태")
        c_weight=find_col(cols,"중량")
        c_amount=find_col(cols,"금액")
        if not all([c_date,c_union,c_item,c_weight,c_amount]):
            continue
        for _,x in t.iterrows():
            union=str(x.get(c_union,"")).strip()
            reg=detect_region(union)
            item=str(x.get(c_item,"")).strip()
            if not reg or not any(k in item for k in ITEMS):
                continue
            w=clean_num(x.get(c_weight))
            a=clean_num(x.get(c_amount))
            if not w or not a or w<=0 or a<=0:
                continue
            p=a/w
            if p<100 or p>2000000:
                continue
            ds=re.sub(r"\D","",str(x.get(c_date,"")))
            if len(ds)!=8:
                continue
            rows.append({
                "date":f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}",
                "region":reg,
                "union":union,
                "market":str(x.get(c_market,"")).strip() if c_market else "",
                "item":item,
                "state":str(x.get(c_state,"")).strip() if c_state else "",
                "weight":round(w,3),
                "amount":round(a,0),
                "kg_price":round(p,1),
                "source":"KMI 위판정보"
            })
    seen=set(); out=[]
    for x in rows:
        k=(x["date"],x["region"],x["union"],x["market"],x["item"],x["weight"],x["amount"])
        if k not in seen:
            seen.add(k); out.append(x)
    out.sort(key=lambda x:(x["date"],x["region"],x["item"]), reverse=True)
    return out[:10000], errors

def main():
    session=requests.Session(); session.headers.update(HEADERS)
    wholesale, wholesale_date, werr=kamis_wholesale(session)
    local, lerr=kmi_local(session)
    kst=timezone(timedelta(hours=9))
    payload={
        "updated_at":datetime.now(kst).strftime("%Y-%m-%d %H:%M KST"),
        "wholesale_date":wholesale_date,
        "wholesale":wholesale,
        "local":local,
        "sources":{
            "wholesale":"https://www.kamis.or.kr/customer/price/wholesale/catalogue.do",
            "local":KMI_URL
        },
        "errors":{"wholesale":werr[-3:], "local":lerr[-3:]}
    }
    Path("data.json").write_text(json.dumps(payload,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    print("wholesale",len(wholesale),"local",len(local),"date",wholesale_date)

if __name__=="__main__":
    main()
