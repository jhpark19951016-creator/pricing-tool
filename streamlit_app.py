# -*- coding: utf-8 -*-
import os
import time, re, json, datetime as dt, time
from urllib.parse import unquote
import pandas as pd, requests, streamlit as st, folium
import streamlit.components.v1 as components
from streamlit_folium import st_folium
import xml.etree.ElementTree as ET

try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:
    HTTPAdapter = None
    Retry = None

st.set_page_config(page_title="분양가 산정 Tool (안정형)", layout="wide")
st.title("분양가 산정 Tool – 안정형")

DEFAULT_CENTER = (37.5665, 126.9780)

# ✅ v11: Dev 엔드포인트 -> 일반 엔드포인트(403 회피용)
APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
OFFI_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade"

SERVICE_KEY_RAW = st.secrets.get("SERVICE_KEY", os.environ.get("SERVICE_KEY", "")).strip()
VWORLD_KEY = st.secrets.get("VWORLD_KEY", os.environ.get("VWORLD_KEY", "")).strip()
KAKAO_KEY = (
    st.secrets.get("KAKAO_REST_API_KEY", "")
    or st.secrets.get("KAKAO_KEY", "")
    or os.environ.get("KAKAO_REST_API_KEY", "")
    or os.environ.get("KAKAO_KEY", "")
).strip()

KAKAO_JS_KEY = (
    st.secrets.get("KAKAO_JAVASCRIPT_KEY", "")
    or st.secrets.get("KAKAO_JS_KEY", "")
    or os.environ.get("KAKAO_JAVASCRIPT_KEY", "")
    or os.environ.get("KAKAO_JS_KEY", "")
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

DEFAULT_HEADERS = {"User-Agent": "pricing-tool/1.1 (streamlit)", "Accept": "application/json"}


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
    for k in (VWORLD_KEY, SERVICE_KEY_RAW, KAKAO_KEY):
        if k:
            text = text.replace(k, "***KEY***")
    return text


def normalize_service_key(k: str) -> str:
    """serviceKey 이중 인코딩 방지: '%' 포함 시 unquote로 1회 디코딩."""
    if not k:
        return ""
    if "%" in k:
        try:
            return unquote(k)
        except Exception:
            return k
    return k


SERVICE_KEY = normalize_service_key(SERVICE_KEY_RAW)


def _extract_bjd_from_vworld_json(data):
    try:
        for it in data.get("response", {}).get("result", []):
            code = (it.get("code") or {})
            if "bjdCd" in code and isinstance(code["bjdCd"], str) and re.fullmatch(r"\d{10}", code["bjdCd"]):
                return code["bjdCd"]
            for key in ("pnu", "PNU"):
                if key in it and isinstance(it[key], str) and re.fullmatch(r"\d{19}", it[key]):
                    return it[key][:10]
    except Exception:
        pass

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


def _extract_label_from_vworld_json(data):
    try:
        rs = data.get("response", {}).get("result", [])
        if rs and isinstance(rs, list):
            txt = rs[0].get("text")
            if isinstance(txt, str) and txt.strip():
                return txt.strip()
    except Exception:
        pass
    return ""


def vworld_reverse_geocode(lat: float, lon: float):
    if not VWORLD_KEY:
        return None, "VWORLD_KEY 없음", ""
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
    last_label = ""
    for tp in ("PARCEL", "BOTH", "ROAD"):
        params = dict(base_params)
        params["type"] = tp
        for attempt in range(2):
            try:
                r = _session.get(url, params=params, headers=DEFAULT_HEADERS, timeout=12)
            except Exception as e:
                return None, f"VWorld 연결 실패({type(e).__name__}): {mask_secret(repr(e))}", ""
            if r.status_code != 200:
                last_hint = f"type={tp}, HTTP={r.status_code}"
                if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(0.9)
                    continue
                break
            try:
                data = r.json()
            except Exception:
                last_hint = f"type={tp}, JSON 파싱 실패"
                break

            vw_status = (data.get("response") or {}).get("status")
            vw_msg = (data.get("response") or {}).get("message")
            last_hint = f"type={tp}, HTTP=200, vworld_status={vw_status}, msg={vw_msg}"
            last_label = _extract_label_from_vworld_json(data) or last_label

            if vw_status and str(vw_status).upper() != "OK":
                break

            code = _extract_bjd_from_vworld_json(data)
            if code:
                return code, last_hint, last_label

            if attempt == 0:
                time.sleep(0.4)

    return None, last_hint or "VWorld 응답에서 코드 추출 실패", last_label


@st.cache_data(show_spinner=False, ttl=60*60*24)
def kakao_reverse_geocode(lat: float, lon: float):
    if not KAKAO_KEY:
        return None, "KAKAO_REST_API_KEY 없음", ""
    url = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
    params = {"x": str(lon), "y": str(lat)}
    headers = dict(DEFAULT_HEADERS)
    headers["Authorization"] = f"KakaoAK {KAKAO_KEY}"

    try:
        r = _session.get(url, params=params, headers=headers, timeout=12)
    except Exception as e:
        return None, f"Kakao 연결 실패({type(e).__name__}): {mask_secret(repr(e))}", ""

    if r.status_code != 200:
        return None, f"Kakao HTTP={r.status_code}", ""

    try:
        data = r.json()
    except Exception:
        return None, "Kakao JSON 파싱 실패", ""

    docs = data.get("documents", []) if isinstance(data, dict) else []
    picked = None
    for d in docs:
        if d.get("region_type") == "B":
            code = d.get("code")
            if isinstance(code, str) and re.fullmatch(r"\d{10}", code):
                picked = d
                break
    if not picked and docs:
        picked = docs[0]

    if picked:
        code = picked.get("code")
        r1 = (picked.get("region_1depth_name") or "").strip()
        r2 = (picked.get("region_2depth_name") or "").strip()
        r3 = (picked.get("region_3depth_name") or "").strip()
        label = " ".join([x for x in (r1, r2, r3) if x])
        if isinstance(code, str) and re.fullmatch(r"\d{10}", code):
            rt = picked.get("region_type")
            return code, f"Kakao OK(region_type={rt})", label

    return None, "Kakao 응답에서 코드 없음", ""


def resolve_bjd_code(lat: float, lon: float, provider: str):
    if provider == "vworld":
        return vworld_reverse_geocode(lat, lon)
    if provider == "kakao":
        return kakao_reverse_geocode(lat, lon)

    code, hint, label = vworld_reverse_geocode(lat, lon)
    if code:
        return code, f"[AUTO] {hint}", label
    code2, hint2, label2 = kakao_reverse_geocode(lat, lon)
    if code2:
        return code2, f"[AUTO] {hint2}", label2
    return None, f"[AUTO] 실패: VWorld={hint} / Kakao={hint2}", label or label2


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
    map_mode = st.selectbox("지도 모드", ["kakao(타일/권장)", "kakao(완전/JS-SDK·실험)", "folium(OSM/대체)"], index=0)
    show_debug = st.toggle("디버그(오류 상세 보기)", value=False)

    test_btn = st.button("연결 테스트(서울시청)")

    with st.expander("키 상태(진단)", expanded=False):
        st.write("SERVICE_KEY:", "✅" if SERVICE_KEY else "❌")
        st.write("SERVICE_KEY(원문/디코딩):", "디코딩됨" if (SERVICE_KEY_RAW and SERVICE_KEY != SERVICE_KEY_RAW) else "그대로")
        st.write("KAKAO_REST_API_KEY:", "✅" if KAKAO_KEY else "❌")
        st.write("KAKAO_JAVASCRIPT_KEY:", "✅" if KAKAO_JS_KEY else "❌")
        st.write("VWORLD_KEY:", "✅" if VWORLD_KEY else "❌")

# --- 상태값 ---
st.session_state.setdefault("lat", DEFAULT_CENTER[0])
st.session_state.setdefault("lon", DEFAULT_CENTER[1])
st.session_state.setdefault("lawd10", "")
st.session_state.setdefault("last_latlon", None)
st.session_state.setdefault("last_hint", "")
st.session_state.setdefault("last_label", "")

# --- v14: Kakao 지도 클릭 좌표를 query params로 전달(리로드 방식) ---
try:
    qp = st.query_params
    q_lat = qp.get("lat")
    q_lon = qp.get("lon")
    if q_lat and q_lon:
        try:
            st.session_state.lat = float(q_lat)
            st.session_state.lon = float(q_lon)
            st.query_params.clear()
        except Exception:
            pass
except Exception:
    pass


# --- 연결 테스트 ---
if test_btn:
    t_lat, t_lon = 37.5665, 126.9780
    code, hint, label = resolve_bjd_code(t_lat, t_lon, provider=provider)
    if code:
        st.success(f"테스트 성공: {code}  /  {label or '-'}  /  {mask_secret(hint)}")
    else:
        st.error(f"테스트 실패: {mask_secret(hint)}")


# --- 지도 ---
# v14.4 (A/B 구조)
# A = kakao(타일/권장) : 안정적으로 Folium 엔진 + Kakao 타일 사용 (기본값/운영)
# B = kakao(완전/JS-SDK·실험) : Kakao JS SDK 렌더링 (실험) - 실패해도 A로 자동 fallback 표시
# folium(OSM/대체) : 완전 백업 (OSM 타일)

if "map_mode" not in locals():
    map_mode = "kakao(타일/권장)"

def _apply_query_params_to_session():
    """
    Kakao JS-SDK(완전) 모드에서 지도 클릭 시, 부모 페이지 URL에 lat/lon을 심어 리로드합니다.
    여기서 query params를 읽어 세션 좌표에 반영합니다.
    """
    try:
        # Streamlit 1.30+ : st.query_params
        qp = st.query_params
        lat_q = qp.get("lat", None)
        lon_q = qp.get("lon", None)
        if lat_q is None or lon_q is None:
            return False
        lat_f = float(lat_q)
        lon_f = float(lon_q)
        # 유효 범위 체크(대충)
        if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
            return False
        st.session_state.lat = lat_f
        st.session_state.lon = lon_f
        return True
    except Exception:
        # 구버전 fallback
        try:
            qp = st.experimental_get_query_params()
            lat_q = qp.get("lat", [None])[0]
            lon_q = qp.get("lon", [None])[0]
            if lat_q is None or lon_q is None:
                return False
            lat_f = float(lat_q)
            lon_f = float(lon_q)
            if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
                return False
            st.session_state.lat = lat_f
            st.session_state.lon = lon_f
            return True
        except Exception:
            return False

def render_map_folium(
    center_lat: float,
    center_lon: float,
    zoom: int = 13,
    tile_mode: str = "kakao",
):
    """Folium(Leaflet) 지도 렌더링.
    - tile_mode="kakao": 카카오 타일을 **시도**하되, 타일 정책/차단/버전변경 등으로 실패할 수 있어
      OSM(OpenStreetMap) 레이어를 함께 제공하여 '지도 빈 화면'을 방지합니다.
    """

    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, control_scale=True)

    # 1) 안정적인 기본(항상 표시) 레이어
    folium.TileLayer(
        tiles="OpenStreetMap",
        name="OSM(대체/안정)",
        control=True,
        show=(tile_mode != "kakao"),
    ).add_to(m)

    # 2) 카카오 타일(실패 가능) 레이어
    if tile_mode == "kakao":
        # NOTE: 카카오 타일 URL은 비공식/변동 가능성이 있어, 동작 보장을 위해 OSM을 같이 둡니다.
        kakao_tile = (
            "https://map.daumcdn.net/map_2d/2212qpe/L{z}/{y}/{x}.png"
        )
        folium.TileLayer(
            tiles=kakao_tile,
            attr="Kakao",
            name="Kakao(타일/실험)",
            control=True,
            show=True,
            max_zoom=19,
        ).add_to(m)

    folium.LayerControl(collapsed=True).add_to(m)

    # 핀/마커
    folium.Marker([center_lat, center_lon], tooltip="선택 위치").add_to(m)

    # Leaflet 클릭 이벤트 -> Streamlit 반환
    return st_folium(
        m,
        width=None,
        height=470,
        returned_objects=["last_clicked", "bounds", "center", "zoom"],
        key=f"folium_map_{tile_mode}",
    )

def render_map_kakao_sdk_with_fallback(
    center_lat: float,
    center_lon: float,
    zoom: int = 3,
    height: int = 470,
):
    """카카오 JS SDK 지도(실험) + Folium 대체지도(항상 표시).
    JS SDK는 '도메인 등록/광고차단/사내망 보안' 이슈로 로드가 안 되는 경우가 많아서,
    아래에 Folium 지도를 항상 같이 보여줍니다.
    """

    js_key = (KAKAO_JAVASCRIPT_KEY or "").strip()
    if not js_key:
        st.error("KAKAO_JAVASCRIPT_KEY(카카오 JavaScript 키)가 설정되지 않아 JS-SDK 지도를 표시할 수 없습니다.")
        st.info("Streamlit Cloud → App → Settings → Secrets 에 KAKAO_JAVASCRIPT_KEY 를 추가하세요.")
        return

    kakao_html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  html, body {{ height: 100%; margin: 0; padding: 0; }}
  #wrap {{ position: relative; width: 100%; height: 100%; }}
  #map {{ width: 100%; height: 100%; background: #f4f6f8; }}
  #overlay {{
    position: absolute; top: 10px; left: 10px; right: 10px;
    padding: 10px 12px;
    font: 13px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Noto Sans KR',sans-serif;
    background: rgba(255,255,255,0.92);
    border: 1px solid rgba(0,0,0,0.08);
    border-radius: 10px;
    box-shadow: 0 6px 18px rgba(0,0,0,0.08);
  }}
  #overlay .ok {{ color: #0a7a2f; font-weight: 700; }}
  #overlay .bad {{ color: #b42318; font-weight: 700; }}
  #overlay code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
</style>
</head>
<body>
<div id="wrap">
  <div id="map"></div>
  <div id="overlay">카카오 JS SDK 로딩 중…</div>
</div>

<script>
(function() {{
  var overlay = document.getElementById('overlay');
  var initialized = false;

  function setHtml(html) {{
    overlay.innerHTML = html;
  }}

  function showGuide(reason) {{
    var ref = document.referrer || '(empty)';
    // Kakao 콘솔에 등록해야 하는 도메인은 "프로토콜 + 도메인"만(경로 제외)
    var base = ref.split('/').slice(0, 3).join('/');
    setHtml(
      '<span class="bad">✕ Kakao SDK 초기화 실패</span><br/>' +
      '<b>사유:</b> ' + reason + '<br/><br/>' +
      '<b>1) Kakao Developers → 내 애플리케이션 → 앱 설정 → 플랫폼 → Web</b> 에서<br/>' +
      '<b>JavaScript SDK 도메인</b>에 아래 값을 추가하세요(프로토콜 포함, 경로 제외):<br/>' +
      '<code>' + base + '</code><br/>' +
      '(현재 Referrer: <code>' + ref + '</code>)<br/><br/>' +
      '<b>2) 광고차단/보안SW/사내망 장비</b>가 <code>dapi.kakao.com</code>을 차단하면 로드가 안 됩니다.'
    );
  }}

  function initMap() {{
    try {{
      if (!window.kakao || !kakao.maps) {{
        showGuide('kakao.maps 객체가 없습니다(스크립트 로드 실패/차단).');
        return;
      }}

      var container = document.getElementById('map');
      var options = {{
        center: new kakao.maps.LatLng({{center_lat}}, {{center_lon}}),
        level: {{zoom}}
      }};
      var map = new kakao.maps.Map(container, options);

      var markerPosition  = new kakao.maps.LatLng({{center_lat}}, {{center_lon}});
      var marker = new kakao.maps.Marker({{ position: markerPosition }});
      marker.setMap(map);

      initialized = true;
      setHtml('<span class="ok">✓ Kakao 지도 로드 완료</span>');
    }} catch (e) {{
      showGuide('예외: ' + (e && e.message ? e.message : e));
    }}
  }}

  // SDK 로드(autoload=true). onload에서 바로 init.
  var script = document.createElement('script');
  script.src = 'https://dapi.kakao.com/v2/maps/sdk.js?appkey={{js_key}}&autoload=true';
  script.async = true;

  script.onload = function() {{
    initMap();
  }};
  script.onerror = function() {{
    showGuide('SDK 스크립트 로드 실패(onerror). 네트워크/차단 여부를 확인하세요.');
  }};

  document.head.appendChild(script);

  // 6초 타임아웃 가드
  setTimeout(function() {{
    if (!initialized) {{
      showGuide('6초 동안 로드되지 않았습니다(도메인 미등록/차단 가능).');
    }}
  }}, 6000);
}})();
</script>
</body>
</html>""".format(center_lat=center_lat, center_lon=center_lon, zoom=zoom, js_key=js_key)

    components.html(kakao_html, height=height, scrolling=False)

    st.caption("⬇︎ (대체지도) JS-SDK가 막히는 환경이 있어 아래 Folium 지도를 항상 같이 제공합니다.")
    render_map_folium(center_lat, center_lon, zoom=13, tile_mode="kakao")

def parse_opendata_error(xml_text: str):
    """공공데이터포털 오류 XML에서 resultCode/resultMsg 추출"""
    try:
        root = ET.fromstring(xml_text)
        code = root.findtext(".//resultCode")
        msg = root.findtext(".//resultMsg")
        if code or msg:
            return (code or "").strip(), (msg or "").strip()
    except Exception:
        pass
    return "", ""


def fetch_rtms(url: str, lawd5: str, ym: str) -> pd.DataFrame:
    params = {"serviceKey": SERVICE_KEY, "LAWD_CD": lawd5, "DEAL_YMD": ym, "numOfRows": 1000, "pageNo": 1}
    r = _session.get(url, params=params, headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]}, timeout=20)

    if r.status_code != 200:
        c, m = parse_opendata_error(r.text)
        hint = f"HTTP {r.status_code}"
        if c or m:
            hint += f" / resultCode={c} / resultMsg={m}"
        raise RuntimeError(hint)

    c, m = parse_opendata_error(r.text)
    # 공공데이터포털은 정상도 resultCode가 "00" 또는 "000" 등으로 내려올 수 있습니다.
    if c and c not in ("00", "000", "0"):
        raise RuntimeError(f"resultCode={c} / resultMsg={m}")

    root = ET.fromstring(r.text)
    items = root.findall(".//item")
    if not items:
        return pd.DataFrame()
    return pd.DataFrame([{c.tag: c.text for c in list(it)} for it in items])


if st.button("실거래 조회"):
    if not SERVICE_KEY:
        st.error("SERVICE_KEY가 없습니다.")
    elif not st.session_state.lawd10:
        st.error("법정동코드를 입력하세요.")
    else:
        lawd5 = st.session_state.lawd10[:5]
        dfs = []
        y, m0 = int(end_ym[:4]), int(end_ym[4:])
        try:
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
        except Exception as e:
            st.error("실거래 조회 중 오류가 발생했습니다.")
            st.code(mask_secret(str(e)))
            if show_debug:
                st.caption("TIP) 403이면 (1) 키/권한 (2) 엔드포인트 불일치 가능성이 큽니다. v11은 Dev->일반 엔드포인트로 교체했습니다.")
        else:
            merged = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
            if merged.empty:
                st.warning("조회된 실거래가가 없습니다.")
            else:
                st.success(f"총 {len(merged):,}건")
                st.dataframe(merged.head(500), use_container_width=True)

st.caption("안정형 v14 – (완전) 카카오맵 JS SDK 전환 + (대체) folium 유지 + 실거래/법정동 기존 기능 유지")
