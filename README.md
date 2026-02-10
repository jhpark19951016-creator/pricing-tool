# 분양가 산정 Tool (Streamlit)

## v10 변경사항 (실거래 조회 오류 해결)
- 공공데이터포털 `serviceKey` 이중 인코딩 문제를 방지합니다.
  - 키에 `%`가 포함되어 있으면 `unquote()`로 1회 디코딩 후 사용
- `raise_for_status()`로 앱이 바로 죽지 않도록:
  - HTTP 상태코드 + XML의 `resultCode/resultMsg`를 화면에 표시
- 사이드바에 `키 상태(진단)` 확장 영역 추가

## Secrets
```toml
SERVICE_KEY = "공공데이터포털_키"     # (중요) 인코딩된 키여도 OK
KAKAO_REST_API_KEY = "카카오_REST_API_키"
# VWORLD_KEY = "VWorld_키" (옵션)
```
