# -*- coding: utf-8 -*-
import os, re, json, datetime as dt, time
import pandas as pd, requests, streamlit as st, folium
from streamlit_folium import st_folium
import xml.etree.ElementTree as ET

try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:
    HTTPAdapter = None
    Retry = None

st.set_page_config(page_title="분양가 산정 Tool (안정형 v14.12-A1)", layout="wide")
st.title("분양가 산정 Tool – 안정형 v14.12-A1")

DEFAULT_CENTER = (37.5665, 126.9780)

APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
OFFI_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTradeDev/getRTMSDataSvcOffiTradeDev"

SERVICE_KEY = st.secrets.get("SERVICE_KEY", os.environ.get("SERVICE_KEY", "")).strip()
VWORLD_KEY = st.secrets.get("VWORLD_KEY", os.environ.get("VWORLD_KEY", "")).strip()
KAKAO_KEY = (
    st.secrets.get("KAKAO_REST_API_KEY", "")
    or st.secrets.get("KAKAO_KEY", "")
    or os.environ.get("KAKAO_REST_API_KEY", "")
    or os.environ.get("KAKAO_KEY", "")
).strip()

# --- HTTP Session (재시도 포함) ---
_session = requests.Session()
if HTTPAdapter and Retry:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    _session.mount("https://", adapter)
    _session.mount("http://", adapter)

DEFAULT_HEADERS = {
    # 일부 공공/상용 API는 User-Agent 없으면 차단/불안정해지는 경우가 있어 기본 지정
    "User-Agent": "pricing-tool/1.0 (streamlit)",
    "Accept": "application/json",
}


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


def mask_secret(text: str) -> str:
    if not text:
        return text
    for k in (VWORLD_KEY, SERVICE_KEY, KAKAO_KEY):
        if k:
            text = text.replace(k, "***KEY***")
    return text


def _extract_bjd_from_vworld_json(data):
    # 구조화: response.result[].code.bjdCd
    try:
        for it in data.get("response", {}).get("result", []):
            code = (it.get("code") or {})
            if "bjdCd" in code and isinstance(code["bjdCd"], str) and re.fullmatch(r"\d{10}", code["bjdCd"]):
                return code["bjdCd"]
            # PNU(19자리) → 앞 10자리
            for key in ("pnu", "PNU"):
                if key in it and isinstance(it[key], str) and re.fullmatch(r"\d{19}", it[key]):
                    return it[key][:10]
    except Exception:
        pass

    # 전체에서 19/10자리 탐색
    try:
        s = json.dumps(data, ensure_ascii=False)
    except Exception:
        s = str(data)
    m19 = re.search(r"\b\d{19}\b", s)
    if m19:
        return m19.group(0)[:10]
    m10 = re.search(r"\b\d{10}\b", s)
    if m10:
        return m10.group(0)
    return None


def vworld_reverse_geocode(lat: float, lon: float):
    if not VWORLD_KEY:
        return None, "VWORLD_KEY 없음"
    url = "https://api.vworld.kr/req/address"
    base_params = {
        "service": "address",
        "request": "getAddress",
        "version": "2.0",
        "crs": "epsg:4326",
        "point": f"{lon},{lat}",
        "format": "json",
        "key": VWORLD_KEY,
    }

    last_hint = ""
    for tp in ("PARCEL", "BOTH", "ROAD"):
        params = dict(base_params)
        params["type"] = tp
        for attempt in range(2):
            try:
                r = _session.get(url, params=params, headers=DEFAULT_HEADERS, timeout=12)
            except Exception as e:
                # 여기서 터지면 Streamlit이 빨간 화면으로 죽을 수 있으니 반드시 잡아서 힌트로 반환
                return None, f"VWorld 연결 실패({type(e).__name__}): {mask_secret(repr(e))}"

            if r.status_code != 200:
                last_hint = f"type={tp}, HTTP={r.status_code}"
                if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(0.9)
                    continue
                break

            try:
                data = r.json()
            except Exception as e:
                last_hint = f"type={tp}, JSON 파싱 실패: {type(e).__name__}"
                break

            vw_status = (data.get("response") or {}).get("status")
            vw_msg = (data.get("response") or {}).get("message")
            last_hint = f"type={tp}, HTTP=200, vworld_status={vw_status}, msg={vw_msg}"

            if vw_status and str(vw_status).upper() != "OK":
                break

            code = _extract_bjd_from_vworld_json(data)
            if code:
                return code, last_hint

            if attempt == 0:
                time.sleep(0.4)

    return None, last_hint or "VWorld 응답에서 코드 추출 실패"


def kakao_reverse_geocode(lat: float, lon: float):
    """카카오 로컬 API: coord2regioncode → 법정동코드(10자리) (region_type=B)"""
    if not KAKAO_KEY:
        return None, "KAKAO_REST_API_KEY 없음"

    url = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
    params = {"x": str(lon), "y": str(lat)}
    headers = dict(DEFAULT_HEADERS)
    headers["Authorization"] = f"KakaoAK {KAKAO_KEY}"

    try:
        r = _session.get(url, params=params, headers=headers, timeout=12)
    except Exception as e:
        return None, f"Kakao 연결 실패({type(e).__name__}): {mask_secret(repr(e))}"

    if r.status_code != 200:
        return None, f"Kakao HTTP={r.status_code}"

    try:
        data = r.json()
    except Exception as e:
        return None, f"Kakao JSON 파싱 실패: {type(e).__name__}"

    docs = data.get("documents", []) if isinstance(data, dict) else []
    # region_type: B(법정동) / H(행정동)
    for d in docs:
        if d.get("region_type") == "B":
            code = d.get("code")
            if isinstance(code, str) and re.fullmatch(r"\d{10}", code):
                return code, "Kakao OK(region_type=B)"
    # 백업: 첫 번째 코드라도 사용 (드물게 region_type이 다르게 올 수도 있어서)
    if docs:
        code = docs[0].get("code")
        if isinstance(code, str) and re.fullmatch(r"\d{10}", code):
            return code, "Kakao OK(fallback)"
    return None, "Kakao 응답에서 코드 없음"


def resolve_bjd_code(lat: float, lon: float, provider: str):
    """provider: auto | vworld | kakao"""
    if provider == "vworld":
        return vworld_reverse_geocode(lat, lon)
    if provider == "kakao":
        return kakao_reverse_geocode(lat, lon)

    # auto
    code, hint = vworld_reverse_geocode(lat, lon)
    if code:
        return code, f"[AUTO] {hint}"
    code2, hint2 = kakao_reverse_geocode(lat, lon)
    if code2:
        return code2, f"[AUTO] {hint2}"
    return None, f"[AUTO] 실패: VWorld={hint} / Kakao={hint2}"


# --- Sidebar UI ---
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
    provider = st.selectbox("자동추적 제공자", ["auto", "vworld", "kakao"], index=0)
    show_debug = st.toggle("디버그(오류 상세 보기)", value=False)

    st.caption("※ Streamlit Cloud에서 VWorld가 연결 에러가 나는 경우가 있어, Kakao 대안을 추가했습니다.")

    test_btn = st.button("연결 테스트(서울시청)")

# --- 상태값 ---
st.session_state.setdefault("lat", DEFAULT_CENTER[0])
st.session_state.setdefault("lon", DEFAULT_CENTER[1])
st.session_state.setdefault("lawd10", "")
st.session_state.setdefault("last_latlon", None)
st.session_state.setdefault("map_click_key", None)
st.session_state.setdefault("last_hint", "")

# --- 연결 테스트 ---
if test_btn:
    t_lat, t_lon = 37.5665, 126.9780
    code, hint = resolve_bjd_code(t_lat, t_lon, provider=provider)
    if code:
        st.success(f"테스트 성공: {code}  /  {mask_secret(hint)}")
    else:
        st.error(f"테스트 실패: {mask_secret(hint)}")
        if provider in ("auto", "vworld") and not VWORLD_KEY:
            st.info("VWORLD_KEY가 Secrets에 없어서 VWorld 테스트를 할 수 없습니다.")
        if provider in ("auto", "kakao") and not KAKAO_KEY:
            st.info("KAKAO_REST_API_KEY가 Secrets에 없어서 Kakao 테스트를 할 수 없습니다.")

# --- 지도 ---
m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=13)
folium.Marker([st.session_state.lat, st.session_state.lon]).add_to(m)
out = st_folium(m, height=420, use_container_width=True)

if isinstance(out, dict) and out.get("last_clicked"):
    new_lat = out["last_clicked"]["lat"]
    new_lon = out["last_clicked"]["lng"]
    new_key = (round(new_lat, 7), round(new_lon, 7))
    # 클릭 직후에도 핀이 바로 이동하도록, 좌표가 바뀐 경우에만 rerun 합니다(무한루프 방지)
    if st.session_state.get("map_click_key") != new_key:
        st.session_state.lat = new_lat
        st.session_state.lon = new_lon
        st.session_state.map_click_key = new_key
        st.rerun()

st.write("핀 좌표:", st.session_state.lat, st.session_state.lon)

# --- 자동추적 ---
if auto_track:
    key = (round(st.session_state.lat, 6), round(st.session_state.lon, 6))
    if st.session_state.last_latlon != key:
        code, hint = resolve_bjd_code(st.session_state.lat, st.session_state.lon, provider=provider)
        st.session_state.last_hint = hint or ""
        if code:
            st.session_state.lawd10 = code
        else:
            st.warning("법정동코드 자동추적 실패(수동 입력 가능).")
        st.session_state.last_latlon = key

if st.session_state.get("last_hint"):
    st.caption(f"자동추적 상태: {mask_secret(st.session_state.last_hint)}")

st.subheader("법정동코드(10자리)")
st.session_state.lawd10 = st.text_input("법정동코드 입력", value=st.session_state.lawd10)


# --- 실거래 조회 ---
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

st.caption("안정형 v8 – VWorld 연결 실패(Cloud) 대비: Kakao 대체 제공자 + 자동(AUTO) 폴백 + 앱 크래시 방지")
