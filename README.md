# PTM Agent

Mac Studio에서 도는 Telegram 봇. ptm-platform과 PTMQuant를 원격으로 pull·배포합니다.

런타임은 **호스트 venv**입니다. Docker는 쓰지 않습니다.

## 요구사항

- Python 3.10+
- macOS (Mac Studio)
- Docker Desktop (ptm-platform / PTMQuant 컨테이너용)
- 텔레그램 봇 토큰, `allowed_user_ids`

Ollama는 일반 채팅용으로 남아 있지만, 슬래시 메뉴에는 없습니다.

## 설치

```bash
cd openclaw-home
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
# config.yaml: token, allowed_user_ids, ptm.project_path / ptmquant_git_path
```

`config.yaml`은 토큰이 들어 있어 git에 올리지 않습니다.

## 실행

항상 **하나만** 띄웁니다. 두 개면 Telegram `getUpdates` Conflict가 납니다. 두 번째 프로세스는 `/tmp/ptm_agent.pid` 락으로 바로 종료됩니다.

```bash
pgrep -fl "main.py"
# 이미 있으면
kill -9 <PID>

cd ~/Work/MyAgent/openclaw-home
nohup ./venv/bin/python main.py > logs/agent.log 2>&1 &
```

`python main.py`가 아니라 **`./venv/bin/python`** 을 쓰세요 (pyenv면 `apscheduler` 등이 빠질 수 있음).

launchd는 `start.sh`를 사용합니다. 웹훅은 `:9000/webhook/ptm`.

## Telegram 명령

| 명령 | 설명 |
|------|------|
| `/start`, `/help` | 도움말 |
| `/ptmplatform_deploy` | ptm-platform `git pull` + 스마트 빌드 |
| `/ptmplatform_status` | git · 컨테이너 · 락 |
| `/ptmplatform_unlock` | ptm-platform 락 강제 해제 |
| `/ptmplatform_unlock quant` | PTMQuant 락 강제 해제 |
| `/ptmquant_deploy` | PTMQuant `git pull` + `./build.sh` |
| `/latest` | 두 저장소 최근 커밋 10개 |
| `/shell <명령>` | ptm-platform 디렉터리에서 쉘 (최대 15분) |

`/translate`, `/guide`, `/ask`, `/grade`, `/index`, `/fetch`, `/insta` 는 메뉴에 없습니다.

## 경로

`config.yaml`의 `ptm` 섹션 (네이티브 기본값):

- ptm-platform: `~/Documents/Work/PTM/ptm-platform`
- PTMQuant: `~/Documents/Work/PTM/PTMQuant`

## 디렉터리

```
openclaw-home/
├── config.yaml           # 토큰·경로 (gitignore)
├── config.example.yaml
├── main.py
├── tools/
│   ├── deploy_manager.py # 배포 락 · 스마트 빌드 · 상태
│   └── ptm.py            # git pull / 스크립트 / 커밋 로그
├── start.sh
└── logs/
```
