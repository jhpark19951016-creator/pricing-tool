# 분양가 산정 Tool (Streamlit)

## v11 변경사항 (403 해결용)
- **RTMS 실거래 API 엔드포인트를 Dev -> 일반으로 교체**
  - 아파트: `RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade`
  - 오피스텔: `RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade`
- 공공데이터포털 `serviceKey` 이중 인코딩 방지
  - 키에 `%`가 포함되어 있으면 `unquote()`로 1회 디코딩 후 사용
- HTTP 상태코드 + XML의 `resultCode/resultMsg`를 화면에 표시

## Secrets
```toml
SERVICE_KEY = "공공데이터포털 디코딩 키"
KAKAO_REST_API_KEY = "카카오 REST API 키"
# VWORLD_KEY = "VWorld 키"(옵션)
```


## v12 변경사항
- 공공데이터포털 RTMS 응답의 `resultCode`가 `000`로 내려오는 경우도 정상으로 처리하도록 수정


## v13 변경사항 (1단계)
- Folium 엔진은 유지하고 **배경지도를 카카오(daum) 타일**로 교체(기존 기능 영향 없음)
- 다음 단계(v14)에서 카카오 JS SDK 기반으로 지도 입력부를 완전 교체 예정


## v14 변경사항 (완전 카카오맵)
- Kakao JS SDK로 지도 렌더링하는 **kakao(완전)** 모드 추가
- 클릭 좌표를 `?lat=...&lon=...` query param으로 전달하여 Streamlit(Python) 세션에 반영(리로드 방식)
- 안전을 위해 **folium(대체)** 모드 유지(기존 방식 그대로)

### Secrets 추가(필수)
```toml
KAKAO_JAVASCRIPT_KEY = "카카오 JavaScript 키"
```
