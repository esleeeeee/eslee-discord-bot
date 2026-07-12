# eslee Discord Bot

[➕ **eslee-bot을 내 Discord 서버에 추가하기**](https://discord.com/oauth2/authorize?client_id=1525689872621240442&scope=bot+applications.commands&permissions=2147576832)

이 고정 초대 링크는 공식 봇 애플리케이션을 가리킵니다. 한 번 서버에 추가하면 이후 코드가 업데이트되어도 다시 초대할 필요 없이 현재 배포된 최신 봇 기능을 사용합니다.

**언어:** [English](README.md) · 한국어

친구들과 쓰는 소규모 Discord 서버를 위한 공지 리마인드·금지어 관리 봇입니다. 공지 원본을 보존하면서 6시간마다 다시 노출하고, Poll을 복제하지 않아 기존 투표 결과와 참여 기록을 유지합니다. 이미지와 파일도 반복 업로드하지 않습니다.

## 주요 기능

- 메시지 우클릭 `Apps → 공지로 등록`
- `/공지 등록`, `/공지 목록`, `/공지 삭제`, `/공지 즉시전송`
- DB 기반 6시간 스케줄과 재시작 복구
- 짧은/긴 텍스트, 이미지, 파일, 혼합 첨부, Poll 리마인드
- Poll 원본 종료 시각 기준 남은 시간 표시
- 금지어 부분 문자열 검사, 영문 대소문자 무시, Unicode NFKC 처리
- `/금지어 일괄추가`로 쉼표 또는 줄바꿈 구분 최대 500개(총 6000자) 등록
- `/금지어 목록`은 일반 사용자를 포함한 서버 구성원 모두 사용 가능
- 새 메시지와 수정 메시지 검사
- 여러 금지어를 한 번에 감지하되 삭제·경고는 한 번만 수행
- 사용자 DM 우선 경고, 실패 시 약 5초짜리 채널 경고
- `/설정 로그채널` 관리자 감사 로그
- 원문을 저장하지 않는 개인정보 최소화 위반 기록

## 빠른 설치

Python 3.12 이상이 필요합니다.

```powershell
git clone https://github.com/esleeeeee/eslee-discord-bot.git
Set-Location eslee-discord-bot
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

`.env`의 `DISCORD_TOKEN`을 실제 봇 토큰으로 바꾼 뒤 실행합니다.

```powershell
python -m eslee_bot
```

## Discord Developer Portal 설정

1. 애플리케이션과 Bot 사용자를 생성합니다.
2. **Privileged Gateway Intents**에서 **Message Content Intent**를 켭니다.
3. OAuth2 초대 URL에 `bot`, `applications.commands` scope를 넣습니다.
4. 다음 권한만 부여합니다.

- View Channels
- Send Messages
- Manage Messages
- Read Message History
- Embed Links

Administrator 권한과 Attach Files 권한은 필요하지 않습니다. Members Intent도 사용하지 않습니다.

개발 중에는 `.env`의 `DISCORD_DEV_GUILD_ID`에 테스트 서버 ID를 넣으면 명령이 해당 서버에 빠르게 동기화됩니다. 비워 두면 global sync를 사용하며 Discord 반영에 시간이 걸릴 수 있습니다.

## 환경변수

```env
DISCORD_TOKEN=replace-with-your-bot-token
DISCORD_DEV_GUILD_ID=
DATABASE_URL=sqlite+aiosqlite:///./data/eslee_bot.db
LOG_LEVEL=INFO
SCHEDULER_POLL_SECONDS=60
```

`.env`는 Git에서 제외됩니다. 토큰이 노출되면 Developer Portal에서 즉시 재발급하세요.

## Docker

```powershell
Copy-Item .env.example .env
# .env 수정 후
docker compose up --build -d
docker compose logs -f bot
```

`bot-data` named volume이 컨테이너 `/app/data`에 마운트되어 SQLite DB가 유지됩니다. non-root 컨테이너와 host bind mount 사이의 소유권 문제도 피합니다.

## 검사

```powershell
python -m ruff check .
python -m pytest
```

## 운영 주의사항

- 관리 명령은 서버 소유자 또는 Discord Administrator 권한 보유자만 실행할 수 있습니다.
- 봇 자체에는 Administrator를 부여하지 마세요.
- 설정한 로그 채널에는 위반 메시지 원문이 표시되므로 관리자만 접근하게 하세요.
- DB 위반 기록에는 원문이 저장되지 않고 서버·사용자·채널 ID와 감지 단어만 저장됩니다.
- v1은 스키마 마이그레이션 도구 없이 시작 시 누락 테이블을 생성합니다. 모델 변경 전에는 DB를 백업하세요.
- SQLite 파일 하나를 공유하는 다중 봇 프로세스 실행은 지원하지 않습니다. 한 인스턴스만 실행하세요.

전체 구조, 명령 표, 보안 및 설계 설명은 [README.md](README.md)를 참고하세요.
