# -*- coding: utf-8 -*-
import os, re, json, datetime as dt, time
import pandas as pd, requests, streamlit as st, folium
from streamlit_folium import st_folium
import xml.etree.ElementTree as ET

# (선택) requests 재시도 설정
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:
    HTTPAdapter = None
    Retry = None

st.set_page_config(page_title="분양가 산정 Tool (안정형)", layout="wide")
st.title("분양가 산정 Tool – 안정형")

DEFAULT_CENTER = (37.5665, 126.9780)

APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
OFFI_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTradeDev/getRTMSDataSvcOffiTradeDev"

SERVICE_KEY = st.secrets.get("SERVICE_KEY", os.environ.get("SERVICE_KEY", "")).strip()
VWORLD_KEY = st.secrets.get("VWORLD_KEY", os.environ.get("VWORLD_KEY", "")).strip()

# --- HTTP Session (재시도 포함) ---
_session = requests.Session()
if HTTPAdapter and Retry:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    _session.mount("https://", adapter)
    _session.mount("http://", adapter)


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


def mask_secret(text: str) -> str:
    if not text:
        return text
    if VWORLD_KEY:
        text = text.replace(VWORLD_KEY, "***VWORLD_KEY***")
    if SERVICE_KEY:
        # 공공데이터포털 키는 길고 urlencoded로 섞일 수 있어 완전 치환은 어렵지만, 최소한 원문은 숨김
        text = text.replace(SERVICE_KEY, "***SERVICE_KEY***")
    return text


def vworld_reverse_geocode(lat: float, lon: float):
    """VWorld 역지오코딩 → 법정동코드(10자리) 반환. (502 등 서버 오류 대비: 재시도 + 안전 파싱)"""
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
        "type": "BOTH",  # PARCEL보다 안정적
        "key": VWORLD_KEY,
    }

    # requests Retry가 있더라도, 간헐 502는 한 번 더 텀을 두고 시도하면 잘 풀리는 경우가 많아 1회 추가
    for attempt in range(2):
        r = _session.get(url, params=params, timeout=12)
        # 200이 아니면 상태코드만 보고 None 처리(경고는 상위에서)
        if r.status_code != 200:
            # 502/503 등이면 잠깐 쉬고 한 번 더
            if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(0.8)
                continue
            raise requests.HTTPError(f"VWorld HTTP {r.status_code}")

        data = r.json()

        # 1) 구조화 필드 우선
        try:
            for it in data.get("response", {}).get("result", []):
                if "code" in it and "bjdCd" in it["code"]:
                    return it["code"]["bjdCd"]
        except Exception:
            pass

        # 2) 전체에서 10자리 패턴 탐색
        code = extract_10digit_code(data)
        if code:
            return code

        return None

    return None


def nominatim_reverse_geocode(lat: float, lon: float):
    """(백업) OSM Nominatim 역지오코딩: 법정동코드 자체는 안 주지만, 주소 표시용으로만 사용."""
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"format": "jsonv2", "lat": str(lat), "lon": str(lon), "zoom": 18, "addressdetails": 1}
    headers = {"User-Agent": "price-tool/1.0 (streamlit)"}
    r = _session.get(url, params=params, headers=headers, timeout=12)
    if r.status_code != 200:
        return None
    data = r.json()
    return data.get("display_name")


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

    st.divider()
    auto_track = st.toggle("법정동코드 자동 추적", value=True)
    show_debug = st.toggle("디버그(오류 상세) 보기", value=False)

    if auto_track and not VWORLD_KEY:
        st.warning("VWORLD_KEY가 Secrets에 없습니다. (자동추적 OFF 또는 키 등록 필요)")


st.session_state.setdefault("lat", DEFAULT_CENTER[0])
st.session_state.setdefault("lon", DEFAULT_CENTER[1])
st.session_state.setdefault("lawd10", "")
st.session_state.setdefault("last_latlon", None)
st.session_state.setdefault("last_addr_label", "")

m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
folium.Marker([st.session_state.lat, st.session_state.lon]).add_to(m)
out = st_folium(m, height=420, use_container_width=True)

if isinstance(out, dict) and out.get("last_clicked"):
    st.session_state.lat = out["last_clicked"]["lat"]
    st.session_state.lon = out["last_clicked"]["lng"]

st.write("핀 좌표:", st.session_state.lat, st.session_state.lon)

# --- 자동추적 ---
if auto_track and VWORLD_KEY:
    key = (round(st.session_state.lat, 6), round(st.session_state.lon, 6))
    if st.session_state.last_latlon != key:
        try:
            code = vworld_reverse_geocode(st.session_state.lat, st.session_state.lon)
            if code:
                st.session_state.lawd10 = code
                st.session_state.last_addr_label = ""
            else:
                # 법정동코드를 못 받으면 주소 라벨만이라도 백업(사용자에게 상황 설명)
                st.session_state.last_addr_label = nominatim_reverse_geocode(st.session_state.lat, st.session_state.lon) or ""
                st.warning("법정동코드를 자동으로 가져오지 못했습니다. (수동 입력 가능)")
        except Exception as e:
            msg = mask_secret(str(e))
            st.warning("법정동코드 자동추적 실패(수동 입력 가능).")
            if show_debug:
                st.code(msg)
        st.session_state.last_latlon = key

if st.session_state.get("last_addr_label"):
    st.caption(f"참고 주소(백업): {st.session_state.last_addr_label}")

st.subheader("법정동코드(10자리)")
st.session_state.lawd10 = st.text_input("법정동코드 입력", value=st.session_state.lawd10)


def fetch_rtms(url: str, lawd5: str, ym: str) -> pd.DataFrame:
    params = {"serviceKey": SERVICE_KEY, "LAWD_CD": lawd5, "DEAL_YMD": ym, "numOfRows": 1000, "pageNo": 1}
    r = _session.get(url, params=params, timeout=20)
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

st.caption("안정형 v5 – VWorld 502/서버오류 재시도 + 키 마스킹 + 주소 백업표시")
