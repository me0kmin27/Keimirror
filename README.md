# Keimirror

Keimirror는 `mirror.keiminem.com`과 `mirror2.keiminem.com`을 한 곳에서 안내하는 정적 HTML 허브 페이지입니다.

## 목표

- 웹서버, 미러 1, 미러 2를 각각 별도 서버로 운영합니다.
- 웹서버는 `index.html`만 제공하며 두 미러로 이동할 수 있는 링크를 제공합니다.
- 미러 1은 `https://mirror.keiminem.com/`에서 fancyindex 기본 파일 목록만 제공합니다.
- 미러 2는 `https://mirror2.keiminem.com/`에서 fancyindex 기본 파일 목록만 제공합니다.

## 파일 구성

- `index.html`: 두 미러 링크와 운영 구조를 안내하는 단일 HTML 페이지입니다.
- `README.md`: 프로젝트 목적, 작업 과정, 배포 절차를 기록합니다.
- `LICENSE`: 프로젝트 라이선스입니다.

## 작업 과정 기록

앞으로의 변경 작업은 이 섹션에 날짜별로 기록합니다.

### 2026-06-15

1. 프로젝트 목적을 `mirror.keiminem.com` 및 `mirror2.keiminem.com` 통합 안내 페이지로 정리했습니다.
2. HTML만 사용하는 `index.html`을 추가했습니다.
3. 미러 1과 미러 2가 각각 fancyindex 기본 리스트를 제공하고, 웹서버가 두 미러로 연결한다는 운영 구조를 문서화했습니다.

## 배포 절차

1. 웹서버의 정적 웹 루트에 `index.html`을 배치합니다.
2. 웹서버 도메인이 해당 HTML 파일을 기본 페이지로 제공하도록 설정합니다.
3. 미러 1 서버는 `mirror.keiminem.com` 도메인에서 fancyindex 기본 디렉터리 목록을 제공합니다.
4. 미러 2 서버는 `mirror2.keiminem.com` 도메인에서 fancyindex 기본 디렉터리 목록을 제공합니다.
5. 웹서버에서 두 미러 링크가 정상적으로 열리는지 확인합니다.

## 로컬 확인

정적 HTML 파일이므로 별도 빌드 과정은 없습니다. 로컬에서는 브라우저로 `index.html`을 열어 확인할 수 있습니다.
