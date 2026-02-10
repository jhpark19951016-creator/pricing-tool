# 분양가 산정 Tool (Streamlit)

## v5 변경사항
- VWorld 역지오코딩 502/서버 오류 대응 강화
  - HTTP 재시도(429/5xx) + 추가 1회 sleep 재시도
  - 오류 메시지에 API Key 노출 방지(마스킹)
  - 법정동코드 실패 시 주소(라벨)만 백업 표시(OSM Nominatim)

## Secrets
```toml
SERVICE_KEY = "공공데이터포털_키"
VWORLD_KEY = "VWorld_키"
```
