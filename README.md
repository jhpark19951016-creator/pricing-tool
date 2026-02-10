# 분양가 산정 Tool (Streamlit)

## v6 변경사항 (법정동코드 자동추적 안정화)
- VWorld 역지오코딩을 type=PARCEL → BOTH → ROAD 순으로 시도
- bjdCd가 없으면 PNU(19자리)에서 앞 10자리(법정동코드) 추출
- 5xx/429 재시도 + 간헐 오류 대비 sleep
- 디버그 OFF여도 최소 상태 힌트 표시(키 마스킹)

## Secrets
```toml
SERVICE_KEY = "공공데이터포털_키"
VWORLD_KEY  = "VWorld_키"
```
