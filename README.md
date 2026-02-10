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
