
import streamlit as st
import streamlit.components.v1 as components

# =========================================================
# Kakao JS-SDK Map (HTTPS 안정화 버전)
# =========================================================

KAKAO_JS_KEY = st.secrets.get("KAKAO_JAVASCRIPT_KEY", "")

def render_kakao_js_map(
    lat: float = 37.5665,
    lng: float = 126.9780,
    height: int = 520,
):

    html = f"""
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>

        <style>
            html, body {{
                margin: 0;
                padding: 0;
                width: 100%;
                height: 100%;
                overflow: hidden;
                font-family: Arial, sans-serif;
            }}
            #map {{
                width: 100%;
                height: {height}px;
                background: #f2f2f2;
            }}
            #status {{
                position: absolute;
                top: 8px;
                left: 8px;
                background: rgba(255,255,255,0.95);
                padding: 6px 10px;
                border-radius: 6px;
                font-size: 12px;
                z-index: 9999;
                white-space: pre-wrap;
            }}
            .wrap {{
                position: relative;
            }}
        </style>
    </head>

    <body>
        <div class="wrap">
            <div id="status">Kakao JS-SDK loading...</div>
            <div id="map"></div>
        </div>

        <script>
            const statusEl = document.getElementById("status");

            function log(msg) {{
                console.log(msg);
                statusEl.textContent = msg;
            }}

            function initMap() {{
                try {{
                    const container = document.getElementById("map");
                    const options = {{
                        center: new kakao.maps.LatLng({lat}, {lng}),
                        level: 3
                    }};
                    const map = new kakao.maps.Map(container, options);

                    const marker = new kakao.maps.Marker({{
                        position: new kakao.maps.LatLng({lat}, {lng})
                    }});
                    marker.setMap(map);

                    log("Kakao map initialized (HTTPS OK)");
                }} catch (e) {{
                    log("Map init error: " + e);
                }}
            }}

            (function loadKakaoSDK() {{
                const script = document.createElement("script");
                script.src =
                    "https://dapi.kakao.com/v2/maps/sdk.js"
                    + "?appkey={KAKAO_JS_KEY}"
                    + "&autoload=false"
                    + "&libraries=services"
                    + "&protocol=https";

                script.async = true;

                script.onload = function () {{
                    try {{
                        if (!window.kakao) {{
                            log("SDK loaded but kakao object missing");
                            return;
                        }}
                        kakao.maps.load(initMap);
                    }} catch (e) {{
                        log("SDK onload error: " + e);
                    }}
                }};

                script.onerror = function () {{
                    log("SDK network error (blocked?)");
                }};

                document.head.appendChild(script);
            }})();
        </script>
    </body>
    </html>
    """

    components.html(html, height=height + 10, scrolling=False)


# =========================================================
# 기본 앱 실행부
# =========================================================

st.title("분양가 산정 Tool - 안정형")
st.caption("버전: v14.10 | 지도: Kakao JS-SDK | HTTPS 안정화 패치 적용")

lat = st.session_state.get("lat", 37.5665)
lng = st.session_state.get("lng", 126.9780)

render_kakao_js_map(lat, lng)
