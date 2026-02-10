# -*- coding: utf-8 -*-
import os
import re
import json
import datetime as dt

import pandas as pd
import requests
import streamlit as st
import folium
from streamlit_folium import st_folium
import xml.etree.ElementTree as ET

st.set_page_config(page_title="분양가 산정 Tool (안정형)", layout="wide")
st.title("분양가 산정 Tool – 안정형")

DEFAULT_CENTER = (37.5665, 126.9780)

APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
OFFI_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTradeDev/getRTMSDataSvcOffiTradeDev"

# 공공데이터포털(실거래) 키
SERVICE_KEY = st.secrets.get("SERVICE_KEY", os.environ.get("SERVICE_KEY", "")).strip()

# ✅ 법정동코드 자동추적용 (VWorld 역지오코딩 등)
# - 아래 중 아무 키 이름으로 넣어도 인식합니다.
VWORLD_KEY = (
    st.secrets.get("VWORLD_KEY", "")
    or st.secrets.get("VWORLD_API_KEY", "")
    or os.environ.get("VWORLD_KEY", "")
    or os.environ.get("VWORLD_API_KEY", "")
).strip()


def make_year_month_options(months: int = 72):
    """오늘 기준 최근 N개월 범위에서 (years, months_by_year) 생성."""
    today = dt.date.today()
    pairs = []
    y, m = today.year, today.month
    for _ in range(months):
        pairs.append((y, m))
        m -= 1
        if m == 0:
            y -= 1
            m = 12

    years = sorted({yy for yy, _ in pairs}, reverse=True)
    months_by_year = {}
    for yy in years:
        ms = sorted({mm for y2, mm in pairs if y2 == yy}, reverse=True)
        months_by_year[yy] = ms
    return years, months_by_year


def _extract_10digit_code(obj):
    """응답(JSON) 어디에 있든 10자리 숫자를 찾아 첫 번째를 반환."""
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    m = re.search(r"\b\d{10}\b", s)
    return m.group(0) if m else None


def vworld_reverse_geocode(lat: float, lon: float):
    """VWorld 역지오코딩 → 법정동코드(10자리) 추출 시도."""
    if not VWORLD_KEY:
        return None

    url = "https://api.vworld.kr/req/address"
    params = {
        "service": "address",
        "request": "getAddress",
        "version": "2.0",
        "crs": "epsg:4326",
        "point": f"{lon},{lat}",  # VWorld는 lon,lat
        "format": "json",
        "type": "PARCEL",
        "key": VWORLD_KEY,
    }

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    # 안전하게 10자리 숫자 패턴을 전역 탐색
    return _extract_10digit_code(data)


with st.sidebar:
    st.header("설정")

    product = st.selectbox("상품", ["아파트", "오피스텔", "아파트+오피스텔"], index=2)

    # ✅ 1) 기준 계약년월: 년/월 분리 선택형
    years, months_by_year = make_year_month_options(months=72)  # 최근 6년
    today = dt.date.today()

    default_year = today.year if today.year in years else years[0]
    base_year = st.selectbox(
        "기준 계약년도",
        options=years,
        index=years.index(default_year),
        key="base_year",
    )

    month_options = months_by_year.get(base_year, list(range(12, 0, -1)))
    default_month = today.month if base_year == today.year and today.month in month_options else month_options[0]
    base_month = st.selectbox(
        "기준 계약월",
        options=month_options,
        index=month_options.index(default_month),
        key="base_month",
    )

    기준_계약년월 = base_year * 100 + base_month
    end_ym = f"{기준_계약년월:06d}"

    # ✅ 2) 최근기간: 선택형
    recent_options = {
        "최근 3개월": 3,
        "최근 6개월": 6,
        "최근 12개월": 12,
        "최근 24개월": 24,
        "최근 36개월": 36,
        "최근 60개월": 60,
    }
    최근기간_라벨 = st.selectbox(
        "최근기간",
        options=list(recent_options.keys()),
        index=1,  # 기본: 최근 6개월
        key="recent_window",
    )
    months_back = int(recent_options[최근기간_라벨])

    st.divider()
    st.subheader("법정동코드 자동 추적")
    auto_track = st.toggle("지도 클릭 시 법정동코드 자동 입력", value=True, key="auto_track")
    if auto_track and not VWORLD_KEY:
        st.warning("자동추적용 API 키(VWORLD_KEY)가 Secrets에 없습니다. 수동 입력은 가능합니다.")


# 지도/좌표 상태값
st.session_state.setdefault("lat", DEFAULT_CENTER[0])
st.session_state.setdefault("lon", DEFAULT_CENTER[1])
st.session_state.setdefault("lawd10", "")
st.session_state.setdefault("last_rg_latlon", None)  # 역지오코딩 호출 중복 방지

# 지도 표시
m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
folium.Marker([st.session_state.lat, st.session_state.lon], tooltip="대상지").add_to(m)
out = st_folium(m, height=420, use_container_width=True)

# 클릭 좌표 반영
if isinstance(out, dict) and out.get("last_clicked"):
    st.session_state.lat = out["last_clicked"]["lat"]
    st.session_state.lon = out["last_clicked"]["lng"]

st.write("핀 좌표:", st.session_state.lat, st.session_state.lon)

# ✅ 자동추적: 좌표가 바뀌었을 때만 역지오코딩 수행
if st.session_state.get("auto_track", True) and VWORLD_KEY:
    latlon_key = (round(st.session_state.lat, 6), round(st.session_state.lon, 6))
    if st.session_state.last_rg_latlon != latlon_key:
        try:
            code10 = vworld_reverse_geocode(st.session_state.lat, st.session_state.lon)
            if code10:
                st.session_state.lawd10 = code10
        except Exception as e:
            st.warning(f"법정동코드 자동추적 실패(수동 입력 가능): {e}")
        finally:
            st.session_state.last_rg_latlon = latlon_key

# 입력 UI (자동으로 채워지되, 사용자가 수정 가능)
st.subheader("법정동코드(10자리)")
st.session_state.lawd10 = st.text_input("법정동코드 입력", value=st.session_state.lawd10)


def fetch_rtms(url: str, lawd5: str, ym: str) -> pd.DataFrame:
    params = {
        "serviceKey": SERVICE_KEY,
        "LAWD_CD": lawd5,
        "DEAL_YMD": ym,
        "numOfRows": 1000,
        "pageNo": 1,
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.text)

    rows = []
    for it in root.findall(".//item"):
        rows.append({c.tag: c.text for c in list(it)})

    return pd.DataFrame(rows)


if st.button("실거래 조회"):
    if not SERVICE_KEY:
        st.error("SERVICE_KEY(실거래 API)가 없습니다. Streamlit Secrets에 등록해주세요.")
    elif not st.session_state.lawd10 or len(str(st.session_state.lawd10).strip()) < 5:
        st.error("법정동코드를 입력하세요. (최소 5자리 필요)")
    else:
        lawd5 = str(st.session_state.lawd10).strip()[:5]
        dfs = []

        y, m0 = int(end_ym[:4]), int(end_ym[4:])
        for i in range(months_back):
            mm = m0 - i
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
            st.dataframe(merged.head(200), use_container_width=True)

st.caption("안정형 베이스 (년/월 분리 선택형 + 최근기간 선택형 + 법정동코드 자동추적[VWorld])")
