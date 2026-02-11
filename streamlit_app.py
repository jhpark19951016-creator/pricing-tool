# -*- coding: utf-8 -*-
"""
분양가 산정 Tool – 안정형 v14.2

v14.2 변경점(핵심)
- (완전) 카카오맵 JavaScript SDK 지도 모드 추가: iframe 로딩상태/실패사유 표시, 재시도, 지도 클릭 시 좌표를 URL 파라미터(lat,lon)로 전달해 Streamlit에 반영
- (대체) Folium 지도 모드 유지(기존처럼 OSM 기반, 필요 시 타일 변경 가능)
- 기준 계약년월: '년도/월' 분리 선택형 유지
- 최근기간: 선택형 유지
- 법정동코드 자동추적: Kakao(REST) / VWorld(REST) / auto(우선 Kakao→VWorld) 유지
- 실거래 조회: 국토부 OpenAPI(아파트/오피스텔) 조회 + resultCode/resultMsg 표시(디버그 옵션 제공)

※ Streamlit Cloud에서 동작하도록 secrets / env 읽기 방식은 다음 키명을 모두 허용합니다.
- SERVICE_KEY 또는 SERVICE_KEY_DECODED (국토부, "디코딩된" 키 권장)
- KAKAO_REST_API_KEY (카카오 로컬 REST)
- KAKAO_JAVASCRIPT_KEY (카카오 JS SDK)
- VWORLD_KEY (VWorld 주소/리버스 지오코딩)

주의(중요)
- Kakao JS SDK는 "JavaScript SDK 도메인" 등록이 필요합니다. (예: https://xxxx.streamlit.app)
"""

import os
import time
import json
import datetime as dt
import xml.etree.ElementTree as ET
from typing import Optional, Tuple, Dict, Any

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
import folium
from streamlit_folium import st_folium


# -------------------------
# 기본 설정
# -------------------------
st.set_page_config(page_title="분양가 산정 Tool (안정형 v14.2)", layout="wide")
st.title("분양가 산정 Tool - 안정형")

DEFAULT_CENTER = (37.5665, 126.9780)

# 국토부 실거래(개발/운영 API는 혼동이 많아 운영 URL 기준으로 구성)
APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
OFFI_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTradeDev/getRTMSDataSvcOffiTradeDev"

# -------------------------
# Secrets / ENV 로딩
# -------------------------
def _get_secret(*names: str) -> str:
    for n in names:
        v = st.secrets.get(n, "") if hasattr(st, "secrets") else ""
        if v:
            return str(v).strip()
        v2 = os.environ.get(n, "")
        if v2:
            return str(v2).strip()
    return ""

SERVICE_KEY = _get_secret("SERVICE_KEY_DECODED", "SERVICE_KEY")
KAKAO_REST_KEY = _get_secret("KAKAO_REST_API_KEY", "KAKAO_KEY")
KAKAO_JS_KEY = _get_secret("KAKAO_JAVASCRIPT_KEY", "KAKAO_JS_KEY")
VWORLD_KEY = _get_secret("VWORLD_KEY", "VWORLD_API_KEY")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

def mask_secret(s: str) -> str:
    if not s:
        return s
    # 키/토큰류 길면 앞/뒤만 남기고 마스킹
    if len(s) <= 10:
        return "***"
    return s[:4] + "***" + s[-4:]

# -------------------------
# 날짜 옵션(년도/월)
# -------------------------
def make_year_month_options(months_back: int = 60):
    """현재월 기준 과거 months_back 개월까지 (년도, 월) 선택 리스트"""
    now = dt.datetime.now()
    options = []
    for i in range(months_back + 1):
        d = (now.replace(day=1) - dt.timedelta(days=1)).replace(day=1) if i == 0 else None
    # 더 간단히: 현재월부터 역순 생성
    cur = dt.datetime(now.year, now.month, 1)
    for i in range(months_back + 1):
        y = cur.year
        m = cur.month
        options.append((y, m))
        # 한 달 빼기
        if m == 1:
            cur = dt.datetime(y - 1, 12, 1)
        else:
            cur = dt.datetime(y, m - 1, 1)
    years = sorted({y for y, _m in options}, reverse=True)
    months_by_year: Dict[int, list] = {y: sorted({m for yy, m in options if yy == y}) for y in years}
    return years, months_by_year

# -------------------------
# 법정동코드 자동추적(카카오)
# -------------------------
def kakao_reverse_geocode(lat: float, lon: float) -> Tuple[Optional[str], str, str]:
    """카카오 coord2regioncode로 법정동코드(10자리) 추출(가능하면 region_type=B 우선)"""
    if not KAKAO_REST_KEY:
        return None, "KAKAO_REST_API_KEY 없음", ""
    url = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    try:
        r = SESSION.get(url, params={"x": lon, "y": lat}, headers=headers, timeout=12)
    except Exception as e:
        return None, f"Kakao 연결 실패: {type(e).__name__}", ""
    if r.status_code != 200:
        return None, f"Kakao HTTP {r.status_code}", ""
    try:
        data = r.json()
    except Exception:
        return None, "Kakao JSON 파싱 실패", ""

    docs = data.get("documents", []) or []
    # region_type B(법정동) 우선, 없으면 H(행정동) fallback
    pick = None
    for d in docs:
        if d.get("region_type") == "B":
            pick = d
            break
    if pick is None and docs:
        pick = docs[0]

    if not pick:
        return None, "Kakao 문서 없음", ""

    code = pick.get("code")  # 예: '1114010100'
    label = " ".join([pick.get("region_1depth_name",""), pick.get("region_2depth_name",""), pick.get("region_3depth_name","")]).strip()
    if code and len(code) == 10:
        return code, "Kakao OK(region_type=%s)" % (pick.get("region_type") or "?"), label
    return None, "Kakao 코드 추출 실패", label

# -------------------------
# 법정동코드 자동추적(VWorld)
# -------------------------
def vworld_reverse_geocode(lat: float, lon: float) -> Tuple[Optional[str], str, Any]:
    """VWorld 주소 API로 PNU/법정동코드 추출 시도"""
    if not VWORLD_KEY:
        return None, "VWORLD_KEY 없음", None
    url = "https://api.vworld.kr/req/address"
    params = {
        "service": "address",
        "request": "getAddress",
        "version": "2.0",
        "crs": "epsg:4326",
        "point": f"{lon},{lat}",
        "format": "json",
        "type": "BOTH",
        "key": VWORLD_KEY,
    }
    try:
        r = SESSION.get(url, params=params, timeout=15)
    except Exception as e:
        return None, f"VWorld 연결 실패: {type(e).__name__}", None
    if r.status_code != 200:
        return None, f"VWorld HTTP {r.status_code}", None
    try:
        data = r.json()
    except Exception:
        return None, "VWorld JSON 파싱 실패", None

    # vworld 응답에서 법정동코드 추출(가능한 필드들을 넓게 탐색)
    # 케이스별로 구조가 달라서 최대한 방어적으로
    try:
        resp = data.get("response", {})
        status = resp.get("status", "")
        if str(status).upper() != "OK":
            return None, f"VWorld status={status}", data
        results = (resp.get("result") or {}).get("items") or (resp.get("result") or {}).get("item") or []
        if isinstance(results, dict):
            results = [results]
        for it in results:
            # pnu(19자리)에서 앞 10자리 = 법정동코드
            pnu = None
            if isinstance(it, dict):
                pnu = it.get("pnu") or (it.get("address") or {}).get("pnu")
            if pnu and len(pnu) >= 10:
                return str(pnu)[:10], "VWorld OK(PNU)", data
            # direct code
            code = None
            if isinstance(it, dict):
                code = it.get("admCd") or it.get("bjdCd") or (it.get("address") or {}).get("admCd")
            if code and len(str(code)) == 10:
                return str(code), "VWorld OK(code)", data
    except Exception:
        pass

    return None, "VWorld 코드 추출 실패", data

# -------------------------
# Folium 지도(대체)
# -------------------------
def render_folium_map(lat: float, lon: float) -> Dict[str, Any]:
    m = folium.Map(location=[lat, lon], zoom_start=14, control_scale=True)
    folium.Marker([lat, lon], tooltip="선택 위치").add_to(m)
    out = st_folium(m, height=420, width=None)
    return out or {}

# -------------------------
# Kakao JS 지도(완전) – v14.2 안정화
# -------------------------
def render_kakao_js_map(lat: float, lon: float):
    if not KAKAO_JS_KEY:
        st.error("KAKAO_JAVASCRIPT_KEY 가 설정되지 않았습니다. (Streamlit Cloud → Manage app → Settings → Secrets)")
        return

    uid = dt.datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    html = f"""
    <div id="kakao_status" style="font-size:12px;color:#666;margin-bottom:6px;"></div>
    <div id="kakao_map_{uid}" style="width:100%;height:420px;border-radius:8px;"></div>

    <script>
      function setStatus(msg) {{
        var el = document.getElementById('kakao_status');
        if (el) el.textContent = msg;
      }}
      setStatus("카카오맵 로딩 중...");
    </script>

    <script type="text/javascript"
      src="https://dapi.kakao.com/v2/maps/sdk.js?appkey={KAKAO_JS_KEY}&autoload=false"
      onerror="setStatus('❌ 카카오 SDK 로드 실패 (도메인/키/차단 확장프로그램 확인)');">
    </script>

    <script>
    (function initKakaoMap() {{
      var containerId = "kakao_map_{uid}";
      var tries = 0;

      function startWhenReady() {{
        tries++;
        try {{
          if (typeof kakao === 'undefined' || !kakao.maps) {{
            if (tries > 40) {{
              setStatus('❌ 카카오 SDK 초기화 실패 (JavaScript SDK 도메인 등록/키 확인)');
              return;
            }}
            setTimeout(startWhenReady, 250);
            return;
          }}

          kakao.maps.load(function() {{
            try {{
              var container = document.getElementById(containerId);
              if (!container) {{
                setStatus('❌ 지도 컨테이너를 찾을 수 없습니다');
                return;
              }}

              var center = new kakao.maps.LatLng({lat}, {lon});
              var options = {{ center: center, level: 4 }};
              var map = new kakao.maps.Map(container, options);

              var marker = new kakao.maps.Marker({{ position: center }});
              marker.setMap(map);

              kakao.maps.event.addListener(map, 'click', function(mouseEvent) {{
                var latlng = mouseEvent.latLng;
                var newLat = latlng.getLat();
                var newLon = latlng.getLng();

                try {{
                  var url = new URL(window.parent.location.href);
                  url.searchParams.set('lat', newLat.toFixed(8));
                  url.searchParams.set('lon', newLon.toFixed(8));
                  window.parent.location.href = url.toString(); // Streamlit은 리로드 방식으로 Python에 반영
                }} catch (e) {{
                  console.log(e);
                }}
              }});

              setStatus("✅ 카카오맵 로딩 완료 (지도를 클릭하면 핀이 이동합니다)");
            }} catch (e) {{
              setStatus('❌ 지도 생성 예외: ' + (e && e.message ? e.message : e));
            }}
          }});
        }} catch (e) {{
          setStatus('❌ 초기화 예외: ' + (e && e.message ? e.message : e));
        }}
      }}

      if (document.readyState === "complete" || document.readyState === "interactive") {{
        setTimeout(startWhenReady, 0);
      }} else {{
        document.addEventListener("DOMContentLoaded", function() {{
          setTimeout(startWhenReady, 0);
        }});
      }}
    }})();
    </script>
    """
    components.html(html, height=470)

# -------------------------
# 쿼리파라미터(lat,lon) → 세션 반영
# -------------------------
qp = st.query_params
try:
    qp_lat = float(qp.get("lat", [None])[0] if isinstance(qp.get("lat"), list) else qp.get("lat", None))
    qp_lon = float(qp.get("lon", [None])[0] if isinstance(qp.get("lon"), list) else qp.get("lon", None))
except Exception:
    qp_lat = None
    qp_lon = None

if "lat" not in st.session_state:
    st.session_state.lat = DEFAULT_CENTER[0]
if "lon" not in st.session_state:
    st.session_state.lon = DEFAULT_CENTER[1]

if qp_lat is not None and qp_lon is not None:
    st.session_state.lat = qp_lat
    st.session_state.lon = qp_lon


# -------------------------
# Sidebar UI
# -------------------------
with st.sidebar:
    st.header("설정")
    product = st.selectbox("상품", ["아파트", "오피스텔", "아파트+오피스텔"], index=2)

    years, months_by_year = make_year_month_options(months_back=72)
    base_year = st.selectbox("기준 계약년도", years, index=0)
    base_month = st.selectbox("기준 계약월", months_by_year[base_year], index=0)

    기준_계약년월 = base_year * 100 + base_month
    end_ym = f"{기준_계약년월:06d}"

    recent_options = {"최근 3개월": 3, "최근 6개월": 6, "최근 12개월": 12, "최근 24개월": 24}
    recent_label = st.selectbox("최근기간", list(recent_options.keys()), index=1)
    months_back = recent_options[recent_label]

    st.divider()
    auto_track = st.toggle("법정동코드 자동 추적", value=True)
    provider = st.selectbox("자동추적 제공자", ["auto", "kakao", "vworld"], index=0)
    map_mode = st.selectbox("지도 모드", ["kakao(완전)", "folium(대체)"], index=0)
    show_debug = st.toggle("디버그(오류 상세 보기)", value=False)

    if st.button("키 상태(진단)"):
        st.write({
            "SERVICE_KEY": bool(SERVICE_KEY),
            "KAKAO_REST_API_KEY": bool(KAKAO_REST_KEY),
            "KAKAO_JAVASCRIPT_KEY": bool(KAKAO_JS_KEY),
            "VWORLD_KEY": bool(VWORLD_KEY),
        })


# -------------------------
# 지도 영역
# -------------------------
if map_mode == "kakao(완전)":
    render_kakao_js_map(st.session_state.lat, st.session_state.lon)
else:
    out = render_folium_map(st.session_state.lat, st.session_state.lon)
    # folium 클릭 좌표 반영(가능한 경우)
    try:
        if out.get("last_clicked"):
            st.session_state.lat = out["last_clicked"]["lat"]
            st.session_state.lon = out["last_clicked"]["lng"]
    except Exception:
        pass

st.caption(f"핀 좌표: {st.session_state.lat:.8f}, {st.session_state.lon:.8f}")


# -------------------------
# 자동추적 실행(법정동코드)
# -------------------------
if "lawd10" not in st.session_state:
    st.session_state.lawd10 = ""
if "last_hint" not in st.session_state:
    st.session_state.last_hint = ""
if "last_label" not in st.session_state:
    st.session_state.last_label = ""

def auto_lookup(lat: float, lon: float):
    if provider == "kakao":
        return kakao_reverse_geocode(lat, lon)
    if provider == "vworld":
        code, hint, payload = vworld_reverse_geocode(lat, lon)
        label = ""
        return code, hint, label
    # auto
    code, hint, label = kakao_reverse_geocode(lat, lon)
    if code:
        return code, hint, label
    code2, hint2, _payload = vworld_reverse_geocode(lat, lon)
    return code2, f"{hint} → {hint2}", label

if auto_track:
    code, hint, label = auto_lookup(st.session_state.lat, st.session_state.lon)
    st.session_state.last_hint = hint or ""
    st.session_state.last_label = label or ""
    if code:
        st.session_state.lawd10 = code
    else:
        st.warning(f"법정동코드 자동추적 실패(수동 입력 가능). 원인 힌트: {mask_secret(hint)}")

if st.session_state.last_hint:
    st.caption(f"자동추적 상태: {mask_secret(st.session_state.last_hint)}")
if st.session_state.last_label:
    st.caption(f"선택 위치(참고): {st.session_state.last_label}")

st.subheader("법정동코드(10자리)")
st.session_state.lawd10 = st.text_input("법정동코드 입력", value=st.session_state.lawd10)


# -------------------------
# 실거래 조회
# -------------------------
def fetch_rtms(url: str, lawd5: str, yyyymm: str) -> Tuple[pd.DataFrame, str, str]:
    """XML 응답에서 resultCode/resultMsg와 item 리스트를 파싱"""
    params = {
        "serviceKey": SERVICE_KEY,
        "LAWD_CD": lawd5,
        "DEAL_YMD": yyyymm,
        "numOfRows": 1000,
        "pageNo": 1,
    }
    r = SESSION.get(url, params=params, timeout=25)
    # 국토부는 200이어도 resultCode로 에러를 주는 경우가 많아서 text 기반 파싱
    txt = r.text
    try:
        root = ET.fromstring(txt)
    except Exception:
        # JSON/HTML 등 예상 밖
        return pd.DataFrame(), "PARSE_FAIL", "XML 파싱 실패"

    def _find_text(path: str) -> str:
        el = root.find(path)
        return el.text.strip() if el is not None and el.text else ""

    result_code = _find_text(".//resultCode")
    result_msg = _find_text(".//resultMsg")

    items = root.findall(".//item")
    rows = []
    for it in items:
        rows.append({c.tag: (c.text.strip() if c.text else "") for c in list(it)})
    df = pd.DataFrame(rows)
    return df, result_code or "", result_msg or ""

if st.button("실거래 조회"):
    if not SERVICE_KEY:
        st.error("SERVICE_KEY(국토부)가 없습니다. Streamlit Secrets에 SERVICE_KEY_DECODED 또는 SERVICE_KEY 를 넣어주세요.")
    else:
        if not st.session_state.lawd10 or len(st.session_state.lawd10) < 5:
            st.error("법정동코드(최소 5자리)가 필요합니다. (예: 11140...)")
        else:
            lawd5 = st.session_state.lawd10[:5]
            st.info(f"조회 파라미터: LAWD_CD={lawd5}, DEAL_YMD={end_ym}, 최근기간={months_back}개월")
            dfs = []
            codes = []
            msgs = []

            if product in ("아파트", "아파트+오피스텔"):
                df, rc, rm = fetch_rtms(APT_URL, lawd5, end_ym)
                dfs.append(("아파트", df))
                codes.append(rc)
                msgs.append(rm)

            if product in ("오피스텔", "아파트+오피스텔"):
                df, rc, rm = fetch_rtms(OFFI_URL, lawd5, end_ym)
                dfs.append(("오피스텔", df))
                codes.append(rc)
                msgs.append(rm)

            # 상태 표시
            # (한쪽만 성공/실패할 수 있어서 합쳐서 보여줌)
            st.success(" / ".join([f"resultCode={c or 'N/A'} resultMsg={m or 'N/A'}" for c, m in zip(codes, msgs)]))

            for name, df in dfs:
                st.subheader(f"{name} 실거래 결과")
                if df is None or df.empty:
                    st.warning("조회 결과가 없습니다.")
                else:
                    st.dataframe(df, use_container_width=True)

            if show_debug:
                st.caption("디버그 힌트")
                st.code({
                    "SERVICE_KEY(masked)": mask_secret(SERVICE_KEY),
                    "SERVICE_KEY_len": len(SERVICE_KEY),
                    "KAKAO_REST_API_KEY(masked)": mask_secret(KAKAO_REST_KEY),
                    "KAKAO_JAVASCRIPT_KEY(masked)": mask_secret(KAKAO_JS_KEY),
                    "VWORLD_KEY(masked)": mask_secret(VWORLD_KEY),
                })
