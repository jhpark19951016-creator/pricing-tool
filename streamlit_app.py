
# -*- coding: utf-8 -*-
import os, datetime as dt
import pandas as pd
import requests
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

def fetch_rtms(url, lawd5, ym):
    params = {
        "serviceKey": SERVICE_KEY,
        "LAWD_CD": lawd5,
        "DEAL_YMD": ym,
        "numOfRows": 1000,
        "pageNo": 1,
    }
    r = requests.get(url, params=params, timeout=20)
    root = ET.fromstring(r.text)
    rows = []
    for it in root.findall(".//item"):
        rows.append({c.tag: c.text for c in list(it)})
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
