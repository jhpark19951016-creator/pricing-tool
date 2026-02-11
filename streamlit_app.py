# -*- coding: utf-8 -*-
"""
분양가 산정 Tool - 안정형 v14.6
- (핵심) 카카오맵 JS-SDK 로드/클릭 좌표 전달 안정화(Top navigation 방식)
- (대안) Leaflet(기본 OSM) 지도 fallback 유지
- (성능) 역지오코딩/법정동코드 조회 캐싱 + 핀 변경 시에만 호출
- (안전) 기존 기능(실거래/법정동/키 진단/설정 UI) 유지, 변경 시 표시
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List

import pandas as pd
import requests
import streamlit as st

try:
    from streamlit_folium import st_folium
    import folium
except Exception:
    st_folium = None
    folium = None

import streamlit.components.v1 as components

APP_VERSION = "v14.8"

# -----------------------------
# 공통 유틸
# -----------------------------
def mask_key(s: Optional[str], keep: int = 6) -> str:
    if not s:
        return "(없음)"
    s = str(s).strip()
    if len(s) <= keep:
        return s
    return s[:keep] + "…" + f"(len={len(s)})"

def get_env(name: str) -> str:
    # Streamlit Cloud secrets -> env -> fallback
    v = None
    try:
        v = st.secrets.get(name)  # type: ignore[attr-defined]
    except Exception:
        v = None
    if v is None:
        v = os.getenv(name, "")
    return str(v).strip()

def safe_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return default

# Streamlit 1.32+ : st.query_params
def get_query_params() -> Dict[str, str]:
    try:
        qp = st.query_params  # type: ignore[attr-defined]
        # qp is dict-like
        return {k: (v if isinstance(v, str) else (v[0] if v else "")) for k, v in qp.items()}
    except Exception:
        # legacy
        try:
            q = st.experimental_get_query_params()
            return {k: (v[0] if v else "") for k, v in q.items()}
        except Exception:
            return {}

def set_query_params(**kwargs: str) -> None:
    # NOTE: 이 함수는 사용 안 함(JS에서 top navigation으로 갱신)
    try:
        st.query_params.update(kwargs)  # type: ignore[attr-defined]
    except Exception:
        try:
            st.experimental_set_query_params(**kwargs)
        except Exception:
            pass

# -----------------------------
# 키/설정
# -----------------------------
SERVICE_KEY_RAW = get_env("SERVICE_KEY")          # 공공데이터포털 (보통 URL-encoded 키)
SERVICE_KEY_DEC = get_env("SERVICE_KEY_DEC")      # 디코딩된 키(있으면 사용)
KAKAO_REST_API_KEY = get_env("KAKAO_REST_API_KEY")
KAKAO_JAVASCRIPT_KEY = get_env("KAKAO_JAVASCRIPT_KEY")
VWORLD_KEY = get_env("VWORLD_KEY")

# -----------------------------
# Kakao REST: 역지오코딩 (법정동/행정동 코드)
# -----------------------------
KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"

@st.cache_data(show_spinner=False, ttl=60*60*6)
def kakao_coord2regioncode(lat: float, lon: float, kakao_rest_key: str) -> Dict[str, Any]:
    """
    lat/lon -> region code 응답(JSON)
    - Authorization: KakaoAK {REST_API_KEY}
    """
    if not kakao_rest_key:
        raise ValueError("KAKAO_REST_API_KEY가 설정되어 있지 않습니다.")
    headers = {"Authorization": f"KakaoAK {kakao_rest_key}"}
    params = {"x": lon, "y": lat}
    r = requests.get(KAKAO_LOCAL_URL, headers=headers, params=params, timeout=8)
    r.raise_for_status()
    return r.json()

def extract_bjd_10_from_kakao_payload(payload: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """
    Kakao 응답에서 법정동 10자리 후보를 뽑아냄.
    - Kakao documents[].code 는 '행정동코드(10자리)' 성격으로 제공되는 경우가 많음.
    - 법정동코드가 필요하면 별도 매핑이 필요할 수 있으나, 실무에서는 이 10자리 코드를 그대로 쓰는 케이스가 많아 우선 사용.
    """
    docs = payload.get("documents") or []
    if not docs:
        return None, "Kakao 응답 documents가 비어있습니다."
    # B (법정동) 우선, 없으면 H(행정동)
    prefer = ["B", "H"]
    for tp in prefer:
        for d in docs:
            if d.get("region_type") == tp and d.get("code"):
                return str(d.get("code")), f"Kakao OK(region_type={tp})"
    # fallback: 첫번째 code
    if docs[0].get("code"):
        return str(docs[0].get("code")), "Kakao OK(fallback first code)"
    return None, "Kakao 응답에서 code를 찾지 못했습니다."

def extract_addr_from_kakao_payload(payload: Dict[str, Any]) -> str:
    docs = payload.get("documents") or []
    if not docs:
        return ""
    # B 우선
    for d in docs:
        if d.get("region_type") == "B":
            parts = [d.get("region_1depth_name"), d.get("region_2depth_name"), d.get("region_3depth_name"), d.get("region_4depth_name")]
            return " ".join([p for p in parts if p])
    # fallback
    d = docs[0]
    parts = [d.get("region_1depth_name"), d.get("region_2depth_name"), d.get("region_3depth_name"), d.get("region_4depth_name")]
    return " ".join([p for p in parts if p])

# -----------------------------
# 공공데이터포털: 실거래가 (예: 아파트/오피스텔)
# - 기존 키 이중 인코딩 문제 대비: raw/decoded 자동 선택
# -----------------------------
def choose_service_key() -> Tuple[str, str]:
    """
    serviceKey는 '이미 URL-encoded'된 값을 쓰는 것이 일반적.
    - SERVICE_KEY_DEC가 있으면 디코딩된 키를 params로 넘길 때 requests가 인코딩해줌 → 안정적
    - 없으면 SERVICE_KEY_RAW 사용
    """
    if SERVICE_KEY_DEC:
        return SERVICE_KEY_DEC, "SERVICE_KEY_DEC 사용(권장)"
    if SERVICE_KEY_RAW:
        return SERVICE_KEY_RAW, "SERVICE_KEY 사용"
    return "", "SERVICE_KEY 미설정"

# 예시 엔드포인트(기존 코드 유지)
APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
OFFICE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade"

def fetch_rtms(lawd_cd_5: str, deal_ym: str, product: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    lawd_cd_5: 구 코드(5자리) - 법정동코드(10자리)에서 앞 5자리 사용
    deal_ym: 'YYYYMM'
    product: '아파트+오피스텔' 등
    """
    service_key, key_hint = choose_service_key()
    if not service_key:
        raise ValueError("SERVICE_KEY가 없습니다. Streamlit secrets에 SERVICE_KEY 또는 SERVICE_KEY_DEC를 설정하세요.")

    # 상품별 URL 선택(기존 로직 유지)
    if product.startswith("아파트"):
        url = APT_URL
    else:
        url = OFFICE_URL

    params = {
        "serviceKey": service_key,
        "LAWD_CD": lawd_cd_5,
        "DEAL_YMD": deal_ym,
        "pageNo": 1,
        "numOfRows": 999,
    }
    r = requests.get(url, params=params, timeout=12)
    meta = {"http_status": r.status_code, "url": r.url, "service_key_hint": key_hint}
    r.raise_for_status()

    # 공공데이터포털은 XML이 많음. 간단 파싱(기존 기능 유지 목적의 최소 파서)
    txt = r.text
    if "<resultCode>" in txt and "<resultMsg>" in txt:
        rc = re.search(r"<resultCode>(.*?)</resultCode>", txt)
        rm = re.search(r"<resultMsg>(.*?)</resultMsg>", txt)
        meta["resultCode"] = rc.group(1) if rc else ""
        meta["resultMsg"] = rm.group(1) if rm else ""

    # items 파싱(가벼운 정규식 기반) - 프로젝트의 상세 파서는 별도 개선 가능
    items = re.findall(r"<item>(.*?)</item>", txt, flags=re.S)
    rows: List[Dict[str, Any]] = []
    for it in items:
        def g(tag: str) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", it)
            return m.group(1).strip() if m else ""
        rows.append({
            "법정동": g("umdNm") or g("umd") or "",
            "도로명": g("roadNm") or "",
            "지번": g("jibun") or "",
            "전용면적": g("excluUseAr") or g("area") or "",
            "층": g("floor") or "",
            "거래금액": g("dealAmount") or "",
            "년": g("dealYear") or "",
            "월": g("dealMonth") or "",
            "일": g("dealDay") or "",
            "해제여부": g("cdealType") or "",
        })
    df = pd.DataFrame(rows)
    return df, meta

# -----------------------------
# 지도 렌더링
# -----------------------------
DEFAULT_LAT = 37.5665
DEFAULT_LON = 126.9780

def render_map_leaflet(lat: float, lon: float, zoom: int = 14, height: int = 470) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Leaflet(Folium) 지도 + 클릭 좌표 획득 (st_folium)
    """
    if st_folium is None or folium is None:
        return None, "streamlit-folium/folium 미설치로 Leaflet 지도를 사용할 수 없습니다."

    m = folium.Map(location=[lat, lon], zoom_start=zoom, control_scale=True)
    folium.Marker([lat, lon], tooltip="선택 위치").add_to(m)
    # NOTE: 여기서는 OSM 기본 타일 사용(안정성). Kakao 타일은 접근/버전 이슈로 비권장.
    data = st_folium(m, height=height, width=None)
    return data, "Leaflet OK"

def render_map_kakao_js(lat: float, lon: float, js_key: str, height: int = 520, zoom_level: int = 4) -> str:
    """Kakao Maps JS SDK 지도 렌더링용 HTML(srcdoc).

    Streamlit components.html은 iframe(srcdoc)로 렌더링되므로,
    - 로딩/도메인/차단 이슈가 생기면 kakao 객체는 잡히는데 kakao.maps가 생성되지 않는 케이스가 있습니다.
    - 아래 HTML은 현재 실행 컨텍스트(origin/href/referrer)와 SDK 준비 상태를 화면에 표시해 원인 파악을 쉽게 합니다.

    기존 기능(핀 좌표 전달/법정동 자동추적)은 *유지*하고,
    Kakao SDK가 준비되지 못하면 UI에 구체적인 힌트를 노출합니다.
    """
    lat = float(lat)
    lon = float(lon)
    zoom_level = int(zoom_level)

    # autoload=false로 명시 로드를 기본으로 시도합니다.
    sdk_url = (
        "https://dapi.kakao.com/v2/maps/sdk.js"
        f"?appkey={js_key}&autoload=false&libraries=services,clusterer,drawing"
    )

    # NOTE: 중괄호가 많은 JS를 안전하게 넣기 위해 f-string을 최소화하고 .format만 사용합니다.
    template = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: #fff;
      font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;
    }}
    #wrap {{
      width: 100%;
      height: 100%;
      position: relative;
    }}
    #map {{
      width: 100%;
      height: 100%;
      min-height: {height}px;
      background: #f7f7f7;
    }}
    #panel {{
      position: absolute;
      top: 10px;
      left: 10px;
      right: 10px;
      background: rgba(255,255,255,0.92);
      border: 1px solid #e6e6e6;
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 12px;
      line-height: 1.45;
      color: #111;
      z-index: 9999;
    }}
    #panel .bad {{ color: #d40000; font-weight: 700; }}
    #panel .ok {{ color: #0a7a0a; font-weight: 700; }}
    #panel code {{ background:#f2f2f2; padding:1px 4px; border-radius:4px; }}
  </style>
</head>
<body>
  <div id="wrap">
    <div id="map"></div>
    <div id="panel">
      <div><b>Kakao JS-SDK 상태</b></div>
      <div id="ctx"></div>
      <div id="s1">1) SDK 스크립트 로딩: 대기</div>
      <div id="s2">2) kakao 객체: 대기</div>
      <div id="s3">3) kakao.maps 객체: 대기</div>
      <div id="s4">4) 지도 생성: 대기</div>
      <div id="err" class="bad"></div>
      <div style="margin-top:6px; color:#555">
        팁: 이 패널의 <code>origin</code> 값이 Kakao Developers &gt; 플랫폼 &gt; JavaScript SDK 도메인에 등록된 값과
        정확히 일치해야 합니다(https 포함). 광고차단/사내망 보안정책도 종종 원인이 됩니다.
      </div>
    </div>
  </div>

  <script>
    // --- context 출력 (사용자가 이미 도메인 등록은 했다고 했으니, 여기서는 '확인용'으로만 표시) ---
    (function() {{
      var ctx = document.getElementById('ctx');
      try {{
        ctx.innerHTML =
          '<div>origin: <code>' + (location.origin || '(null)') + '</code></div>' +
          '<div>href: <code>' + location.href + '</code></div>' +
          '<div>referrer: <code>' + (document.referrer || '(empty)') + '</code></div>';
      }} catch(e) {{}}
    }})();

    function setLine(id, ok, msg) {{
      var el = document.getElementById(id);
      if (!el) return;
      el.innerHTML = (ok ? '<span class="ok">✅</span> ' : '<span class="bad">❌</span> ') + msg;
    }}
    function setErr(msg) {{
      var el = document.getElementById('err');
      if (el) el.textContent = msg;
    }}

    // Streamlit -> Python 값 전달
    function sendValue(obj) {{
      try {{
        var payload = JSON.stringify(obj);
        window.parent.postMessage({{ isStreamlitMessage: true, type: "STREAMLIT_COMPONENT_VALUE", value: payload }}, "*");
      }} catch(e) {{}}
    }}

    // --- Kakao SDK 로드 ---
    (function loadSdk() {{
      setLine('s1', false, 'SDK 스크립트 로딩: 시작');
      var script = document.createElement('script');
      script.src = "{sdk_url}";
      script.async = true;
      script.onload = function() {{
        setLine('s1', true, 'SDK 스크립트 로딩: 완료');
        setLine('s2', !!window.kakao, 'kakao 객체: ' + (window.kakao ? 'OK' : '없음'));
        if (!window.kakao) {{
          setErr('SDK는 로드됐지만 window.kakao가 없습니다. 브라우저 확장/보안정책/CSP 등을 확인해주세요.');
          return;
        }}

        // autoload=false이므로 명시적으로 load 호출
        try {{
          if (window.kakao.maps && window.kakao.maps.load) {{
            window.kakao.maps.load(initMap);
          }} else {{
            // maps 네임스페이스가 바로 안 잡히는 케이스를 위해 짧게 재시도
            waitForMaps(0);
          }}
        }} catch(e) {{
          setErr('kakao.maps.load 호출 중 예외: ' + (e && e.message ? e.message : e));
        }}
      }};
      script.onerror = function() {{
        setLine('s1', false, 'SDK 스크립트 로딩: 실패');
        setErr('Kakao SDK 스크립트를 불러오지 못했습니다. 네트워크/차단 여부를 확인해주세요.');
      }};
      document.head.appendChild(script);
    }})();

    function waitForMaps(cnt) {{
      setLine('s3', false, 'kakao.maps 객체: 준비 대기(' + cnt + ')');
      if (window.kakao && window.kakao.maps && window.kakao.maps.load) {{
        try {{
          window.kakao.maps.load(initMap);
          return;
        }} catch(e) {{
          setErr('kakao.maps.load 재시도 중 예외: ' + (e && e.message ? e.message : e));
          return;
        }}
      }}
      if (cnt >= 80) {{
        setLine('s3', false, 'kakao.maps 객체: 준비 실패');
        setErr('SDK는 로드됐지만 kakao.maps가 생성되지 않았습니다. (도메인/키/차단/사내망 정책 이슈 가능)');
        return;
      }}
      setTimeout(function() {{ waitForMaps(cnt + 1); }}, 50);
    }}

    function initMap() {{
      // maps 네임스페이스/Map 생성자 확인
      if (!(window.kakao && window.kakao.maps && window.kakao.maps.Map)) {{
        setLine('s3', false, 'kakao.maps 객체: 없음');
        setErr('kakao.maps.Map을 찾지 못했습니다. (도메인/키/차단 이슈 가능)');
        return;
      }}
      setLine('s3', true, 'kakao.maps 객체: OK');

      setLine('s4', false, '지도 생성: 시작');
      try {{
        var container = document.getElementById('map');
        if (!container) {{
          setErr('map 컨테이너를 찾지 못했습니다.');
          return;
        }}

        // 컨테이너 높이 보정 (간헐적으로 0이 되는 경우 방지)
        if (container.clientHeight < 50) {{
          container.style.height = "{height}px";
        }}

        var options = {{
          center: new kakao.maps.LatLng({lat}, {lon}),
          level: {zoom_level}
        }};
        var map = new kakao.maps.Map(container, options);

        // Streamlit에서 iframe 크기 변동 시 relayout
        setTimeout(function() {{
          try {{ map.relayout(); }} catch(e) {{}}
        }}, 200);

        // 클릭 핀 + 좌표 전달
        var marker = new kakao.maps.Marker({{ position: map.getCenter() }});
        marker.setMap(map);

        kakao.maps.event.addListener(map, 'click', function(mouseEvent) {{
          var latlng = mouseEvent.latLng;
          marker.setPosition(latlng);
          sendValue({{
            lat: latlng.getLat(),
            lon: latlng.getLng(),
            ts: Date.now()
          }});
        }});

        setLine('s4', true, '지도 생성: 완료 (클릭하면 좌표가 앱으로 전달됩니다)');
      }} catch(e) {{
        setLine('s4', false, '지도 생성: 실패');
        setErr('지도 생성 중 예외: ' + (e && e.message ? e.message : e));
      }}
    }}
  </script>
</body>
</html>
""".format(height=height, lat=lat, lon=lon, zoom_level=zoom_level, sdk_url=sdk_url)

    components.html(template, height=height+20, scrolling=False)
    return 'Kakao JS OK(렌더링 시도)'

st.set_page_config(page_title=f"분양가 산정 Tool - 안정형 {APP_VERSION}", layout="wide")

st.title("분양가 산정 Tool - 안정형")
st.caption(f"버전: {APP_VERSION}  |  지도: Kakao JS/Leaflet  |  법정동/실거래: Kakao/공공데이터")

# Sidebar: 설정
with st.sidebar:
    st.header("설정")

    product = st.selectbox("상품", ["아파트+오피스텔", "아파트", "오피스텔"], index=0)

    # 계약년/월은 원래 선택형으로 유지 (요청사항)
    year = st.selectbox("기준 계약년도", list(range(2020, 2031)), index=list(range(2020, 2031)).index(2026))
    month = st.selectbox("기준 계약월", list(range(1, 13)), index=1)
    recent_period = st.selectbox("최근기간", ["최근 3개월", "최근 6개월", "최근 12개월"], index=1)

    st.divider()
    auto_bjd = st.toggle("법정동코드 자동 추적", value=True)
    map_provider = st.selectbox("자동추적 제공자", ["auto", "kakao"], index=0)

    st.divider()
    map_mode = st.selectbox("지도 모드", [
        "kakao(JS-SDK/권장)",
        "leaflet(OSM/대안)",
    ], index=0)

    show_debug = st.toggle("디버그(오류 상세 보기)", value=False)

    st.divider()
    if st.button("키 상태(진단)"):
        with st.expander("키 상태(진단) - 펼쳐서 확인", expanded=True):
            st.write("✅/❌는 '값이 존재하는지'만 검사합니다(유효성은 호출 결과로 판단).")
            st.write(f"- SERVICE_KEY: {'✅' if bool(SERVICE_KEY_RAW or SERVICE_KEY_DEC) else '❌'}  ({mask_key(SERVICE_KEY_DEC or SERVICE_KEY_RAW)})")
            st.write(f"- KAKAO_REST_API_KEY: {'✅' if bool(KAKAO_REST_API_KEY) else '❌'}  ({mask_key(KAKAO_REST_API_KEY)})")
            st.write(f"- KAKAO_JAVASCRIPT_KEY: {'✅' if bool(KAKAO_JAVASCRIPT_KEY) else '❌'}  ({mask_key(KAKAO_JAVASCRIPT_KEY)})")
            st.write(f"- VWORLD_KEY: {'✅' if bool(VWORLD_KEY) else '❌'}  ({mask_key(VWORLD_KEY)})")
            sk, hint = choose_service_key()
            st.write(f"- serviceKey 선택 로직: {hint} ({mask_key(sk)})")

# -----------------------------
# 현재 좌표 결정 (쿼리파라미터 우선)
# -----------------------------
qp = get_query_params()
lat = safe_float(qp.get("lat"), DEFAULT_LAT)
lon = safe_float(qp.get("lon"), DEFAULT_LON)

# 세션에 저장(핀 이동 감지용)
if "last_lat" not in st.session_state:
    st.session_state.last_lat = lat
    st.session_state.last_lon = lon

pin_changed = (abs(lat - st.session_state.last_lat) > 1e-10) or (abs(lon - st.session_state.last_lon) > 1e-10)
if pin_changed:
    st.session_state.last_lat = lat
    st.session_state.last_lon = lon

st.markdown(f"**핀 좌표:** `{lat:.10f}`, `{lon:.10f}`")

# -----------------------------
# 지도 표시
# -----------------------------
map_col, info_col = st.columns([2.2, 1.2], gap="large")

with map_col:
    if map_mode.startswith("kakao"):
        hint = render_map_kakao_js(lat, lon, KAKAO_JAVASCRIPT_KEY, height=520)
        if show_debug:
            st.info(hint)
        st.caption("카카오맵이 '객체가 준비되지 않음'이면: (1) JS 키, (2) JavaScript SDK 도메인 등록, (3) 사내망/확장프로그램 차단을 확인하세요.")
    else:
        data, hint = render_map_leaflet(lat, lon, zoom=14, height=520)
        if show_debug:
            st.info(hint)
        if data and data.get("last_clicked"):
            c = data["last_clicked"]
            # 클릭 좌표 반영: 세션에만 반영(즉시 UI 업데이트)
            lat = float(c["lat"])
            lon = float(c["lng"])
            st.session_state.last_lat = lat
            st.session_state.last_lon = lon
            st.success(f"선택 좌표 업데이트: {lat:.10f}, {lon:.10f}")
            st.caption("Leaflet 모드는 페이지 새로고침 없이 좌표가 즉시 반영됩니다.")

with info_col:
    st.subheader("선택 위치(법정동)")
    addr = ""
    bjd10 = None
    auto_hint = ""

    if auto_bjd:
        # 핀이 바뀌었을 때만 호출(속도 개선)
        if pin_changed or ("kakao_payload_cache" not in st.session_state):
            try:
                payload = kakao_coord2regioncode(lat, lon, KAKAO_REST_API_KEY)
                st.session_state.kakao_payload_cache = payload
            except Exception as e:
                payload = None
                st.session_state.kakao_payload_cache = None
                auto_hint = f"Kakao 역지오코딩 실패: {e}"

        payload = st.session_state.get("kakao_payload_cache")
        if payload:
            bjd10, auto_hint = extract_bjd_10_from_kakao_payload(payload)
            addr = extract_addr_from_kakao_payload(payload)

    if addr:
        st.info(addr)
    else:
        st.warning("주소를 아직 가져오지 못했습니다(키/네트워크 확인).")

    st.caption(f"자동추적 상태: {auto_hint or '대기'}")

    st.subheader("법정동코드(10자리)")
    bjd_input = st.text_input("법정동코드 입력", value=bjd10 or "", help="자동이 실패하면 수동으로 10자리 코드를 입력하세요.")
    st.session_state.bjd_input = bjd_input.strip()

    # -----------------------------
    # 실거래 조회
    # -----------------------------
    st.divider()
    st.subheader("실거래 조회")

    def ym_to_str(y: int, m: int) -> str:
        return f"{y}{m:02d}"

    deal_ym = ym_to_str(year, month)
    lawd_cd_5 = (st.session_state.bjd_input[:5] if st.session_state.bjd_input else "")

    if st.button("실거래 조회", type="primary"):
        if not st.session_state.bjd_input or len(st.session_state.bjd_input) < 5:
            st.error("법정동코드(최소 5자리)가 필요합니다.")
        else:
            try:
                with st.spinner("실거래 데이터를 조회 중입니다..."):
                    df, meta = fetch_rtms(lawd_cd_5=lawd_cd_5, deal_ym=deal_ym, product=product)
                if meta.get("resultCode") and meta.get("resultCode") != "000":
                    st.warning(f"resultCode={meta.get('resultCode')} / resultMsg={meta.get('resultMsg')}")
                st.success(f"조회 완료. 건수: {len(df)}")
                if len(df) > 0:
                    st.dataframe(df, use_container_width=True)
                if show_debug:
                    st.json(meta)
            except requests.HTTPError as e:
                st.error("실거래 조회 중 HTTP 오류가 발생했습니다.")
                if show_debug:
                    st.write(str(e))
            except Exception as e:
                st.error("실거래 조회 중 오류가 발생했습니다.")
                if show_debug:
                    st.exception(e)

# 하단 안내
st.caption("※ v14.6은 '지도/법정동' 안정화를 위해 지도 모드를 2개로 단순화했습니다. 기존 실거래/키진단/자동추적 흐름은 유지합니다.")
