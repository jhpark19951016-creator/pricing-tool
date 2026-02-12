
# -*- coding: utf-8 -*-
import os, datetime as dt
import pandas as pd
import requests

from urllib.parse import quote_plus

def normalize_service_key(k: str) -> str:
    """data.go.kr SERVICE_KEY 정리(따옴표/개행 제거). 디코딩(unquote) 금지."""
    if not k:
        return ""
    k = str(k).strip()
    k = k.strip('"').strip("'")
    k = k.replace("\n", "").replace("\r", "").strip()
    return k
import streamlit as st
import folium
from streamlit_folium import st_folium
import xml.etree.ElementTree as ET

st.set_page_config(page_title="분양가 산정 Tool (풀세트 안정형)", layout="wide")
st.title("분양가 산정 Tool – 풀세트(안정형)")

DEFAULT_CENTER = (37.5665, 126.9780)
APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
OFFI_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTradeDev/getRTMSDataSvcOffiTradeDev"

SERVICE_KEY = st.secrets.get("SERVICE_KEY", os.environ.get("SERVICE_KEY", "")).strip()

with st.sidebar:
    st.header("설정")
    product = st.selectbox("상품", ["아파트", "오피스텔", "아파트+오피스텔"], index=2)
    end_ym = st.text_input("기준 계약년월(YYYYMM)", value=dt.date.today().strftime("%Y%m"))
    months_back = st.number_input("최근 기간(개월)", 1, 36, 6)

st.session_state.setdefault("lat", DEFAULT_CENTER[0])
st.session_state.setdefault("lon", DEFAULT_CENTER[1])
st.session_state.setdefault("lawd10", "")

m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
folium.Marker([st.session_state.lat, st.session_state.lon], tooltip="대상지").add_to(m)
out = st_folium(m, height=420, use_container_width=True)

if isinstance(out, dict) and out.get("last_clicked"):
    st.session_state.lat = out["last_clicked"]["lat"]
    st.session_state.lon = out["last_clicked"]["lng"]

st.write("핀 좌표:", st.session_state.lat, st.session_state.lon)

st.subheader("법정동코드(10자리)")
st.session_state.lawd10 = st.text_input("법정동코드 입력", value=st.session_state.lawd10)

def fetch_rtms(lawd: str, ym: str) -> pd.DataFrame:
    """국토부 RTMS 실거래(아파트) 조회.
    - SERVICE_KEY에 '%'가 포함되어 있으면: '인코딩 키'로 보고 그대로 사용(추가 인코딩 금지)
    - '%'가 없으면: '디코딩 키'로 보고 URL 인코딩(+,/,= 처리)
    """
    if not SERVICE_KEY:
        raise RuntimeError("SERVICE_KEY가 비어있습니다. Streamlit secrets의 SERVICE_KEY를 확인하세요.")

    # serviceKey 준비
    if "%" in SERVICE_KEY:
        sk = SERVICE_KEY
    else:
        sk = quote_plus(SERVICE_KEY, safe="")

    qs = "&".join([
        f"serviceKey={sk}",
        f"LAWD_CD={lawd}",
        f"DEAL_YMD={ym}",
        "numOfRows=1000",
        "pageNo=1",
    ])
    url = f"{API_URL}?{qs}"

    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        hint = ""
        if r.status_code == 403:
            hint = " (힌트: serviceKey 인코딩/디코딩 형태 불일치로 403이 자주 발생합니다. '%' 포함 여부를 확인하세요.)"
        raise RuntimeError(f"실거래 조회 실패: HTTP {r.status_code}{hint}")

    root = ET.fromstring(r.text)
    items = root.findall(".//item")
    rows = []
    for it in items:
        def t(tag):
            el = it.find(tag)
            return el.text.strip() if el is not None and el.text else ""
        rows.append({
            "거래금액(만원)": t("dealAmount").replace(",", "").replace(" ", ""),
            "년": t("dealYear"),
            "월": t("dealMonth"),
            "일": t("dealDay"),
            "전용면적": t("excluUseAr"),
            "층": t("floor"),
            "아파트": t("aptNm"),
            "법정동": t("umdNm"),
            "지번": t("jibun"),
            "건축년도": t("buildYear"),
            "도로명": t("roadNm"),
        })
    return pd.DataFrame(rows)

if st.button("실거래 조회"):
    if not SERVICE_KEY:
        st.error("SERVICE_KEY가 없습니다.")
    elif not st.session_state.lawd10:
        st.error("법정동코드를 입력하세요.")
    else:
        lawd5 = st.session_state.lawd10[:5]
        dfs = []
        y, m = int(end_ym[:4]), int(end_ym[4:])
        for i in range(months_back):
            mm = m - i
            yy = y
            while mm <= 0:
                yy -= 1
                mm += 12
            ym = f"{yy:04d}{mm:02d}"
            if product in ("아파트", "아파트+오피스텔"):
                dfs.append(fetch_rtms(APT_URL, lawd5, ym))
            if product in ("오피스텔", "아파트+오피스텔"):
                dfs.append(fetch_rtms(OFFI_URL, lawd5, ym))

        merged = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        if merged.empty:
            st.warning("조회된 실거래가가 없습니다.")
        else:
            st.success(f"총 {len(merged):,}건")
            st.dataframe(merged.head(300), use_container_width=True)

st.caption("풀세트 안정형 – 최종 재업로드")
