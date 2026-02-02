import os
import datetime as dt
import math
from dataclasses import dataclass
from typing import Dict, Any, List

import requests
import pandas as pd
import streamlit as st
import xmltodict

M2_PER_PYEONG = 3.305785


def ym_backwards(end_ym: str, months: int) -> List[str]:
    """Return YYYYMM list going backwards, inclusive of end_ym."""
    y = int(end_ym[:4])
    m = int(end_ym[4:6])
    out = []
    cur = dt.date(y, m, 1)
    for _ in range(max(1, int(months))):
        out.append(cur.strftime("%Y%m"))
        py = cur.year
        pm = cur.month - 1
        if pm == 0:
            pm = 12
            py -= 1
        cur = dt.date(py, pm, 1)
    return out


def to_int(x: Any) -> int:
    if x is None:
        return 0
    if isinstance(x, (int, float)):
        return int(x)
    s = str(x).strip().replace(",", "")
    if not s:
        return 0
    return int(float(s))


def to_float(x: Any) -> float:
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if not s:
        return 0.0
    return float(s)


def won_per_pyeong_from_trade(deal_amount_manwon: int, exclu_m2: float) -> float:
    """deal_amount in 만원, exclu_m2 in ㎡ -> 원/평(전용 기준)"""
    if exclu_m2 <= 0:
        return 0.0
    won = deal_amount_manwon * 10000  # 만원 -> 원
    won_per_m2 = won / exclu_m2
    return won_per_m2 * M2_PER_PYEONG


def safe_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


@dataclass
class RtmsConfig:
    service_key: str
    timeout_sec: int = 20


def fetch_rtms_items(base_url: str, params: Dict[str, str], timeout: int = 20) -> List[Dict[str, Any]]:
    r = requests.get(base_url, params=params, timeout=timeout)
    r.raise_for_status()
    obj = xmltodict.parse(r.text)
    resp = obj.get("response", {})
    body = resp.get("body", {})
    items = body.get("items", {})
    return safe_list(items.get("item"))


@st.cache_data(ttl=600, show_spinner=False)
def get_apartment_trades(cfg: RtmsConfig, lawd_cd: str, deal_ym: str) -> pd.DataFrame:
    base_url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    params = {
        "serviceKey": cfg.service_key,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ym,
        "numOfRows": "1000",
        "pageNo": "1",
    }
    items = fetch_rtms_items(base_url, params, timeout=cfg.timeout_sec)

    rows = []
    for it in items:
        rows.append({
            "자산": "아파트",
            "단지명": (it.get("aptNm") or "").strip(),
            "전용㎡": to_float(it.get("excluUseAr")),
            "거래금액(만원)": to_int(it.get("dealAmount")),
            "거래년": to_int(it.get("dealYear")),
            "거래월": to_int(it.get("dealMonth")),
            "거래일": to_int(it.get("dealDay")),
            "층": to_int(it.get("floor")),
            "건축년도": to_int(it.get("buildYear")),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["거래일자"] = pd.to_datetime(
            df["거래년"].astype(str) + "-" + df["거래월"].astype(str) + "-" + df["거래일"].astype(str),
            errors="coerce"
        )
        df["평당가(원/평,전용)"] = df.apply(
            lambda r: won_per_pyeong_from_trade(int(r["거래금액(만원)"]), float(r["전용㎡"])),
            axis=1
        )
    return df


@st.cache_data(ttl=600, show_spinner=False)
def get_officetel_trades(cfg: RtmsConfig, lawd_cd: str, deal_ym: str) -> pd.DataFrame:
    base_url = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade"
    params = {
        "serviceKey": cfg.service_key,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ym,
        "numOfRows": "1000",
        "pageNo": "1",
    }
    items = fetch_rtms_items(base_url, params, timeout=cfg.timeout_sec)

    rows = []
    for it in items:
        rows.append({
            "자산": "오피스텔",
            "단지명": (it.get("offiNm") or it.get("aptNm") or "").strip(),
            "전용㎡": to_float(it.get("excluUseAr")),
            "거래금액(만원)": to_int(it.get("dealAmount")),
            "거래년": to_int(it.get("dealYear")),
            "거래월": to_int(it.get("dealMonth")),
            "거래일": to_int(it.get("dealDay")),
            "층": to_int(it.get("floor")),
            "건축년도": to_int(it.get("buildYear")),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["거래일자"] = pd.to_datetime(
            df["거래년"].astype(str) + "-" + df["거래월"].astype(str) + "-" + df["거래일"].astype(str),
            errors="coerce"
        )
        df["평당가(원/평,전용)"] = df.apply(
            lambda r: won_per_pyeong_from_trade(int(r["거래금액(만원)"]), float(r["전용㎡"])),
            axis=1
        )
    return df


def hogang_style_filter(df: pd.DataFrame, target_m2: float, tol_m2: float, keyword: str, recent_n: int) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out = out[out["전용㎡"].between(target_m2 - tol_m2, target_m2 + tol_m2)]
    if keyword.strip():
        kw = keyword.strip().lower()
        out = out[out["단지명"].fillna("").str.lower().str.contains(kw)]
    out = out.sort_values("거래일자", ascending=False)
    if recent_n > 0:
        out = out.head(int(recent_n))
    return out


@st.cache_data(show_spinner=False)
def load_lawd_codes() -> pd.DataFrame:
    path = "lawd_codes.csv"
    if os.path.exists(path):
        df = pd.read_csv(path, dtype={"code": str})
        if "code" not in df.columns or "name" not in df.columns:
            return pd.DataFrame([{"code": "11110", "name": "서울 종로구", "label": "서울 종로구 (11110)"}])
        df["code"] = df["code"].astype(str).str.zfill(5)
        df["label"] = df["name"].astype(str) + " (" + df["code"] + ")"
        return df.sort_values(["name", "code"]).reset_index(drop=True)
    return pd.DataFrame([{"code": "11110", "name": "서울 종로구", "label": "서울 종로구 (11110)"}])


def fmt0(x: float) -> str:
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "-"


st.set_page_config(page_title="분양가 산정 Tool", layout="wide")
st.title("분양가 산정 Tool (일반 사업성 + 상한제 + 실거래 자동)")
st.caption("회사 PC에서도 URL로 사용. 주변시세는 국토부 실거래가 OpenAPI를 자동 조회합니다.")

service_key = st.secrets.get("SERVICE_KEY", "").strip()
if not service_key:
    st.warning("SERVICE_KEY가 설정되지 않았습니다. (Streamlit Cloud → App settings → Secrets에 넣어주세요)")

cfg = RtmsConfig(service_key=service_key)
lawd_df = load_lawd_codes()

with st.sidebar:
    st.subheader("주변 시세 자동 (실거래)")
    q = st.text_input("지역 검색(선택)", value="")
    view = lawd_df
    if q.strip():
        qq = q.strip().lower()
        view = view[view["label"].fillna("").str.lower().str.contains(qq)]
        if view.empty:
            st.info("검색 결과가 없습니다. lawd_codes.csv에 지역을 추가하세요.")
            view = lawd_df

    sel = st.selectbox("법정동코드(시군구 5자리) 선택", view["label"].tolist(), index=0)
    lawd_cd = view[view["label"] == sel]["code"].iloc[0]

    end_ym = st.text_input("기준 계약년월(YYYYMM)", value=dt.date.today().strftime("%Y%m"))
    months = st.number_input("최근 기간(개월)", min_value=1, max_value=36, value=12, step=1)
    assets = st.multiselect("조회 대상", ["아파트", "오피스텔"], default=["아파트", "오피스텔"])

    st.divider()
    st.subheader("호갱노노식 집계")
    target_m2 = st.number_input("기준 전용면적(㎡)", min_value=10.0, max_value=300.0, value=84.0, step=1.0)
    tol_m2 = st.number_input("허용오차(±㎡)", min_value=0.0, max_value=20.0, value=3.0, step=0.5)
    keyword = st.text_input("단지명 키워드(선택)", value="")
    recent_n = st.number_input("최근 N건 (0=미사용)", min_value=0, max_value=200, value=30, step=5)

    st.divider()
    st.subheader("시세 → 분양가(공급기준)")
    exclusive_ratio = st.number_input("전용률(전용/공급) 예: 0.70", min_value=0.50, max_value=0.95, value=0.70, step=0.01)
    alpha = st.number_input("시세 반영계수 α", min_value=0.50, max_value=1.50, value=0.98, step=0.01)
    beta = st.number_input("상품 프리미엄/디스카운트 β", min_value=0.70, max_value=1.30, value=1.00, step=0.01)

col1, col2 = st.columns(2)

with col1:
    st.subheader("1) 일반 사업성(원가+이윤) — 공급기준")
    g_supply_m2 = st.number_input("공급면적 합계(㎡)", value=45000.0, step=100.0)
    g_land = st.number_input("택지비(총, 원)", value=350_000_000_000.0, step=1_000_000.0, format="%.0f")
    g_const = st.number_input("건축비(총, 원)", value=176_000_000_000.0, step=1_000_000.0, format="%.0f")
    g_add = st.number_input("가산비(총, 원)", value=20_000_000_000.0, step=1_000_000.0, format="%.0f")
    g_design = st.number_input("설계·감리비(총, 원)", value=5_000_000_000.0, step=1_000_000.0, format="%.0f")
    g_fin = st.number_input("금융비용(총, 원)", value=12_000_000_000.0, step=1_000_000.0, format="%.0f")
    g_mkt = st.number_input("분양·홍보비(총, 원)", value=4_000_000_000.0, step=1_000_000.0, format="%.0f")
    g_oh = st.number_input("일반관리비(총, 원)", value=6_000_000_000.0, step=1_000_000.0, format="%.0f")
    g_etc = st.number_input("기타(총, 원)", value=3_000_000_000.0, step=1_000_000.0, format="%.0f")
    g_margin = st.number_input("이윤율(%)", min_value=0.0, max_value=30.0, value=8.0, step=0.5)
    g_vat = st.number_input("부가세율(%)", min_value=0.0, max_value=20.0, value=10.0, step=1.0)

with col2:
    st.subheader("2) 분양가상한제(간이) — 공급기준")
    c_supply_m2 = st.number_input("상한제 기준 공급면적 합계(㎡)", value=45000.0, step=100.0)
    c_land = st.number_input("택지비(총, 원)", value=350_000_000_000.0, step=1_000_000.0, format="%.0f")
    c_basic = st.number_input("기본형 건축비(총, 원)", value=176_000_000_000.0, step=1_000_000.0, format="%.0f")
    c_add = st.number_input("가산비(총, 원)", value=20_000_000_000.0, step=1_000_000.0, format="%.0f")
    c_etc = st.number_input("간접비/기타(선택, 원)", value=0.0, step=1_000_000.0, format="%.0f")
    c_vat = st.number_input("상한제 부가세율(%, 선택)", min_value=0.0, max_value=20.0, value=0.0, step=1.0)

g_cost = g_land + g_const + g_add + g_design + g_fin + g_mkt + g_oh + g_etc
g_profit = g_cost * (g_margin / 100.0)
g_supply_price = g_cost + g_profit
g_vat_amt = g_supply_price * (g_vat / 100.0)
g_total = g_supply_price + g_vat_amt
g_won_per_py_supply = (g_total / g_supply_m2) * M2_PER_PYEONG if g_supply_m2 > 0 else 0.0

c_supply_price = c_land + c_basic + c_add + c_etc
c_vat_amt = c_supply_price * (c_vat / 100.0)
c_total = c_supply_price + c_vat_amt
c_won_per_py_supply = (c_total / c_supply_m2) * M2_PER_PYEONG if c_supply_m2 > 0 else 0.0

st.divider()
st.subheader("3) 주변 시세 자동(실거래가) — 아파트 + 오피스텔")

run = st.button("실거래가 자동 조회 / 재계산", type="primary")

market_avg_exclu = 0.0
market_price_supply = 0.0
market_count = 0

if run:
    if not service_key:
        st.error("SERVICE_KEY가 비어 있습니다. 배포 후 Secrets에 설정해주세요.")
    else:
        yms = ym_backwards(end_ym, int(months))
        dfs = []
        with st.spinner("실거래가 조회 중... (월별 호출 후 합산)"):
            if "아파트" in assets:
                for ym in yms:
                    df = get_apartment_trades(cfg, lawd_cd, ym)
                    if not df.empty:
                        dfs.append(df)
            if "오피스텔" in assets:
                for ym in yms:
                    df = get_officetel_trades(cfg, lawd_cd, ym)
                    if not df.empty:
                        dfs.append(df)

        merged = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        if merged.empty:
            st.warning("조회된 실거래가가 없습니다. (지역/기간/면적대/키워드를 확인해주세요)")
        else:
            filtered = hogang_style_filter(merged, float(target_m2), float(tol_m2), keyword, int(recent_n))
            market_count = len(filtered)
            if market_count > 0:
                market_avg_exclu = float(filtered["평당가(원/평,전용)"].mean())
                # 공급기준 시세기반 분양가(공급) = 전용기준 × 전용률 × α × β
                market_price_supply = market_avg_exclu * float(exclusive_ratio) * float(alpha) * float(beta)

                st.success(
                    f"집계 {market_count}건 · "
                    f"평당 평균(전용) {fmt0(market_avg_exclu)}원/평 · "
                    f"시세기반 분양가(공급) {fmt0(market_price_supply)}원/평"
                )
            else:
                st.warning("필터(면적대/키워드/최근N건) 적용 후 데이터가 없습니다.")

            st.dataframe(
                filtered.sort_values("거래일자", ascending=False).head(50),
                use_container_width=True,
                hide_index=True
            )

st.divider()
st.subheader("요약 비교 (공급기준)")

k1, k2, k3, k4 = st.columns(4)
k1.metric("일반 사업성(공급)", f"{fmt0(g_won_per_py_supply)} 원/평")
k2.metric("상한제(공급)", f"{fmt0(c_won_per_py_supply)} 원/평")
k3.metric("시세기반 분양가(공급)", "-" if market_price_supply <= 0 else f"{fmt0(market_price_supply)} 원/평")

rec_supply = min(
    c_won_per_py_supply if c_won_per_py_supply > 0 else math.inf,
    market_price_supply if market_price_supply > 0 else math.inf
)
k4.metric(
    "권장 분양가(공급, 예시)",
    "-" if rec_supply == math.inf else f"{fmt0(rec_supply)} 원/평",
    help="예시 로직: MIN(상한제(공급), 시세기반(공급))"
)

st.caption(
    "※ 시세기반(공급) = 실거래 평당(전용) × 전용률(전용/공급) × α × β. "
    "API는 월(YYYYMM) 단위라 ‘최근 N개월’은 월별 호출을 합산합니다."
)
