# OpenClaw-Home

가족용 AI 에이전트. Telegram으로 모든 기능을 관리합니다.

## 기능

1. **Instagram 팔로워 트래킹** – 주기별 체크, 급등/급락 알림
2. **대학 입시 문서** – 2027 입시요강 다운로드, 새 문서 알림, RAG 기반 Q&A
3. **중국어↔한국어 번역** – 로컬 Ollama로 번역 (API 비용 없음)
4. **MES 게시판 모니터링** – 로그인 후 새 글/댓글 Telegram 알림

## 요구사항

- Python 3.10+
- macOS (Mac Studio 권장)
- Ollama (gemma3:27b, nomic-embed-text)

## 설치

```bash
cd openclaw-home
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Ollama 모델:

```bash
ollama pull gemma3:27b
ollama pull nomic-embed-text   # RAG용 임베딩
```

## 설정

```bash
cp config.example.yaml config.yaml
# config.yaml 수정: Telegram 토큰, allowed_user_ids 등
```

## 실행

### 로컬 (venv)

```bash
python main.py
```

### Docker (호스트 Python·패키지와 무관)

호스트에서 [Ollama](https://ollama.com/)를 띄운 뒤, `config.yaml`을 준비합니다.

```bash
cd openclaw-home
docker compose up -d --build
```

- 호스트의 **이 저장소 전체가 `./:/app` 으로 마운트**됩니다 (`config.yaml`, `data/` 등). 편집 후 재시작: `docker compose restart myagent`.
- **PTM 플랫폼** (`/git_pull`, `/shell`, `/dev_deploy` …): 호스트 `~/Documents/Work/PTM/ptm-platform`(또는 `PTM_PLATFORM_REPO`)을 컨테이너 **`/ptm-platform`** 으로 마운트하고, `OPENCLAW_PTM_PLATFORM_PATH` / `OPENCLAW_PTM_SHELL_CWD` 가 그 경로를 가리킵니다. (`config.yaml`의 `~/...` 는 컨테이너에서 `/root/...`로 풀리므로 Docker 에서는 이 마운트·환경 변수가 실제 작업 디렉터리입니다.)
- **PTMQuant** (`/ptmquant_pull`, `/ptmquant_build`): 호스트 `~/Documents/Work/PTM/PTMQuant`(또는 `PTMQUANT_REPO`)를 **`/PTMQuant`** 로 마운트, `OPENCLAW_PTMQUANT_GIT_PATH=/PTMQuant` (또는 `ptm.ptmquant_git_path`).
- 웹훅 포트는 기본 `9000` (`WEBHOOK_PORT` 환경변수로 변경 가능).
- 컨테이너 → 호스트 Ollama 접속은 기본적으로 `OLLAMA_BASE_URL=http://host.docker.internal:11434` 입니다. 다른 주소면 compose 실행 전에 `export OLLAMA_BASE_URL=...` 하거나 `docker-compose.yml`의 `environment`를 수정합니다.
- 호스트 Docker를 쓰는 명령(`/ptmquant_build` 등)을 위해 **`/var/run/docker.sock`** 이 마운트되어 있습니다. 이미지에 Docker CLI가 포함되어 있습니다.
- Compose **`name: openclaw-home`** 으로 프로젝트 이름이 고정되어, 호스트에서 띄운 스택과 봇 컨테이너(`/app`)에서의 `docker compose`가 같은 프로젝트로 묶입니다 (기존 `container_name` 충돌 방지).

로그 확인: `docker compose logs -f myagent` (서비스 이름)

이미지 갱신: `docker compose up -d --build`

## 사용법 (Telegram)

| 명령 | 설명 |
|------|------|
| `/start`, `/help` | 도움말 |
| `/translate`, `/tr [텍스트]` | 중국어↔한국어 번역 |
| `/insta [user_id]` | Instagram 팔로워 조회 |
| `/guide [대학명]` | 학교별 입시요강 요약 |
| `/ask [질문]` | 입시 PDF 기반 질문 |
| `/grade [과목] [점수]` | 성적 저장 |
| `/grade` | 합격 가능성 분석 |
| `/fetch` | 입시 PDF 수동 다운로드 |
| `/index` | RAG 인덱스 재구축 |

## 디렉터리 구조

```
openclaw-home/
├── config.yaml          # 텔레그램 토큰, API 키
├── main.py              # 에이전트 실행 및 텔레그램 핸들러
├── engine.py            # AI 루팅 및 도구 호출
├── rag.py               # RAG (입시 문서 Q&A)
├── tools/
│   ├── browser.py       # Playwright 기반 웹 스크래핑
│   ├── translator.py   # 중국어↔한국어 번역
│   ├── admission.py    # 입시 성적 비교, PDF 분석
│   └── mes_board.py    # MES 게시판 모니터링
├── skills/              # AI 역할 정의
│   ├── study_expert.md
│   └── code_assistant.md
└── data/
    ├── student_info.json
    ├── instagram_history.json
    ├── univ_cache/       # 다운로드한 대학 PDF
    └── vectorstore/     # RAG 벡터 DB
```

## 추가 스킬/도구

- `skills/`에 새 `.md` 파일 추가 → 에이전트 역할 확장
- `tools/`에 새 `.py` 모듈 추가 → `engine.py`에서 라우팅 및 호출
