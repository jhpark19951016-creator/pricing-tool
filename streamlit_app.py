import datetime as dt
import math
from dataclasses import dataclass
from typing import Dict, Any, List

import requests
import pandas as pd
import streamlit as st
import xmltodict

M2_PER_PYEONG = 3.305785


# -----------------------------
# Utils
# -----------------------------
def ym_backwards(end_ym: str, months: int) -> List[str]:
    y = int(end_ym[:4])
    m = int(end_ym[4:6])
    out = []
    cur = dt.date(y, m, 1)
    for _ in range(max(1, months)):
        out.append(cur.strftime("%Y%m"))
        py, pm = cur.year, cur.month - 1
        if pm == 0:
            pm = 12
            py -= 1
        cur = dt.date(py, pm, 1)
    return out


def to_int(x):
    if x is None:
        return 0
    return int(str(x).replace(",", ""))


def to_float(x):
    if x is None:
        return 0.0
    return float(str(x).replace(",", ""))


def pyeong_price(deal_amount_manwon, exclu_m2):
    if exclu_m2 <= 0:
        return 0
    won = deal_amount_manwon * 10000
    return (won / exclu_m2) * M2_PER_PYEONG


def safe_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


@dataclass
class RtmsConfig:
    service_key: str


def fetch_items(url, params):
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    obj = xmltodict.parse(r.text)
    return safe_list(obj["response"]["body"]["items"].get("item"))


@st.cache_data(ttl=600)
def get_trades(cfg, lawd_cd, ym, kind):
    if kind == "아파트":
        base = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    else:
        base = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade"

    params = {
        "serviceKey": cfg.service_key,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": ym,
        "numOfRows": "1000",
        "pageNo": "1",
    }

    rows = []
    for it in fetch_items(base, params):
        rows.append({
            "자산": kind,
            "단지명": (it.get("aptNm") or it.get("offiNm") or "").strip(),
            "전용㎡": to_float(it.get("excluUseAr")),
            "거래금액": to_int(it.get("dealAmount")),
            "거래일": f"{it.get('dealYear')}-{it.get('dealMonth')}-{it.get('dealDay')}"
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["평당가"] = df.apply(lambda r: pyeong_price(r["거래금액"], r["전용㎡"]), axis=1)
    return df


# -----------------------------
# UI
# -----------------------------
st.set_page_config(layout="wide")
st.title("분양가 산정 Tool (일반 + 상한제 + 실거래 자동)")

service_key = st.secrets.get("SERVICE_KEY", "")
cfg = RtmsConfig(service_key)

with st.sidebar:
    lawd = st.text_input("법정동코드", "11110")
    ym = st.text_input("기준 계약년월(YYYYMM)", dt.date.today().strftime("%Y%m"))
    months = st.slider("최근 개월", 1, 24, 12)
    assets = st.multiselect("자산", ["아파트", "오피스텔"], ["아파트", "오피스텔"])
    area = st.number_input("기준 전용면적", 10.0, 200.0, 84.0)
    tol = st.number_input("허용오차", 0.0, 10.0, 3.0)
    recent_n = st.number_input("최근 N건", 0, 100, 30)

c1, c2 = st.columns(2)

with c1:
    st.subheader("일반 사업성")
    cost = st.number_input("총원가(원)", 0, value=550_000_000_000)
    margin = st.slider("이윤율(%)", 0.0, 20.0, 8.0)
    supply = st.number_input("공급면적 합계", 1000.0, value=45000.0)

with c2:
    st.subheader("상한제")
    cap = st.number_input("상한제 총액(원)", 0, value=520_000_000_000)
    cap_supply = st.number_input("상한제 공급면적", 1000.0, value=45000.0)

run = st.button("실거래 자동 조회")

market_avg = 0
if run and service_key:
    dfs = []
    for i in range(months):
        ymi = ym_backwards(ym, months)[i]
        for a in assets:
            df = get_trades(cfg, lawd, ymi, a)
            if not df.empty:
                dfs.append(df)

    if dfs:
        all_df = pd.concat(dfs)
        f = all_df[(all_df["전용㎡"].between(area - tol, area + tol))]
        f = f.sort_values("거래일", ascending=False)
        if recent_n > 0:
            f = f.head(recent_n)
        market_avg = int(f["평당가"].mean())
        st.success(f"실거래 평균: {market_avg:,} 원/평")
        st.dataframe(f.head(30))

st.divider()
st.subheader("결과 요약")

g_py = int(((cost * (1 + margin / 100)) / supply) * M2_PER_PYEONG)
c_py = int((cap / cap_supply) * M2_PER_PYEONG)

k1, k2, k3 = st.columns(3)
k1.metric("일반 사업성", f"{g_py:,} 원/평")
k2.metric("상한제", f"{c_py:,} 원/평")
k3.metric("실거래 시세", "-" if market_avg == 0 else f"{market_avg:,} 원/평")
