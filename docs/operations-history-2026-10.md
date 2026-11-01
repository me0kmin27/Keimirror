# 2026년 10월 홈페이지·미러 변경 및 운영 적용 기록

작성일: **2026-11-01 KST**. 소스 범위: 2026-10-15~31 병합된 **PR #30–46**.
기준 소스: `f1bebc85e26a7d81187da9dbb7aad8dfa1cabe2b`.
운영 적용 상태는 **2026-10-31 14:51 KST 전후에 수집한 마지막 운영 증거**를 기준으로 한다.
이번 문서화는 보존된 증거와 GitHub 기록을 대조한 작업이며, 11월 1일 서버를 다시 점검하거나 변경한 결과가 아니다.

## 읽는 기준

- **소스 병합**, **대상 서버 설치**, **실제 프로세스에 로드**, **기능 검증**은 별개의 상태다.
- 아래 PR 이력은 구현 이력이다. 모든 PR이 모든 서버에 배포되었다는 뜻이 아니다. 실제 적용 범위는 서버별 기록과 해시를 우선한다.
- 일부 초기 PR 본문·중간 보고서의 OPEN/DRAFT 표시는 이후 병합 기록보다 앞선 시점의 기록이다. 최종 PR #46은 2026-10-31 **14:51:53 KST**에 병합되었다.
- `null`은 측정 근거 부족, 측정된 `0`은 해당 관측 구간의 유효한 영값이다. 간헐적 `null` 자체를 결함이나 동기화 실패로 분류하지 않는다.
- 미러 파일, 기존 SSH 키, 인증·접근 통제, 원래 동기화 일정·전송 인수·upstream을 바꾸는 작업과 관측 기능 설치를 혼동하지 않는다.
- 공개 문서에는 역할명·소스/산출물 해시·검증 결과만 정리했다. 원문 관리 로그, 자격증명, 사설 주소, 프로세스 식별 목록은 게시하지 않는다.

## 1. 전체 변경 이력

아래 날짜는 **UTC 병합일**이다. 특히 PR #39의 UTC 10월 28일 병합은 한국시간으로 10월 29일이다. PR 링크에서 논의·변경 파일·검증 기록을 확인할 수 있다.

| UTC 날짜 | PR | 병합 커밋 | 변경 사항 |
|---|---|---|---|
| 10-15 | [#30](https://github.com/me0kmin27/Keimirror/pull/30) | `7545289a38d0464881ff7ca297e98f824a8123ab` | localStorage 접근·읽기·쓰기 실패를 격리해 언어/테마 조작 유지. 잘못된 저장값 복구, 브라우저 회귀 검사·lockfile·PR/main CI 추가. |
| 10-15 | [#31](https://github.com/me0kmin27/Keimirror/pull/31) | `63d9495324e0f5dc0bfcce18c798c5a85c7ab140` | Donate 옆 기존 Grafana 공개 대시보드 링크와 한·영/반응형 검사. Grafana 인프라나 권한 변경은 아님. |
| 10-15 | [#32](https://github.com/me0kmin27/Keimirror/pull/32) | `e7bca68f0bba65e733ac1e6c1fbd289cf68641da` | 병렬 브라우저 시작 시 시험용 loopback 서버의 연결 대기열을 128로 확장. 운영 nginx 변경이나 저장소 경합 수정이 아님. |
| 10-28 | [#33](https://github.com/me0kmin27/Keimirror/pull/33) | `ce3eed6c853bb5666bb199c156d178c61cb80840` | 검색·분류 중심의 반응형 프로젝트 탐색, 접근성·복사·미리보기·후원 대화상자·IP 조회 개선, 완성 HTML 생성·JS 묶기·캐시 해시, 검증 SHA에 묶인 배포. |
| 10-28 | [#34](https://github.com/me0kmin27/Keimirror/pull/34) | `1c5a613caf3afd9d488be1187c74bdb38f8192d9` | 미러 직접 수집·홈페이지 집계·공개 JSON, 14개 저장소를 10개 프로젝트로 집계, 동기화 결과/프로세스·용량·TCP·완료 제공량 관측, adapter와 systemd/nginx 설정. 초기 주기는 15초. |
| 10-28 | [#35](https://github.com/me0kmin27/Keimirror/pull/35) | `dda0ac7655fdf6932ad5aff8f0403b75d46ffc05` | 기존 행에 상태·전체 저장 용량·RX/TX만 표시. 펼침·시각·저장소별 진단 설명을 목록에서 제거하고 API에는 보존. |
| 10-28 | [#36](https://github.com/me0kmin27/Keimirror/pull/36) | `dc94f2ce2e35d3d9e6cfd0a04dff1fa5cbeb9148` | 수집·집계·브라우저 목표 주기 1초, monotonic 인접 표본 증분, 2초 초과 간격 거부, 속도 3초/스냅샷 5초 유효기간, timer 정확도·CPU 제한. |
| 10-28 | [#37](https://github.com/me0kmin27/Keimirror/pull/37) | `16ee8574280d7b6d0f85b28527e292243642731d` | collector/aggregate 시작 제한을 10초당 20회로 조정해 1Hz 실행과의 충돌 해소. 시작 제한 자체를 제거하지 않음. |
| 10-28 | [#38](https://github.com/me0kmin27/Keimirror/pull/38) | `df2ca01ef50d9736f75011f58d7e8218cd54e53e` | 정확한 JSON location에서 open_file_cache 상속 해제. 집계 봉투에만 미래 30초 허용, 원본·속도 freshness는 유지. 시계 교정은 아님. |
| 10-28 | [#39](https://github.com/me0kmin27/Keimirror/pull/39) | `5df28883b7197f6cf609fc2f6342bf0d786bbb84` | live origin HTTP + upstream 동기화/rsync daemon 제공 TCP를 합친 관측 속도. nginx HTTP/1 모듈, private Unix reader, 정확한 nginx 의존성의 패키지 빌드·제거 안전 include. |
| 10-30 | [#40](https://github.com/me0kmin27/Keimirror/pull/40) | `c600341e55c8f232c5373c144205d1dc4720c97b` | INET_DIAG의 종료 TCP 상태를 보존. 이전 소유 cookie 손실은 해당 저장소 무효화, ownerless 활성·미지원 상태는 보수적 처리 유지. |
| 10-30 | [#41](https://github.com/me0kmin27/Keimirror/pull/41) | `6454f4b370a00477bfca6015cd26308c2703e0b7` | namespace/mount 근거와 인증된 Unix SEQPACKET의 제한된 RPC 관측 제외 증명. ENOENT는 공유 2초 deadline 안에서 전체 관측 최대 3회 재시도. |
| 10-30 | [#42](https://github.com/me0kmin27/Keimirror/pull/42) | `afa3d18b433394f125e9ca349af663ce9cf46454` | 동일 collector 실행의 비공개 숫자 진단 코드와 sidecar. 공개 schema·측정 조건 완화 아님. private_reasons.py 의존성 추가. |
| 10-30 | [#43](https://github.com/me0kmin27/Keimirror/pull/43) | `85fc9c12a25a3c2d1fadb691b4082c55a7744cb6` | 엄격히 증명된 bodyless 요청의 nginx 내장 403/404/416 응답에서 HTTP coverage 유지. 기존 불확실성·custom error_page 등을 임의 해제하지 않음. |
| 10-30 | [#44](https://github.com/me0kmin27/Keimirror/pull/44) | `30aa8228d5bcf204c2e4b1c1e73e0cf1b89f43fb` | ss 소유자 누락 시 제한된 전체 FD inventory와 반복 identity 증명으로 복구. 두 rsync shim의 /proc status snapshot 기반 SigIgn 시작 오류도 함께 수정. |
| 10-30 | [#45](https://github.com/me0kmin27/Keimirror/pull/45) | `27269689baa4ea664c4322bfdf8ecbf244b811d5` | journal·deadline·source/host provenance에 묶인 HTTP 모듈 re-exec transaction, root staging, 격리 시험·receipt. 소스 준비와 운영 성공은 별개. |
| 10-31 | [#46](https://github.com/me0kmin27/Keimirror/pull/46) | `f1bebc85e26a7d81187da9dbb7aad8dfa1cabe2b` | 실제 배포 장애의 PID publication·inventory churn·rollback worker 충돌 수정. source-only 마감; Nchan 차단과 32-worker 실제 시험 미완료 유지. |

### 기존 기반과의 관계

사이트는 이번 작업에서 새로 만든 것이 아니다. 6월의 두 미러 안내·컴포넌트·테마·한영·SEO·접속 주소·후원/문의 기능과 8월의 수동 배포 기능을 유지·개선했다. 최근 작업 이전 기준은 PR #29의 `f10ba4025425eb39678e828f3082883a83e85795`이다. 이전 기록은 [README](../README.md)의 작업 기록 및 Git 이력을 따른다.

## 2. 홈페이지에 반영된 기능과 운영 구성

- 10개 프로젝트 검색·한국어 별칭·공식/커뮤니티 필터, 미러별 직접 링크, 여섯 접속 주소 복사, 선택 시에만 여는 디렉터리 미리보기를 제공한다.
- 한·영/라이트·다크·자동 테마, 키보드 포커스, 후원 대화상자, 저장소·클립보드·IP 조회 실패 대응을 보완했다. JavaScript 없이도 본문과 링크가 남는다.
- 완성된 `index.html`과 `assets/js/site.js`를 생성해 저장소에 보관하고 CSS/JS URL에 콘텐츠 해시를 붙인다. 최초 화면에서 외부 폰트·분석·IP 조회·미러 iframe을 자동 요청하지 않는다.
- 기존 프로젝트 행에서 양쪽 미러의 상태·저장 용량·RX/TX만 보여 준다. 측정 시각·진단·하위 저장소는 API에 유지한다. unavailable 표시는 양 언어 모두 **`—`**다.
- 홈페이지의 제한된 비로그인 계정이 양쪽 미러 JSON을 TLS 검증·크기/시간 제한·리다이렉트 거부 조건으로 읽고 `/status/mirrors.json`으로 제공한다. 브라우저는 같은 origin만 요청한다.
- aggregate timer와 JSON location의 cache/freshness 수정이 포함된다. 수집 JSON은 정적 웹 루트 밖에 두어 정적 업로드의 삭제 대상과 분리한다.
- 기존 Grafana 공개 링크를 유지하되, 프로젝트 관측값은 Grafana에 의존하지 않는다.

프런트엔드의 과거 배포/QA 기록과 최종 공개 페이지 관측은 존재하지만, PR #46 병합 후 자동 업로드는 실패했다. **현재 main 전체가 운영 정적 루트와 일치한다거나 모든 기능을 무보정 운영 브라우저에서 통과했다고 주장하지 않는다.**

## 3. 양쪽 미러 공통 적용

### 수집·동기화·용량

- `/opt/keimirror-telemetry/`의 수집 코드, 고정 설정, root-owned shim, 작업별 관측 상태, 저우선순위 용량 캐시, 공개 snapshot으로 분리했다.
- 동기화 스크립트는 검증된 실행 파일 토큰만 관측 shim으로 교체하는 방식이다. 원래 인수·환경·표준 입출력·락 FD·시그널 전달·종료 결과를 보존한다. upstream ftpsync의 단계 흐름을 관측 하나로 대체하지 않는다.
- PR #44는 live `/proc/$$/status`를 직접 반복 해석할 때 빈 SigIgn이 생길 수 있는 시작 문제를 snapshot 후 파싱하도록 수정했다. 전송 옵션이나 일정 변경이 아니다.
- 용량은 GNU du의 저장소별 할당 바이트이며 동기화 진행률·파일시스템 quota·서버 총 디스크가 아니다. 순차 저우선순위 스캔과 캐시를 사용한다.
- collector/sizes timer, 공개 GET/HEAD `/.well-known/keimirror-status.json`, 기존 로그를 보존하는 별도 개인정보 최소화 HTTP 집계 로그를 둔다.
- 기존 비활성 agent의 고정 성공값을 활성화하거나 동기화 성공을 만들어내지 않는다. 수집 실패와 실제 동기화 실패를 구별한다.

### 최근 백엔드 적용 해시

마지막 운영 readback에서 양쪽 모두 다음 **4개 대상 파일**이 PR #44 병합 소스에 묶인 manifest와 일치했다. 11월 1일 문서 작성 때도 보존된 actual/expected 해시를 해당 Git blob과 다시 대조했다. 이는 저장소 전체 또는 모든 상주 프로세스의 재로드를 증명하지 않는다.

| 설치 루트 기준 파일 | SHA-256 |
|---|---|
| `private_reasons.py` | `4418bc0acbbe74477a5ae4fbb6fb65f890f8e359cddd24409a473f5e943f4aca` |
| `rates.py` | `22e25d9ab1455e0c9a4ef8cbaee2af4823269a6af6cd84c5f009fa49af24bc8d` |
| `bin/rsync` | `26e6bfa529df4e9c0e5fee237ed3c4fb2c13cfb1c66916baed01c6aa4931da14` |
| `bin/rsync-ssl` | `24f792ae1432dc57b062a46f98cba2b33f08ff05d028196c97ac806fe39ce54f` |

소스 경로는 앞 두 파일이 `telemetry/`, shim은 `ops/telemetry/` 아래다. 설치 권한은 Python root:root 0644, shim root:root 0755이며 Git blob mode와 설치 실행권한은 다르다.

미러에 남아 있던 비실행 `aggregate.py`는 과거 정상 Git blob 출처가 확인된 파일로 유지했다. 홈페이지에서 실행되는 aggregator와 구별해야 하며, 이 잔존 파일을 이유로 전체 최신 main 동기화를 주장하거나 무단 변경으로 판단하지 않는다.

### 관측 속도의 정확한 의미

호환 필드명 `sync_rx_bps` / `sync_tx_bps`는 이제 순수 동기화 속도만 뜻하지 않는다. **origin HTTP의 live application bytes + 귀속된 upstream 동기화/rsync daemon 제공 TCP**를 합친 관측값이며 동기화 상태와 독립적이다. 실패한 동기화와 진행 중 다운로드가 공존할 수 있다.

- 인접 약 1초 표본의 실제 monotonic 간격을 사용한다. 이동 평균·15초/60초 평균이 아니다.
- HTTP와 TCP 양쪽 측정 조건 및 그룹의 모든 하위 저장소 측정 조건이 충족되어야 합계를 낸다. 누락은 `null`, 정상 관측된 무활동은 `0`이다.
- ownerless 활성 소켓은 무조건 무시하지 않는다. FD holder 복구·RPC 제외는 검증된 identity/namespace 근거에 한정하고 실패하면 미확인으로 유지한다.
- HTTP application bytes는 헤더·프레이밍 등을 포함하지만 TLS/TCP overhead나 재전송·최종 사용자 소비량을 뜻하지 않는다. TCP 표본은 서로 다른 전송 계층 의미를 갖고 재전송을 포함할 수 있다. 두 표본 사이의 짧은 연결을 놓칠 수 있다.
- `http_bytes_60s` / `rsync_bytes_60s`는 **완료 로그의 별도 진단 필드**다. 진행 중 다운로드의 live rate와 바꾸어 읽지 않는다. 세부 계약은 [telemetry-contract.md](telemetry-contract.md)를 따른다.

## 4. 서버별 차이와 실제 운영 확인

| 마지막 관측 항목 | 홈페이지 | Mirror 1 | Mirror 2 |
|---|---|---|---|
| nginx / 설정 검사 | active / 성공 | active / 성공 | active / 성공 |
| 주요 작업 | aggregate timer 활성 | collector·sizes timer 활성, collector 성공 | collector·sizes timer 활성, collector 성공 |
| PR #44의 위 4개 파일 | 미러용 대상 아님 | 해시 일치 | 해시 일치 |
| private RPC helper | 해당 없음 | active | inactive |
| HTTP 모듈 | 미러용 대상 아님 | 후보 모듈, 별도 장애 복구 후 운영 | 기존 모듈 유지 |
| 시각 동기화 상태 | NTP=no, NTPSynchronized=no | 동기화 yes | 동기화 yes |

상주 Mirror 1 helper는 최근 백엔드 교체에서 재시작하지 않았다. 디스크 파일 해시만으로 이미 import한 코드까지 새로 로드되었다고 하지 않는다.

### HTTP 모듈 식별

- Mirror 1 로드/파일 SHA-256: `a5035e055ece858073606d8e4952167286914eafb8704e2cfa29a2bc97e247e5`.
- Mirror 2 로드/파일 SHA-256: `700adce1c053f3a0b1accf590f77d1f79d68a574599b47025af65b0dea0d02f1`.
- 후보 패키지 SHA-256: `a057348974aa3ab2ae5297e02ddaac7b882d4c4534c285e2d6f649e69ca6ea4e`.
- 후보 패키지 버전 문자열 `0.1.0+nginx1.26.3-3+deb13u10`만으로 기존/후보 바이트를 구분하지 않는다. 패키지 해시 자체도 설치 증거가 아니다.
- PR #43의 내장 403/404/416 수정은 좁게 증명된 HTTP/1 경로만 다룬다. custom/internal error_page, 요청 body, HTTP/2 등의 불확실성을 없애지 않으며 HTTP/3 실전송 검증을 주장하지 않는다.

**양쪽 백엔드 갱신 완료와 양쪽 HTTP 모듈 업그레이드 완료는 다르다. Mirror 2의 새 HTTP 모듈은 미적용이다.**

## 5. HTTP re-exec 장애·복구·수정

1. 10월 30일 PR #45 기반 실제 transaction은 새 PID 파일 publication 중 metadata 변경과 일시적인 파일 부재로 실패했다.
2. 아직 WINCH 전이고 기존 32 worker가 남아 있는데 rollback이 old master에 HUP을 보냈다. Nchan worker slot assertion/abort/respawn이 발생했고 새 master의 worker는 안정적이었다.
3. 원래 transaction은 deadline을 초과했다. **commit도 성공 rollback도 아니며**, journal·원래 오류·deadline을 보존했다.
4. 별도 승인된 incident recovery가 old client worker의 자연 소멸 뒤 forward 완료했다. 새 master의 systemd 채택, 새 32 worker의 30회 identity 안정성, 보호 대상 69개 보존이 기록되었다. 이것은 원래 transaction의 성공으로 소급되지 않는다.
5. 초기 probe에는 회전 파일 내용 불일치와 기존 self-signed origin 인증서의 system-CA 검사 실패가 있었다. 최종 검증은 고정 내용과 baseline-pinned 인증서를 사용했다. 아래 공개 endpoint 검사에서의 정상 TLS 검증과 구별한다. Nchan 채널/메시지 연속성은 입증하지 않았다.
6. PR #46은 PID publication 및 typed child inventory churn만 기존 deadline 내 제한적으로 재시도한다. pre-WINCH에서는 원래 worker를 증명하고 불필요한 HUP을 보내지 않는다. 가능한 WINCH 이후 부분 drain이면 안전하지 않은 HUP/QUIT을 강행하지 않는다.
7. Nchan 설정·매핑은 production preflight에서 차단한다. PR 병합은 차단 해제, root staging 재승인, Mirror 2 적용 승인이 아니다.

기존 상세 증거는 [INCIDENT-RECOVERY.md](../ops/telemetry/http-reexec/INCIDENT-RECOVERY.md), [P2 source-only evidence](../ops/telemetry/http-reexec/PR46-P2-EVIDENCE.md), [re-exec 운영 경계](../ops/telemetry/http-reexec/README.md)를 참고한다. 이 파일들은 당시 source/manifest에 묶인 역사적 기록이며, 후속 병합 여부는 본 문서와 GitHub metadata를 함께 읽는다.

## 6. 시험·공개 관측·한계

### 소스와 격리 시험

- PR #46의 최종 로컬 비특권 suite 기록: **313 tests, OK, 3 skipped**, 추가 regression 17개 통과. 이를 313개 모두 실행·통과 또는 privileged-runtime 통과라고 바꾸어 쓰지 않는다.
- full-module fixture는 포착한 dynamic module 22개를 로드한 **2-worker** 환경에서 기존 old-HUP 결함과 수정 rollback 경로를 실행했다. systemd adoption 등은 시험 backend로 대체했으며 production apply 시험이 아니다.
- **32-worker fixture는 초기 inventory churn에서 중단**했다. 보완된 harness의 privileged 재실행 및 남은 local fixture 정리는 미완료다. 기존 실행 증거를 수정된 source의 실행 증거로 재사용하지 않는다.
- 백엔드 적용 기록에는 대상별 104개 검사와 native fixture/loopback/collector sandbox 검증이 있다. 대상 GCC 부재로 source-bound 로컬 Debian 13 사전 빌드 fixture를 사용했으며 서버에서 컴파일한 결과가 아니다.
- HTTP 모듈 자체의 별도 nginx integration 기록은 24 tests, no skips다. Nchan을 포함한 전체 운영 transaction 승인과 다르다.
- 백엔드 적용 후 양쪽에서 실제 HEAD·1바이트 206 검사, 제한된 Range 다운로드와 rsync list-only/속도 제한 전송을 수행했다. 전송은 의도적으로 완료 전에 중단했고 시험 프로세스·임시 목적지 부재를 재확인했다. 실제 daemon/held-file/socket identity 결합으로 rsync 시험의 Arch Linux 귀속을 확인했으나, 주변 운영 트래픽이 있으므로 프로젝트 총 HTTP/TX를 시험만의 정확한 증가량으로 주장하지 않는다. 이때의 화면 수치 검증은 QA 브라우저 시계를 test-only 보정한 조건이며, 아래 최종 무보정 결과와 별개다.

### 마지막 운영 관측

- 세 nginx의 설정 검사 성공, 두 표본에서 master/child identity 유지, 양쪽 collector `Result=success`/`ExecMainStatus=0`, 관련 timer 활성 상태를 확인했다. 해당 시점의 표본이지 장기 가용성 보장이 아니다.
- 홈페이지·양쪽 미러 root HTTP/HTTPS와 집계/원본 JSON의 최종 200 응답을 확인했다. HTTP redirect는 따라갔고 공개 HTTPS 인증서/호스트명 검증을 우회하지 않았다.
- 저장된 공개 trace를 재집계한 결과 **10회 요청, 서로 다른 generated_at 10개**, 프로젝트 RX/TX 관측값은 **null 8 · zero 323 · positive 69**였다. 이는 반복 표본의 값 개수이지 프로젝트 수나 사용자 수가 아니다. 연속적인 전 프로젝트 숫자 가용성을 요구하거나 입증한 결과가 아니다.
- 무보정 native browser는 `오래된 정보`, `RX/TX —`를 보였다. **무보정 실제 DOM의 숫자/0 표시 acceptance는 통과로 기록하지 않는다.** 이전 QA의 test-only 시계 보정 결과와 구별한다.
- 홈페이지 시계와 QA 브라우저 시계 차이 진단이 남아 있다. 제품의 집계 봉투 미래 허용은 시계 교정이 아니며 실제 시각 동기화 수정은 미실행이다.
- 당시 양쪽 Fyra Labs는 `failed`, `exit_code=5`, `last_success_at=null`이었다. 서비스/collector 정상과 모든 프로젝트 동기화 성공은 다르다. 여기서 실패 원인을 새로 확정하거나 현재도 동일하다고 주장하지 않는다.

## 7. GitHub 반영과 자동 배포 결과

- PR #46 reviewed head: `5cc75a8fdc5217f7196196510af404554a25ddaa`.
- 병합 커밋: `f1bebc85e26a7d81187da9dbb7aad8dfa1cabe2b`, tree: `aefe8a9dab9b8fa545975e2e4bf7abdec0ef6702`.
- [병합 전 CI 41080012084](https://github.com/me0kmin27/Keimirror/actions/runs/41080012084), [병합 후 CI 41174264049](https://github.com/me0kmin27/Keimirror/actions/runs/41174264049): 성공.
- [정적 배포 41174265031](https://github.com/me0kmin27/Keimirror/actions/runs/41174265031): validate 성공, **Prepare SSH 실패**, **Upload to server 생략**. 검증과 업로드 checkout은 같은 병합 SHA에 묶였다. CI 성공을 업로드 성공으로 보고하지 않는다.
- 정적 workflow는 `telemetry/`와 `ops/`를 설치하지 않는다. 미러 backend/module 적용은 별도 관리 절차다. SSH secrets를 수정하거나 접근 통제를 낮추어 실패를 우회하지 않았다.
- 이번 문서 전용 반영은 README/docs만 대상으로 하고 workflow를 변경하지 않는다. 기존 workflow가 모든 main PR 병합 시 정적 배포를 시도하므로, 보호 규칙을 확인한 뒤 일반 fast-forward main push로 문서만 게시한다. main push 검증 CI는 실행하되 운영 배포 workflow는 수동 호출하지 않는다.

## 8. 남은 항목과 재개 조건

- **Nchan 호환성:** 차단 유지. 모든 phase 복구 또는 별도 검토된 제한적 forward-only 설계의 증거가 필요하다. 모듈 제거·검증 생략으로 통과시키지 않는다.
- **32-worker runtime acceptance:** 기존 로컬 fixture의 identity-guarded 정리 승인과 수정 harness 실제 재실행 필요. 비특권 unit test로 대체하지 않는다.
- **Mirror 2 HTTP 교체:** 미실행. 위 안전 검증과 새 host-specific manifest/승인 전 적용하지 않는다.
- **홈페이지 시각 동기화:** 수정 승인 및 실제 시간/브라우저 검증 필요. 시간 차이의 과거 진단을 현재 정확도 보증으로 사용하지 않는다.
- **자동 정적 업로드:** SSH 준비 실패는 미해결. 소스 병합/문서 게시 완료와 별도로 관리한다.
- **프로젝트별 동기화 결과:** 실제 upstream 작업 결과에 따라 판단한다. 성공 표시를 만들기 위한 강제 실행·null의 0 변환은 하지 않는다.

문서 작성으로 위 항목을 실행·승인·해결하지 않았다. 마지막 기록의 failed rollout unit은 보존된 이력이며 실행 중 배포 프로세스와 같지 않다.

## 9. 운영 문서 탐색

- [데이터 계약·freshness·0/null](telemetry-contract.md)
- [기본 설치·검증·롤백 및 기능별 경계](telemetry-operations.md)
- [nginx live 모듈 빌드·제약](../ops/telemetry/nginx-live/README.md)
- [RPC 관측 제외 증명](rpc-exclusion-candidate.md)
- [비공개 진단](private-diagnostics.md)
- [TCP holder recovery](../telemetry/HOLDER_RECOVERY.md)
- [HTTP re-exec 및 차단 조건](../ops/telemetry/http-reexec/README.md)

GitHub에서 읽을 수 있는 PR/소스/시험 문서와, 당시 저장된 운영 readback·module mapping/hash·public trace·merge receipt를 대조해 작성했다. 비공개 원문 증거는 공개 저장소에 복사하지 않았으며, 본 문서의 운영 요약은 해당 날짜의 관측 기록으로 한정한다.
