# 분양가 산정 Tool (Streamlit)

## v8 변경사항 (연결 오류 해결 방향)
Streamlit Cloud에서 VWorld가 `ConnectionError`로 막히는 케이스가 있어,
법정동코드 자동추적 제공자를 선택할 수 있게 개선했습니다.

- 제공자 선택: `auto / vworld / kakao`
- auto: VWorld 먼저 시도 → 실패 시 Kakao로 자동 폴백
- VWorld/Kakao 호출은 예외를 반드시 잡아 앱이 죽지 않게 처리
- 연결 테스트(서울시청) 버튼으로 즉시 진단

## Secrets
```toml
SERVICE_KEY = "공공데이터포털_키"

# (옵션) VWorld (Streamlit Cloud에서 막힐 수 있음)
VWORLD_KEY  = "VWorld_키"

# (권장) Kakao Local REST API Key
KAKAO_REST_API_KEY = "카카오_REST_API_키"
```
