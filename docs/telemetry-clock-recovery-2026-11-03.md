# 2026-11-03 전송속도 표시 중단: 시계 동기화 복구

## 재현과 원인

미러 수집기와 홈페이지 집계기는 실행 중이었고, 공개 원본의 측정 시각과 실제 RX/TX 숫자는 계속 바뀌었다. 그러나 홈페이지 `/status/mirrors.json`은 양쪽 모두 `available:false`를 내보냈다.

집계 코드의 `published_at - snapshot.collected_at <= 5` 판정과 실제 응답을 대조했다. 홈페이지 서버에는 시간 동기화 서비스가 없었고 `NTP=no`, `NTPSynchronized=no`였다. 홈페이지의 빠른 시계에 수집/전달 시간이 더해져 원본이 매번 5초를 초과한 자료로 판정되었다. 수집 정지나 JSON 캐시 고정이 아니었다. 정상 TLS 검증을 사용하는 외부 HTTP Date 비교에서도 홈페이지의 상대 시각 차이가 관측되었으며, HTTP Date의 초 단위 정밀도는 정밀 NTP 오차 측정과 구별했다.

별도로 검증용 Hermes 호스트도 시간 동기화 서비스가 없고 약 100초 느렸다. 이는 QA 브라우저에서 미래 표본 거부를 일으키는 별개 문제였다. 이번에는 브라우저 `Date.now()`를 덮어쓰지 않고 사용자 승인에 따라 두 시스템의 실제 시계를 동기화했다.

## 승인된 적용 범위

- 홈페이지와 Hermes에 Debian 13 정식 `chrony 4.6.1-3+deb13u2` 설치. 각 대상에서 사전 simulation은 chrony 하나 신규 설치, 다른 패키지 업그레이드·제거 없음이었다.
- 기존 두 미러의 실제 chrony 설정을 읽고, Mirror 2의 `0.kr.pool.ntp.org`~`3.kr.pool.ntp.org` 및 `iburst` 구성을 채택했다.
- [적용한 chrony.conf](../ops/time/chrony.conf)는 두 대상에 동일하며 SHA-256은 `c5e8f6903e6970714a3ce6ea64de16127a50db86917b4e03ca96429ba26b028a`다. 기존 Debian 기본 설정을 owner-only 백업 후 구문 검사·교체·chrony 활성화/재시작을 수행했다.
- `rtcsync`, `makestep 1 3`, 기본 drift/log/leap 설정은 유지했다. 최초 설치 직후 패키지 기본 Debian pool로 서비스가 시작된 뒤, 한국 pool 설정으로 교체하고 재시작했다. 최종 유효 설정에는 Debian pool이 없다.
- sourcedir/confdir는 기존 미러와 같이 유지한다. 향후 DHCP/source drop-in이 추가될 수 있으므로 파일만 보지 말고 `chronyd -p`와 `chronyc sources`의 실제 소스를 확인한다. keyfile 경로는 유지하지만 인증 키 내용은 복사·공개하지 않았다.
- 미러 1·2의 chrony·수집기·HTTP 모듈·동기화 작업은 변경하지 않았다. nginx 재시작, SSH 키·접근 통제 변경도 하지 않았다.

## 실제 복구 확인

- 홈페이지와 Hermes 모두 `chronyc waitsync 30 0.1 0 2` 성공, `Leap status: Normal`, `NTP=yes`, `NTPSynchronized=yes`, chrony 활성·부팅 자동 시작을 확인했다. 초기 source 선택은 동기화 성립을 증명하며 장기 주파수 추정 수렴이나 SLA를 뜻하지 않는다.
- 시간 수정만으로 공개 집계의 양쪽 `available`이 다시 true가 되었다. 보존된 12회 연속 공개 관측에서 양쪽 모두 true, 집계 시각 전진, 양쪽의 실제 양수 속도와 0을 확인했다. 일부 원본 속도 null은 그대로 보존했다.
- 시계 override와 payload 변조 없이 실제 운영 페이지를 확인했다. foreground 탭의 RX/TX가 양쪽에서 갱신되고 `0 B/s`와 양수 속도가 표시되었다. background 탭의 의도된 polling pause는 장애와 구별했다.
- freshness 유효기간, schema, TCP/HTTP 측정 조건을 느슨하게 바꾸지 않았다. 시간 교정 전의 10월 검증 한계는 [당시 기록](operations-history-2026-10.md)에 보존한다.

## Stale 표시 제거

이번 프런트엔드 변경은 `Stale / 오래된 정보` 배지를 제거하는 별도 표시 수정이다. 오래되거나 사용할 수 없는 스냅샷의 상태 배지를 숨기고, 유효한 새 스냅샷이 오면 실제 동기화 상태를 다시 표시한다. 측정 만료와 null 처리는 유지하므로 오래된 양수 속도를 유지하거나 알 수 없는 값을 0/완료로 바꾸지 않는다. 서버 시간 복구와 UI 변경은 서로 다른 검증 대상으로 취급한다.

## 재발 점검

```sh
systemctl is-enabled chrony
systemctl is-active chrony
timedatectl show -p NTP -p NTPSynchronized
chronyc tracking
chronyc sources -n
chronyd -p
```

프로젝트 전체가 동시에 숫자를 잃으면 원본 timestamp/값 → 집계 `available`/timestamp → native 브라우저 시각을 비교한다. 장비의 시간 서비스 부재를 UI expiry 확대나 브라우저 시계 보정으로 가리지 않는다. 수집값 null 하나는 이번 전체 표시 중단과 다른 문제이며 자동으로 수집 실패로 분류하지 않는다.

정적 홈페이지 업로드는 chrony 설정을 적용하지 않는다. `ops/time/chrony.conf`는 이번 명시적 관리 변경의 추적용이며 다른 서버에 일괄 설치하라는 지시가 아니다.
