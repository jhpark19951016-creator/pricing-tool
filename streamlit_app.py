
# -*- coding: utf-8 -*-
import os
import hashlib
import urllib.parse, datetime as dt
import pandas as pd
import requests
import streamlit as st
import folium
from streamlit_folium import st_folium
import xml.etree.ElementTree as ET

st.set_page_config(page_title="분양가 산정 Tool (풀세트 안정형)", layout="wide")
st.title("분양가 산정 Tool – 풀세트(안정형)")

DEFAULT_CENTER = (37.5665, 126.9780)
APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
OFFI_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade"

SERVICE_KEY = st.secrets.get("SERVICE_KEY", os.environ.get("SERVICE_KEY", "")).strip()

KAKAO_REST_API_KEY = st.secrets.get("KAKAO_REST_API_KEY", os.environ.get("KAKAO_REST_API_KEY", "")).strip()

def _looks_urlencoded(s: str) -> bool:
    return "%" in s

def _service_key_variants(raw: str):
    """Return (decoded_key, encoded_key_or_empty, is_encoded_input)."""
    raw = (raw or "").strip()
    if not raw:
        return "", "", False
    if _looks_urlencoded(raw):
        # raw is likely "인코딩 키". Keep it as-is for direct URL usage.
        decoded = urllib.parse.unquote(raw)
        return decoded, raw, True
    # raw is likely "디코딩 키"
    return raw, "", False

def _parse_rtms_error(xml_text: str):
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    # 공공데이터 포털 표준: header/resultCode, header/resultMsg
    code = root.findtext(".//header/resultCode")
    msg  = root.findtext(".//header/resultMsg")
    if code and code != "00":
        return f"{code} / {msg or ''}".strip()
    # 일부 응답은 정상이어도 header가 존재
    return None

@st.cache_data(show_spinner=False, ttl=60*10)
def _kakao_coord2region(lat: float, lon: float, kakao_rest_key: str):
    """좌표 -> 행정구역명(시/구/동)"""
    if not kakao_rest_key:
        return None
    url = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
    headers = {"Authorization": f"KakaoAK {kakao_rest_key}"}
    params = {"x": str(lon), "y": str(lat)}
    r = requests.get(url, headers=headers, params=params, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    docs = data.get("documents") or []
    # region_type 'B' (법정동) 우선
    docs_sorted = sorted(docs, key=lambda d: 0 if d.get("region_type") == "B" else 1)
    if not docs_sorted:
        return None
    d = docs_sorted[0]
    r1 = d.get("region_1depth_name") or ""
    r2 = d.get("region_2depth_name") or ""
    r3 = d.get("region_3depth_name") or ""
    return " ".join([p for p in [r1, r2, r3] if p]).strip()

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
    """실거래 API 호출 (오류/키 인코딩 이슈 포함 방어)"""
    decoded_key, encoded_key, is_encoded = _service_key_variants(SERVICE_KEY)
    if not decoded_key and not encoded_key:
        raise RuntimeError("SERVICE_KEY가 비어있습니다. Streamlit secrets에 SERVICE_KEY를 등록하세요.")

    # 공공데이터포털 RTMS는 serviceKey가 '인코딩키'인 경우, params로 넣으면 %가 %25로 재인코딩되어 403이 날 수 있습니다.
    # 그래서 인코딩키 입력이면 serviceKey만 URL에 직접 붙이고, 나머지 파라미터는 urlencode로 붙입니다.
    base_params = {"LAWD_CD": str(lawd5), "DEAL_YMD": str(ym)}
    timeout = 20
    headers = {"User-Agent": "Mozilla/5.0"}

    def _do_request_with_decoded():
        params = {"serviceKey": decoded_key, **base_params}
        return requests.get(url, params=params, headers=headers, timeout=timeout)

    def _do_request_with_encoded():
        q = urllib.parse.urlencode(base_params, doseq=True)
        full = f"{url}?serviceKey={encoded_key}&{q}"
        return requests.get(full, headers=headers, timeout=timeout)

    # 1) decoded(디코딩키) 방식 우선
    r = _do_request_with_decoded() if decoded_key else _do_request_with_encoded()

    # 403이면 (인코딩키 입력 + 이중인코딩) 가능성이 높아서 인코딩키 방식으로 재시도
    if r.status_code == 403 and is_encoded and encoded_key:
        r = _do_request_with_encoded()

    # HTTP 자체 오류일 때도 XML에 에러코드가 들어오는 경우가 있어 먼저 파싱
    err = _parse_rtms_error(r.text)
    if err:
        raise RuntimeError(f"실거래 API 오류: {err}")

    if r.status_code != 200:
        # 여기서는 response 일부만 보여주고, 실제 상세는 로그에서 확인
        raise RuntimeError(f"실거래 API HTTP 오류: {r.status_code}")

    root = ET.fromstring(r.text)
    items = root.findall('.//item')
    rows = []
    for it in items:
        row = {c.tag: (c.text or '') for c in it}
        rows.append(row)

    return pd.DataFrame(rows)

@st.cache_data(show_spinner=False, ttl=60*10)
def cached_fetch_rtms(url: str, lawd5: str, ym: str, key_fingerprint: str):
    return fetch_rtms(url, lawd5, ym)


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
                dfs.append(cached_fetch_rtms(APT_URL, lawd5, ym, hashlib.sha256(SERVICE_KEY.encode('utf-8')).hexdigest()[:12]))
            if product in ("오피스텔", "아파트+오피스텔"):
                dfs.append(cached_fetch_rtms(OFFI_URL, lawd5, ym, hashlib.sha256(SERVICE_KEY.encode('utf-8')).hexdigest()[:12]))

        merged = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        if merged.empty:
            st.warning("조회된 실거래가가 없습니다.")
        else:
            st.success(f"총 {len(merged):,}건")
            st.dataframe(merged.head(300), use_container_width=True)

st.caption("풀세트 안정형 – 최종 재업로드")
