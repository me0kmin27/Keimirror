# 사용자 정의 telemetry 실제 제거 완료 — 2026-11-13

이 문서는 [폐지 체크리스트](telemetry-retirement.md)의 실행 결과다.
소스 삭제, 서비스 중지, 실제 파일·패키지 삭제를 서로 구분한다.
운영 readback 관측은 **2026-11-13 11:09–11:15 KST**, 최종 공개 HTTP·rsync
검사는 **11:15 KST** 기준이다. 이후의 지속적인 가용성이나 미래 상태를 보증하지 않는다.

## 완료된 범위

- 홈페이지: 집계 서비스·타이머 정의, 집계 코드, 전용 설정·상태와 nginx의
  상태 JSON 연결을 제거했다. 전송량·상태 UI, 조회 요청과 주기 타이머도 제거했다.
- 두 미러: collector·sizes 서비스/타이머와 존재하던 telemetry RPC helper,
  observer/shim 구현, 전용 설정·상태·완료 로그 및 회전 로그를 제거했다.
  관련 nginx 설정·모듈 로딩 지시문·런타임 디렉터리·상태 소켓도 제거했다.
- 두 미러의 `libnginx-mod-http-project-live` 패키지를 제거했고, 별도로 설치됐던
  Mirror의 versioned module도 제거했다. nginx 자체와 관계없는 패키지는 제거하지 않았다.
- 이미 시작된 작업을 위한 임시 native compatibility link는 해당 이전 shell들이
  끝난 뒤 모두 제거했다. 두 미러의 `/opt/keimirror-telemetry`도 존재하지 않는다.
- Mirror2의 기존 `keimirror-agent.service`는 이름만으로 판단하지 않았다.
  설치된 코드의 기능과 inactive/disabled 상태를 확인해 이전 상태·전송량 수집
  클라이언트로 분류한 뒤, 전용 코드·서비스 정의·설정을 함께 제거했다.
  환경변수 값이나 인증정보는 공개 증적에 포함하지 않았다.

독립 원격 재조회에서 **7개 서비스와 5개 타이머, 합계 12개 unit이
`LoadState=not-found`, `ActiveState=inactive`**였다.
홈페이지 2개, Mirror 5개, Mirror2 5개이며 마지막 수에는 기존 agent가 포함된다.
이는 단순히 stop/disable한 상태가 아니다.

실행 중 observer가 남지 않았고, 모듈 삭제 전에는 해당 모듈의 process mapping,
전용 로그의 열린 FD와 바인딩된 상태 소켓이 없는 것을 확인했다.
운영 파일 삭제 뒤에는 삭제 목록의 부재와 보호 대상 파일을 별도 readback으로 대조했다.
복구용 archive·설치 상태 기반 rollback package·작업 증적은 웹 경로 밖의 비공개
관리/백업 디렉터리에 보존했다. 이 백업까지 삭제했다는 의미는 아니다.

## 유지한 미러 기능과 보호 대상

- 두 미러의 기존 동기화 스크립트 **26개**는 원래 native `rsync`/`rsync-ssl`
  실행 표현으로 복원했다. 그 밖의 옵션·리다이렉션·소유권·모드와 스케줄은 보존했다.
  `ftpsync`, 미러 데이터, native rsync 로그·세션 helper는 제거 대상이 아니었다.
- nginx master와 native rsync daemon의 기존 PID가 유지됐다. nginx에는 구문 검사 후
  graceful reload만 사용했고 restart/re-exec/USR2는 사용하지 않았다.
  진행 중 native 동기화나 다운로드를 강제로 종료하지 않았다.
- 보호 대상으로 수집한 SSH 관련 파일, cron 설정, native 실행 파일, chrony 및
  관계없는 서비스·설정은 대조했다. 미러 콘텐츠를 재귀 스캔하거나 시험 동기화를
  시작하지 않았다. 이 검증은 모든 콘텐츠의 전후 전체 해시 비교를 의미하지 않는다.
- 홈페이지의 10개 프로젝트, 직접 링크 20개, 프로토콜 주소 6개, 검색·필터,
  한국어/영어·테마·no-JS, 복사·미리보기·후원·연락처와 기존 Grafana 링크를 보존했다.

## 실제 사용자 경로 검증

- 홈페이지 HTTP **200**, 프로젝트 직접 링크 **20/20 HTTP 200**.
- 양쪽 미러의 작은 파일에 대한 HTTP Range **2/2 HTTP 206**.
- 공개된 `rsync.keiminem.com`, `rsync2.keiminem.com`에서 목록 조회와
  `archlinux/lastsync` 실제 다운로드 성공. 각각 11바이트를 수신했고 같은 시점의
  해당 HTTPS 파일과 바이트가 일치했다. 시험 수신 파일은 제거했다.
- 폐지한 홈페이지 `/status/mirrors.json` 및 두 미러의
  `/.well-known/keimirror-status.json`: **3/3 HTTP 404**.
- 실제 홈페이지에서 320/1440px, 한국어/영어, light/dark의 8개 조합과 no-JS를
  검사했다. telemetry 요청·관련 DOM·페이지 오류가 없고, 검색·언어 전환,
  모바일 터치 타깃·가로 넘침·배포 자산 해시 검사를 통과했다.
- Termix의 작업용 임시 snippet 잔재는 최종 조회에서 0개였다.

로컬 관리 호스트에는 rsync 실행 파일이 없어 공통 공개 검사기의 로컬 rsync 부분은
실행하지 못했다. 이를 서비스 장애나 성공으로 간주하지 않고, 별도 관리 호스트에서
위의 실제 목록 조회·파일 다운로드를 수행했다. 처음 다운로드 증적의
`module_count` 필드는 실제로 비어 있지 않은 출력 행 수를 센 것이므로 모듈 수로
사용하지 않았다. 후속 검사 코드에서는 `listing_nonempty_lines`로 이름을 고쳤다.
동적 `lastsync` 파일 비교는 해당 검사 시점에 한정하며 지속적인 불변 파일 검증은 아니다.

## 소스와 배포 이력

- PR #48: UI와 feature 구현/설치 자산/전용 테스트를 제거했다.
  병합 커밋: `b1798501575498e882f30b2d27b20dab2404cd7e`.
- PR #49: 상태 allowlist와 fail-closed 회귀 검사를 포함한 stage-1 retirement 도구를
  반영했다. 병합 커밋: `f5b571472fe92bbaebcb4dfa0757bcf0c647ada8`.
- 런타임 도구 검토에서 발견한 R1은 수정 후 별도 bounded delta 검토를 통과했다.
  최초 FAIL 기록은 보존했다. 이 검토는 stage-1 도구 범위이며 이후 파일/패키지
  삭제 전체가 같은 독립 코드 검토를 받았다는 의미는 아니다.
- 부모 전체 Python 검사 29개는 일반/최적화 모드에서 각각 통과했다.
  브라우저 suite 89개와 GitHub의 source validation도 통과했다.
- PR 병합 자동 배포 run `43339565070`, `43347250372`는 validation 성공 후
  `Prepare SSH`에서 실패했고 upload는 skipped였다. 자동 배포 성공으로 기록하지 않는다.
  SSH 키나 접근 통제를 바꾸지 않고, 검증한 동일 정적 소스를 기존 관리 경로로
  배포했으며 운영 파일 해시와 실제 브라우저에서 별도 확인했다.
- 이 완료 기록은 문서 변경만으로 반영하며 관련 없는 자동 운영 배포를 다시 시작하지 않는다.

## 복구 경계

비공개 백업은 파일 내용·메타데이터·archive 무결성, rollback package의 파일 바이트를
검증했다. 실제 운영 전체 rollback을 실행한 것은 아니다.

실제 코드·unit·패키지 삭제 후에는 stage-1 도구의 `rollback` 하나만 실행해서는
전체 기능이 복구되지 않는다. 현재 호스트/부팅 정체성과 패키지 호환성을 새로 검토하고,
관련 코드·설정·unit archive와 패키지/수동 module을 올바른 순서로 복원한 뒤
stage-1 변경을 되돌려야 한다. 최초 timer stop 이전 상태는 별도 기록에 있으며,
quiet-state plan의 rollback은 그 최초 timer 활성 상태까지 자동 복원하지 않는다.
다른 서비스의 활성화나 기존 agent의 자동 재기동은 복구 절차에 묵시적으로 포함되지 않는다.

자세한 원본 plan·manifest·삭제 및 readback receipt·백업 위치는 비공개 운영 증적에
있다. 공개 저장소에는 자격증명, 원본 로그, private inventory를 올리지 않았다.
