# 운영 점검과 보강 (2026-11-22)

코드와 인프라를 읽어 "혼자 쓰는 도구"와 "남에게 맡겨도 되는 서비스" 사이의 틈을 찾고, 코드로 고칠 수 있는 것부터 고쳤다.
점검은 두 갈래로 했다: 백엔드(보안, 안정성, 관측, 남용 방지, 데이터, API, 테스트)와 인프라·CI·프론트 런타임.
서버 설정을 손대야 하는 것과 검증 없이 올리기 위험한 것은 아래 "남긴 것"에 이유와 함께 적었다.

## 이번에 고친 것

| 영역 | 무엇이 문제였나 | 고친 것 |
|---|---|---|
| 안정성 | 시세 조회(yfinance)가 응답 없이 멈추면 실행 칸(동시 2개)을 계속 잡아 다른 분석과 자동 판단이 멈춘다 | 데이터 단계 전체에 90초 제한 (`pipeline.DATA_TIMEOUT`) |
| 안정성 | 자동 판단이 한 번 실패하면 다음 날까지 다시 하지 않고, 실패를 알 길이 로그뿐 | 15분 뒤 다시(한 시각에 세 번까지), 실패 종목과 마지막 오류를 `/health` 에 |
| 안정성 | DB 가 죽으면 캐시 호출마다 10초씩 기다린다. 저장이 한 번 실패하면 결과를 잃는다 | 캐시 연결 대기 3초, 실행 결과 저장은 한 번 더 시도 |
| 안정성 | 가중치 조정(대형주 31개 x 10년)이 분석 두 개와 겹쳐 메모리 한도(1GB)를 넘을 수 있다 | 조정도 실행 칸을 하나 잡는다 |
| 남용 방지 | 없는 종목을 `/quote` 에 넣으면 그때마다 10년 시세를 다시 부른다. 공개 조회에 제한이 없다 | 없는 응답을 10분 기억, `/quote`·`/search` 는 IP 마다 분당 60번 |
| 보안 | 응답에 보안 헤더가 하나도 없다 | API 는 미들웨어(본문을 건드리지 않는 순수 ASGI), 화면은 nginx 가 CSP 포함해 붙인다 |
| 보안 | API 문서 화면(`/api/docs`)이 공개 | `API_DOCS=1` 일 때만 |
| 보안 | 실행 중 오류의 내부 예외 내용이 화면으로 나간다 | 화면에는 오류 종류만, 내용은 로그에 |
| 관측 | 어떤 커밋이 떠 있는지, 자동 판단과 조정이 언제 돌았는지 알 수 없다 | `/health` 에 version(커밋), 기동 시각, 자동 판단·조정 상태. 결과마다 `meta.app_version`, `meta.weights_version` |
| 정확성 | 결과의 "LLM 호출 수"가 프로세스 누적값이었다 | 그 실행에서 부른 횟수로 |
| 정확성 | `GET /runs?symbol=$$` 가 500 | 400 |
| 성능 | 판단 기록 화면이 줄마다 10년 시세 캐시(수백 KB)를 다시 읽는다 | 요청 안에서 종목별로 한 번만 |
| 데이터 | 캐시를 기동할 때만 비운다 | 자동 판단 루프가 돌 때마다 |
| 프론트 | 그리다 오류가 나면 앱 전체가 하얗게 된다 | 오류 경계(안내와 새로 고침) |
| 프론트 | 실행 중에 다른 화면으로 가도 끝나면 결과 화면으로 끌려온다 | 화면을 떠나면 진행 상황 수신을 끊는다 (분석은 서버에서 끝까지 돌고 저장된다) |
| 프론트 | 응답 없는 요청을 끝없이 기다린다 | 30초 제한과 안내 문장 |
| 프론트 | JS 묶음(400KB)을 압축 없이 보낸다 | nginx gzip |
| 배포 | 배포 게이트(`publish-images` 의 test)가 PR 의 CI 보다 약했다: DB 통합 테스트와 이미지 import 검사가 빠져 있었다 | 게이트에도 postgres 서비스와 import 검사 |
| CI | 대시보드 이미지와 nginx 설정은 main 에 올라가야 처음 빌드된다 | PR 에서 이미지 빌드 + `nginx -t` |
| CI | 의존성 갱신을 알려 주는 것이 없다. 워크플로 토큰 권한이 기본값 | dependabot(월 1회, 생태계별 한 PR), `permissions: contents: read` |

CSP 는 `default-src 'self'` 에 스크립트는 자기 출처만 허용한다. 그래서 테마를 미리 정하는 인라인 스크립트를 `/theme.js` 로 뺐다.
스타일은 차트가 위치를 `style` 속성으로 넣어 `'unsafe-inline'` 을 남겼다. 운영과 같은 헤더로 빌드 결과를 띄워 모든 탭에서 위반 0건을 확인했다.

## 남긴 것 (우선순위순)

1. **배포가 실패한 커밋을 2분마다 다시 올린다.** 헬스 실패로 롤백하면 HEAD 가 옛 커밋으로 돌아가 다음 타이머가 같은 커밋을 또 올린다.
   실패한 커밋을 파일에 적어 두고 건너뛰면 된다. 다만 서버는 저장소 안의 `infra/kaspflow-autodeploy.sh` 를 직접 실행하고, 그 스크립트가 도는 중에
   `git pull` 로 자기 자신이 바뀐다. bash 는 스크립트를 읽어 가며 실행하므로 `git pull`(81행) 앞쪽을 고치면 실행 위치가 어긋난다.
   먼저 본문을 `main() { … }; main "$@"` 로 감싸는 변경을 서버에서 손으로 적용한 뒤에 고친다.
2. **장애 알림이 없다.** 배포 실패, 백업 실패, 자동 판단 실패가 모두 로그에만 남는다. systemd `OnFailure=` 로 알림을 보내고, 바깥에서 `/api/health` 를 주기적으로 본다. 알림 받을 곳(메일, 웹훅)이 정해져야 한다.
3. **Caddy 설정.** API 응답 압축(`encode zstd gzip`: 결과 한 건이 250KB), 요청 크기 한도. 보유 수량과 평균 단가가 쿼리스트링으로 가서 접근 로그에 남는다(POST 본문으로 옮기는 편이 낫다). 서버 설정이라 이번에 손대지 않았다.
4. **적중률 통계가 최근 50건만 본다.** 자동 판단이 주 50건씩 쌓여 곧 최근 일주일치만 집계된다. 평가 결과(수익률, 적중)를 열로 저장하고 SQL 로 전체를 집계해야 한다. 목록 조회가 JSONB 에서 제목을 꺼내느라 줄마다 250KB 를 푸는 것도 같이 해결된다.
5. **저장 용량.** 실행 한 건 약 250KB(전략 검증 135KB, 차트 50KB). 주 12MB, 연 640MB 쯤이고 매일 백업도 같이 커진다. 오래된 자동 판단은 차트와 검증 상세를 빼고 남긴다.
6. **컨테이너 권한.** root 로 돌고 `cap_drop`, `read_only`, `no-new-privileges`, healthcheck 가 없다. 런타임이 kiri 라 compose 옵션이 그대로 먹는지 서버에서 확인하고 올려야 한다(실패하면 1번의 반복 배포에 걸린다).
7. **백업이 같은 디스크에 있고 복구를 해 본 적이 없다.** 덤프 뒤 `pg_restore --list` 로 읽히는지 확인, 다른 저장소로 복사, 복구 연습을 문서로.
8. **LLM 호출 상한이 없다.** 실행 한 번에 논리 호출 9번, 재시도와 대체 모델까지 최대 6배. 하루 상한을 두고 넘으면 규칙 기반 해설로 내려간다.
9. **의존성.** 직접 의존성만 고정돼 있다(해시 없음). pip-audit, 이미지 스캔, ruff 가 CI 에 없다.
10. **세션.** 토큰이 14일 살고 서버에서 무효화할 수 없다. 로그인 제한이 메모리에만 있어 재시작하면 풀린다.

판단 엔진 쪽에는 결정이 하나 남아 있다. 시장 국면 모듈은 백테스트 IC 가 -0.02~-0.04 인데 모든 종목 점수를 똑같이 깎는다(3개월 판단 약 -6점, 1개월 약 -12점).
그대로 두기, 가중치 낮추기, 점수에서 빼고 경고로만 보이기 가운데 고른다.

## 참고한 것

- OWASP Secure Headers Project, 권장 응답 헤더와 값: https://secure-headers-project.owasp.org/best-practices/
- OWASP Content Security Policy Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html
- MDN, CSP `style-src` 와 `connect-src`(EventSource 포함): https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy
- Kubernetes, liveness 와 readiness 의 차이: https://kubernetes.io/docs/concepts/workloads/pods/probes/
- Google SRE, 네 가지 신호(지연, 트래픽, 오류, 포화): https://sre.google/sre-book/monitoring-distributed-systems/
- FastAPI, 프록시 뒤 배포와 문서 화면 끄기: https://fastapi.tiangolo.com/advanced/behind-a-proxy/ , https://fastapi.tiangolo.com/how-to/conditional-openapi/
- 시장 화면 구성: CNN Fear & Greed(지표 7개), Finviz(업종별 기간 막대, 시장 폭), TradingView·StockCharts 시장 요약이 공통으로 두는 지수, 변동성, 금리, 업종 순위, 시장 폭
- 색: 0 이 기준인 값은 두 색 + 중립(Datawrapper), 막대는 값 순서로(영국 정부 통계 차트 지침)
