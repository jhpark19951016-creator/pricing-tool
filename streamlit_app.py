# -*- coding: utf-8 -*-
import os, re, json, datetime as dt
import pandas as pd, requests, streamlit as st, folium
from streamlit_folium import st_folium
import xml.etree.ElementTree as ET

st.set_page_config(page_title="분양가 산정 Tool (안정형)", layout="wide")
st.title("분양가 산정 Tool – 안정형")

DEFAULT_CENTER = (37.5665, 126.9780)

APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
OFFI_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTradeDev/getRTMSDataSvcOffiTradeDev"

SERVICE_KEY = st.secrets.get("SERVICE_KEY", os.environ.get("SERVICE_KEY", "")).strip()
VWORLD_KEY = st.secrets.get("VWORLD_KEY", os.environ.get("VWORLD_KEY", "")).strip()


def make_year_month_options(months: int = 72):
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
    months_by_year = {yy: sorted({mm for y2, mm in pairs if y2 == yy}, reverse=True) for yy in years}
    return years, months_by_year


def extract_10digit_code(obj):
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    m = re.search(r"\b\d{10}\b", s)
    return m.group(0) if m else None


def vworld_reverse_geocode(lat: float, lon: float):
    if not VWORLD_KEY:
        return None
    url = "https://api.vworld.kr/req/address"
    params = {
        "service": "address",
        "request": "getAddress",
        "version": "2.0",
        "crs": "epsg:4326",
        "point": f"{lon},{lat}",
        "format": "json",
        "type": "BOTH",  # 502 회피
        "key": VWORLD_KEY,
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    try:
        for it in data.get("response", {}).get("result", []):
            if "code" in it and "bjdCd" in it["code"]:
                return it["code"]["bjdCd"]
    except Exception:
        pass
    return extract_10digit_code(data)


with st.sidebar:
    st.header("설정")
    product = st.selectbox("상품", ["아파트", "오피스텔", "아파트+오피스텔"], index=2)

    years, months_by_year = make_year_month_options()
    base_year = st.selectbox("기준 계약년도", years, index=0)
    base_month = st.selectbox("기준 계약월", months_by_year[base_year], index=0)

    기준_계약년월 = base_year * 100 + base_month
    end_ym = f"{기준_계약년월:06d}"

    recent_options = {"최근 3개월": 3, "최근 6개월": 6, "최근 12개월": 12, "최근 24개월": 24}
    recent_label = st.selectbox("최근기간", list(recent_options.keys()), index=1)
    months_back = recent_options[recent_label]

    auto_track = st.toggle("법정동코드 자동 추적", value=True)


st.session_state.setdefault("lat", DEFAULT_CENTER[0])
st.session_state.setdefault("lon", DEFAULT_CENTER[1])
st.session_state.setdefault("lawd10", "")
st.session_state.setdefault("last_latlon", None)

m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
folium.Marker([st.session_state.lat, st.session_state.lon]).add_to(m)
out = st_folium(m, height=420, use_container_width=True)

if isinstance(out, dict) and out.get("last_clicked"):
    st.session_state.lat = out["last_clicked"]["lat"]
    st.session_state.lon = out["last_clicked"]["lng"]

st.write("핀 좌표:", st.session_state.lat, st.session_state.lon)

if auto_track and VWORLD_KEY:
    key = (round(st.session_state.lat, 6), round(st.session_state.lon, 6))
    if st.session_state.last_latlon != key:
        try:
            code = vworld_reverse_geocode(st.session_state.lat, st.session_state.lon)
            if code:
                st.session_state.lawd10 = code
        except Exception as e:
            st.warning(f"법정동코드 자동추적 실패 (수동 입력 가능): {e}")
        st.session_state.last_latlon = key

st.subheader("법정동코드(10자리)")
st.session_state.lawd10 = st.text_input("법정동코드 입력", value=st.session_state.lawd10)


def fetch_rtms(url: str, lawd5: str, ym: str) -> pd.DataFrame:
    params = {"serviceKey": SERVICE_KEY, "LAWD_CD": lawd5, "DEAL_YMD": ym, "numOfRows": 1000, "pageNo": 1}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    return pd.DataFrame([{c.tag: c.text for c in list(it)} for it in root.findall(".//item")])


if st.button("실거래 조회"):
    if not SERVICE_KEY:
        st.error("SERVICE_KEY가 없습니다.")
    elif not st.session_state.lawd10:
        st.error("법정동코드를 입력하세요.")
    else:
        lawd5 = st.session_state.lawd10[:5]
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

st.caption("안정형 v4 – VWorld 502 대응(BOTH) + 법정동코드 자동추적")
