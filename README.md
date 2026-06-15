# Keimirror

Keimirror는 `mirror.keiminem.com`과 `mirror2.keiminem.com`을 한 곳에서 안내하는 정적 HTML 허브 페이지입니다.

## 목표

- 웹 서버, 미러 1, 미러 2를 각각 별도 서버로 운영합니다.
- 웹 서버는 모듈화된 정적 파일과 두 미러로 이동할 수 있는 링크를 제공합니다.
- 미러 1은 `https://mirror.keiminem.com/`에서 fancyindex 기본 파일 목록을 제공합니다.
- 미러 2는 `https://mirror2.keiminem.com/`에서 fancyindex 기본 파일 목록을 제공합니다.

## 파일 구성

- `index.html`: 메타데이터와 전체 레이아웃 자리 표시자만 관리하는 진입 HTML입니다.
- `assets/css/styles.css`: 사이트 전체 스타일과 반응형 레이아웃을 관리합니다.
- `assets/js/app.js`: 섹션 및 카드 컴포넌트 로딩, 테마 전환, 언어 전환, 접속자 IP 조회를 담당합니다.
- `assets/js/i18n.js`: 영어와 한국어 번역 문자열을 관리합니다.
- `components/sections/`: 헤더, 히어로, 본문 컬럼, 사이드바, 푸터 등 레이아웃 섹션 단위 HTML을 보관합니다.
- `components/cards/`: 환영 문구, 면책조항, 서버 프로필, 접속자 IP, 미러 접속 정보 등 카드 단위 HTML을 보관합니다.
- `README.md`: 프로젝트 목적, 작업 과정, 배포 절차를 기록합니다.
- `LICENSE`: 프로젝트 라이선스입니다.

## 모듈화 구조

`index.html`은 `data-component` 속성으로 섹션 컴포넌트를 불러오고, 각 섹션은 필요에 따라 카드 컴포넌트를 다시 불러옵니다. 카드 추가, 삭제, 교체가 필요할 때는 `components/cards/`에 파일을 추가하거나 섹션 파일의 `data-component` 항목만 조정하면 됩니다.

## 작업 과정 기록

앞으로의 변경 작업은 이 섹션에 날짜별로 기록합니다.

### 2026-06-15

1. 프로젝트 목적을 `mirror.keiminem.com` 및 `mirror2.keiminem.com` 통합 안내 페이지로 정리했습니다.
2. `index.html` 기반의 정적 HTML 페이지를 추가했습니다.
3. 미러 1과 미러 2가 각각 fancyindex 기본 목록을 제공하고, 웹 서버가 두 미러로 연결하는 운영 구조를 문서화했습니다.
4. `index.html`을 레이아웃 전용 진입점으로 재구성하고, 섹션과 카드 HTML, CSS, JavaScript, 번역 데이터를 각각 별도 파일로 분리했습니다.

## 배포 절차

1. 웹 서버의 정적 웹 루트에 저장소의 모든 정적 파일과 디렉터리를 배치합니다.
2. 웹 서버 도메인이 `index.html`을 기본 페이지로 제공하도록 설정합니다.
3. `assets/` 및 `components/` 경로의 정적 파일이 같은 도메인에서 접근 가능한지 확인합니다.
4. 미러 1 서버는 `mirror.keiminem.com` 도메인에서 fancyindex 기본 디렉터리 목록을 제공합니다.
5. 미러 2 서버는 `mirror2.keiminem.com` 도메인에서 fancyindex 기본 디렉터리 목록을 제공합니다.
6. 웹 서버에서 두 미러 링크와 컴포넌트 로딩이 정상적으로 동작하는지 확인합니다.

## 로컬 확인

브라우저에서 `file://`로 접근하면 컴포넌트 `fetch()`가 제한될 수 있으므로 로컬 정적 서버로 확인합니다.

```bash
python3 -m http.server 8000
```

이후 `http://localhost:8000/`에서 페이지를 확인합니다.
