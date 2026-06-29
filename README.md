# Keimirror

Keimirror는 Keiverse가 대한민국 구미에서 운영하는 공개 오픈소스 미러 안내 허브입니다. `keiminem.com` 웹 페이지에서 `mirror.keiminem.com`과 `mirror2.keiminem.com`의 접속 정보, 서버 사양, 지원 프로젝트 현황, 운영 정책을 한곳에 정리합니다.

## 현재 상태

- 웹 허브는 모듈화된 정적 HTML, CSS, JavaScript로 구성되어 별도 빌드 과정 없이 배포할 수 있습니다.
- 미러 1과 미러 2는 각각 독립 서버로 운영하며, HTTP, HTTPS, rsync 엔드포인트를 제공합니다.
- 두 미러 모두 DDoS 완화를 위한 CDN을 사용하되, 파일은 원본 미러에서 직접 제공되도록 CDN 캐싱을 사용하지 않습니다.
- 한국어와 영어 UI를 지원하며, `?lang=ko` 쿼리 또는 언어 전환 버튼으로 표시 언어를 바꿀 수 있습니다.
- 자동, 라이트, 다크 테마 전환, 접속자 IP 확인, GitHub Sponsors 후원 안내 기능을 제공합니다.
- 검색 엔진 수집을 위한 메타 태그, canonical, hreflang, JSON-LD, `robots.txt`, `sitemap.xml`, 파비콘 세트를 포함합니다.
- PR 병합 시 self-hosted runner에서 rsync로 운영 서버에 배포하는 GitHub Actions 워크플로를 포함합니다.

## 운영 목표

- 웹 서버, 미러 1, 미러 2를 각각 분리해 장애 영향을 줄이고 유지보수를 단순화합니다.
- 웹 서버는 Keimirror 소개, 운영 정책, 미러 접속 정보, 서버 프로필, 지원 프로젝트 현황을 제공합니다.
- 미러 1은 `https://mirror.keiminem.com/`에서 fancyindex 기반 기본 파일 목록을 제공합니다.
- 미러 2는 `https://mirror2.keiminem.com/`에서 fancyindex 기반 기본 파일 목록을 제공합니다.
- rsync 동기화가 필요한 사용자를 위해 `rsync://rsync.keiminem.com/` 및 `rsync://rsync2.keiminem.com/` 정보를 함께 안내합니다.

## 주요 기능

- **컴포넌트 기반 정적 페이지**: `index.html`은 레이아웃 자리만 두고, 실제 섹션과 카드는 `components/` 하위 HTML을 `fetch()`로 불러옵니다.
- **미러 디렉터리 미리보기**: 상단 미러 버튼을 누르면 히어로 영역에서 선택한 미러의 디렉터리 목록을 iframe으로 표시하고, 새 탭 열기 링크도 제공합니다.
- **다국어 지원**: `assets/js/i18n.js`에서 영어와 한국어 문자열을 관리하며, `data-i18n` 속성을 기준으로 문구를 교체합니다.
- **테마 전환**: 자동, 라이트, 다크 테마를 순환하며 사용자의 선택을 `localStorage`에 저장합니다.
- **접속자 IP 확인**: 사용자가 버튼을 누를 때 `api.ipify.org`를 호출해 현재 클라이언트 IP를 표시합니다.
- **운영 정보 카드**: 환영 문구, 면책 조항, 문의, 서버 프로필, 미러 접속 정보, 프로젝트 현황, Eliv 후원 감사, GitHub Sponsors 후원 안내를 카드 단위로 제공합니다.
- **후원 CTA**: 헤더 Donate 버튼과 푸터 후원 링크를 통해 GitHub Sponsors 팝업 및 후원 페이지로 연결합니다.
- **SEO 기본 구성**: Open Graph, Twitter Card, JSON-LD, canonical, hreflang, sitemap, robots 설정을 포함합니다.
- **배포 자동화**: PR이 `main` 브랜치에 병합되면 self-hosted runner가 SSH 키를 준비하고 rsync로 운영 경로를 동기화합니다.

## 파일 구성

```text
.
├── index.html
├── robots.txt
├── sitemap.xml
├── .github/
│   ├── FUNDING.yml
│   └── workflows/
│       ├── Test SSH Connection.yml
│       └── deploy-on-pr-merge.yml
├── assets/
│   ├── css/styles.css
│   ├── favicon/
│   ├── img/
│   └── js/
│       ├── app.js
│       └── i18n.js
├── components/
│   ├── cards/
│   └── sections/
├── LICENSE
└── README.md
```

- `.github/FUNDING.yml`: GitHub Sponsors 계정 정보를 제공해 저장소 후원 버튼을 활성화합니다.
- `.github/workflows/deploy-on-pr-merge.yml`: `main` 대상 PR 병합 완료 시 self-hosted runner에서 운영 서버로 정적 파일을 rsync 배포합니다.
- `.github/workflows/Test SSH Connection.yml`: 배포용 SSH 시크릿과 원격 경로 접근성을 수동으로 점검하는 워크플로입니다.
- `index.html`: 메타데이터, SEO 태그, 전역 스크립트와 섹션 컴포넌트 자리 표시자를 관리하는 진입 HTML입니다.
- `assets/css/styles.css`: 사이트 전체 스타일, 반응형 레이아웃, 테마별 색상, 카드 UI, 미러 iframe 영역을 관리합니다.
- `assets/js/app.js`: 컴포넌트 로딩, 중첩 컴포넌트 처리, 테마 전환, 언어 전환, 미러 목록 토글, 접속자 IP 조회, 후원 팝업 열기/닫기를 담당합니다.
- `assets/js/i18n.js`: 영어와 한국어 번역 문자열과 후원 안내 문구를 관리합니다.
- `assets/img/`: 라이트 모드와 다크 모드용 Keiverse 로고 이미지를 보관합니다.
- `assets/favicon/`: 브라우저, Apple Touch Icon, Android, Microsoft Tile용 파비콘과 manifest 파일을 보관합니다.
- `components/sections/`: 헤더, 히어로, 본문 컬럼, 사이드바, 푸터 등 레이아웃 섹션 단위 HTML을 보관합니다.
- `components/cards/`: 환영 문구, 면책 조항, 문의, 서버 프로필, 접속자 IP, 미러 접속 정보, 프로젝트 현황, Eliv 후원 안내, 푸터 공지 등 카드 단위 HTML을 보관합니다.
- `robots.txt`: 검색 엔진 크롤링 허용 범위와 sitemap 위치를 안내합니다.
- `sitemap.xml`: `keiminem.com` 기본 페이지와 한국어 대체 URL 정보를 제공합니다.
- `README.md`: 프로젝트 목적, 현재 상태, 작업 과정, 배포 절차를 기록합니다.
- `LICENSE`: 프로젝트 라이선스입니다.

## 컴포넌트 구조

`index.html`은 `data-component` 속성을 가진 빈 컨테이너를 선언합니다. `assets/js/app.js`는 해당 경로의 HTML을 불러와 삽입하고, 삽입된 HTML 안에 다시 `data-component`가 있으면 중첩 컴포넌트까지 재귀적으로 로딩합니다.

현재 레이아웃 흐름은 다음과 같습니다.

1. `components/sections/header.html`
   - 로고, 미러 1/2 버튼, Donate 버튼, 언어 전환, 테마 전환을 제공합니다.
2. `components/sections/hero.html`
   - 서비스 제목, 설명, 미러 디렉터리 iframe 패널을 제공합니다.
3. `components/sections/overview.html`
   - `welcome`, `disclaimer`, `contact`, `server-profile` 카드를 포함합니다.
4. `components/sections/sidebar.html`
   - `visitor-ip`, `mirror-access`, `project-status`, `eliv-thanks` 카드를 포함합니다.
5. `components/cards/footer-notice.html`
   - Keiverse 미러 네트워크와 krfoss 참여 안내, GitHub Sponsors 후원 CTA와 팝업을 제공합니다.
6. `components/sections/footer.html`
   - 저작권 및 운영 문구를 제공합니다.

새 섹션이나 카드를 추가할 때는 `components/` 하위에 HTML 파일을 만들고, 포함할 위치의 `data-component` 경로만 추가하면 됩니다. 다국어 문구가 필요한 요소에는 `data-i18n` 키를 지정하고 `assets/js/i18n.js`의 `en`, `ko` 객체에 같은 키를 추가합니다.

## 미러 및 프로젝트 정보

### 접속 엔드포인트

| 구분 | 미러 1 | 미러 2 |
| --- | --- | --- |
| HTTPS | `https://mirror.keiminem.com/` | `https://mirror2.keiminem.com/` |
| HTTP | `http://mirror.keiminem.com/` | `http://mirror2.keiminem.com/` |
| RSYNC | `rsync://rsync.keiminem.com/` | `rsync://rsync2.keiminem.com/` |

### 서버 프로필

| 항목 | 미러 1 | 미러 2 |
| --- | --- | --- |
| CPU | Intel Xeon E5-2650 v2, 2소켓 | Intel Xeon E5-2696 v4 |
| RAM | 128 GB | 32 GB |
| Storage | 10 TB | 10 TB |
| Network | WAN 2회선, 각 1Gbps, 총 2Gbps | WAN 2회선, 각 1Gbps, 총 2Gbps |
| Location | 대한민국 구미 | 대한민국 구미 |
| CDN | DDoS 완화 용도, 캐싱 미사용 | DDoS 완화 용도, 캐싱 미사용 |

### 지원 프로젝트 현황

- 공식 미러: Arch Linux Tier 1, Gentoo, Kali Linux, CachyOS, Artix, OPNsense
- 커뮤니티 미러: EndeavourOS, Fyra Labs, Manjaro, Proxmox

## 배포 절차

1. 웹 서버의 정적 웹 루트에 저장소의 모든 파일과 디렉터리를 배치합니다.
2. 웹 서버 도메인 `keiminem.com`이 `index.html`을 기본 페이지로 제공하도록 설정합니다.
3. `assets/` 및 `components/` 경로가 같은 도메인에서 정적 파일로 접근 가능한지 확인합니다.
4. 브라우저에서 컴포넌트 로딩, 언어 전환, 테마 전환, 미러 iframe 토글, 후원 팝업이 정상 동작하는지 확인합니다.
5. `robots.txt`와 `sitemap.xml`이 루트 경로에서 접근 가능한지 확인합니다.
6. 미러 1 서버는 `mirror.keiminem.com` 도메인에서 fancyindex 기반 기본 디렉터리 목록을 제공합니다.
7. 미러 2 서버는 `mirror2.keiminem.com` 도메인에서 fancyindex 기반 기본 디렉터리 목록을 제공합니다.
8. HTTP, HTTPS, rsync 엔드포인트와 CDN 캐싱 정책이 운영 의도와 일치하는지 확인합니다.
9. GitHub Actions 배포를 사용할 경우 `DEPLOY_SSH_KEY`, `DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_USER`, `DEPLOY_PATH` 시크릿을 설정하고, 필요 시 `Test SSH Connection` 워크플로로 연결을 먼저 확인합니다.

## 로컬 확인

브라우저에서 `file://`로 접근하면 컴포넌트 `fetch()`가 제한될 수 있으므로 로컬 정적 서버로 확인합니다.

```bash
python3 -m http.server 8000
```

이후 `http://localhost:8000/`에서 페이지를 확인합니다.

권장 확인 항목은 다음과 같습니다.

- 페이지 진입 시 헤더, 히어로, 본문 카드, 사이드바 카드, 푸터가 모두 표시되는지 확인합니다.
- 미러 1/2 버튼을 눌렀을 때 iframe 패널이 열리고 다시 누르면 닫히는지 확인합니다.
- 언어 전환 버튼으로 영어와 한국어 문구가 자연스럽게 바뀌는지 확인합니다.
- 테마 버튼으로 자동, 라이트, 다크 모드가 순환되는지 확인합니다.
- Donate 버튼과 푸터 후원 링크가 GitHub Sponsors 팝업 또는 후원 페이지로 자연스럽게 연결되는지 확인합니다.
- 접속자 IP 확인 버튼이 네트워크 가능 환경에서 정상 응답을 표시하는지 확인합니다.

## 작업 과정 기록

앞으로의 변경 작업은 이 섹션에 날짜별로 기록합니다.

### 2026-06-29

1. GitHub Sponsors 후원 CTA를 README의 현재 상태, 주요 기능, 컴포넌트 구조, 로컬 확인 항목에 반영했습니다.
2. iframe 기반 후원 위젯 대신 저장소 네이티브 버튼, 헤더 Donate 버튼, 푸터 후원 링크, 모달형 후원 안내로 구성된 현재 UI를 문서화했습니다.
3. `.github/FUNDING.yml`과 배포/SSH 점검 GitHub Actions 워크플로를 파일 구성 및 배포 절차에 추가했습니다.
4. PR 병합 후 self-hosted runner가 SSH와 rsync로 운영 서버를 동기화하는 배포 흐름과 필요한 시크릿을 명시했습니다.

### 2026-06-19

1. README를 현재 구현 상태 기준으로 재정리했습니다.
2. 현재 상태, 주요 기능, 컴포넌트 구조, 미러 엔드포인트, 서버 프로필, 지원 프로젝트 현황을 별도 섹션으로 분리했습니다.
3. SEO 파일, 파비콘, 로고, robots, sitemap 등 현재 저장소에 포함된 정적 리소스 설명을 파일 구성에 반영했습니다.
4. 배포 절차와 로컬 확인 항목을 실제 컴포넌트 로딩 방식에 맞춰 보강했습니다.

### 2026-06-16

1. 한국어 번역 문구의 문장 흐름을 점검하고, 어색하거나 끊어지는 표현을 자연스럽게 다듬었습니다.
2. 용어와 띄어쓰기를 정리해 `로드 밸런싱`, `면책 조항`, `한곳`처럼 일관된 표기를 적용했습니다.
3. README의 작업 내용과 문장 구성을 정리해 프로젝트 설명, 파일 구성, 배포 절차를 더 명확하게 읽을 수 있도록 개선했습니다.

### 2026-06-15

1. 프로젝트 목적을 `mirror.keiminem.com` 및 `mirror2.keiminem.com` 통합 안내 페이지로 정리했습니다.
2. `index.html` 기반의 정적 HTML 페이지를 추가했습니다.
3. 미러 1과 미러 2가 각각 fancyindex 기반 기본 목록을 제공하고, 웹 서버가 두 미러로 연결하는 운영 구조를 문서화했습니다.
4. `index.html`을 레이아웃 전용 진입점으로 재구성하고, 섹션과 카드 HTML, CSS, JavaScript, 번역 데이터를 각각 별도 파일로 분리했습니다.
