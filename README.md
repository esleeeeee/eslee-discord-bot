# eslee Discord Bot

[➕ **eslee-bot을 내 Discord 서버에 추가하기**](https://discord.com/oauth2/authorize?client_id=1525689872621240442&scope=bot+applications.commands&permissions=2147576832)

위 링크는 공식 봇 애플리케이션으로 연결되는 고정 초대 링크입니다. 한 번 서버에 추가하면 기능이 업데이트될 때마다 다시 초대할 필요 없이 현재 실행 중인 최신 버전을 그대로 사용합니다.

**문서 언어:** 한국어 · [English](README.en.md)

Discord에서 중요한 공지를 올려도 대화가 이어지면 금방 위로 밀려납니다. 고정 메시지는 직접 찾아봐야 해서 특히 모바일에서는 놓치기 쉽고, 투표나 첨부파일이 포함된 공지를 반복해서 올리면 투표 결과가 나뉘거나 같은 파일이 계속 재업로드되는 문제가 생깁니다.

`eslee Discord Bot`은 이런 불편을 해결하기 위해 만든 소규모 서버용 관리 봇입니다. 중요한 메시지는 원본을 그대로 보존한 채 6시간마다 다시 알려주고, 서버에서 사용하면 안 되는 단어는 새 메시지와 수정된 메시지에서 실시간으로 감지합니다. 선택한 채널의 하루 대화는 Gemini로 요약해 다음 날 자동 리포트로 남길 수 있습니다.

## 공지가 대화에 묻히지 않게 합니다

관리자는 이미 작성된 메시지를 우클릭한 뒤 `Apps → 공지로 등록`을 선택할 수 있습니다. 새 공지를 직접 작성하고 싶다면 `/공지 등록`을 사용하면 됩니다. 등록 직후 첫 번째 리마인드가 전송되고, 이후에는 데이터베이스에 저장된 일정에 따라 6시간마다 공지가 다시 노출됩니다.

리마인드가 새로 올라올 때는 이전 리마인드만 정리합니다. 원본 공지 메시지는 삭제하거나 고정하지 않으며, 봇이 재시작되더라도 다음 전송 시각이 데이터베이스에 남아 있기 때문에 일정이 처음부터 다시 시작되지 않습니다. 오랫동안 봇이 꺼져 있었더라도 밀린 알림을 한꺼번에 보내지 않고 공지마다 한 번만 다시 알려준 뒤 다음 정상 주기로 돌아갑니다.

```text
원본 공지 등록
      ↓
즉시 첫 리마인드 전송
      ↓
DB에 다음 전송 시각 저장
      ↓
6시간마다 이전 리마인드 교체
```

짧은 글은 리마인드 안에서 바로 읽을 수 있고, 긴 글은 미리보기와 원본 링크가 표시됩니다. 이미지와 파일은 다시 업로드하지 않고 원본 첨부파일을 안내하므로 같은 파일이 채널에 반복해서 쌓이지 않습니다.

### Discord 투표도 기존 결과를 유지합니다

투표 공지는 새로운 Poll로 복제하지 않습니다. Poll을 복제하면 기존 참여 기록과 결과가 분리되기 때문입니다. 대신 원본 투표의 질문, 진행 상태, 실제 종료 시각을 읽어 리마인드에 보여주고 사용자를 원본 투표로 안내합니다.

```text
📊 투표 공지 리마인드

언제 만날까요?
상태: 진행 중
남은 시간: 25시간 30분

[원본 투표에서 참여하기]
```

종료 시각이 지나면 `종료됨`으로 표시합니다. 다중 선택 Poll에서는 단순 득표수 합계가 실제 참여자 수와 다를 수 있으므로, 정확하게 계산할 수 없는 참여자 수는 추측해서 보여주지 않습니다.

## 금지어를 실시간으로 감지합니다

서버 관리자는 `/금지어 추가`로 단어를 하나씩 등록하거나 `/금지어 일괄추가`에 쉼표 또는 줄바꿈으로 구분된 목록을 넣어 최대 500개까지 한 번에 등록할 수 있습니다. 영문은 대소문자를 구분하지 않으며 Unicode 문자를 정규화해서 비교합니다.

기본 규칙은 부분 문자열 포함입니다. 예를 들어 `사과`를 금지어로 등록하면 `사과`, `청사과`, `사과나무`가 모두 감지됩니다. 한 메시지에 여러 금지어가 들어 있어도 메시지 삭제와 사용자 경고는 한 번만 수행하고, 감지된 모든 단어를 함께 기록합니다.

등록된 금지어를 다시 입력하지 않아도 흔한 우회 표기를 함께 막습니다. `주식`이 등록되어 있다면 `주.식`, `주123식`, `주 식`, `주___식`, `주ㅋㅋ식`, zero-width 문자 삽입과 `ㅈㅜㅅㅣㄱ` 같은 자모 분리도 감지합니다. 금지어 글자 사이에서 공백·기호·숫자·짧은 `ㅋ/ㅎ` 반복처럼 제한된 방해 문자만 최대 8자까지 허용하며, `주말에 맛있는 식당에 갔다`처럼 실제 한글 단어가 사이에 있는 정상 문장은 연결해서 검사하지 않습니다. 기존 부분 문자열 규칙과 영문 대소문자 무시 동작은 그대로 유지합니다.

```text
사용자가 메시지 전송 또는 수정
              ↓
등록된 금지어와 비교
              ↓
메시지 삭제 시도
              ↓
사용자에게 DM 경고
              ↓ DM을 받을 수 없는 경우
원래 채널에 약 5초 동안 임시 경고
              ↓
관리자 로그와 최소한의 DB 기록 남김
```

봇 메시지와 Webhook 메시지는 기본적으로 검사하지 않으므로 봇 자신의 경고를 다시 감지하는 무한 반복이 생기지 않습니다. 사용자가 정상적인 메시지를 보낸 뒤 수정해서 금지어를 추가하는 경우도 raw message edit 이벤트를 통해 다시 검사합니다.

## 사용자 경고와 관리자 기록을 분리했습니다

일반 채팅 메시지에는 Discord의 ephemeral 응답을 사용할 수 없습니다. 그래서 위반 사용자에게 먼저 DM을 보내고, DM이 차단되어 있을 때만 원래 채널에 잠시 보이는 경고를 남깁니다.

관리자가 `/설정 로그채널`로 감사 채널을 지정하면 해당 채널에는 사용자, 채널, 감지 단어, 삭제 성공 여부와 원문 미리보기가 Embed로 전달됩니다. 반면 DB 위반 기록에는 원문 전체를 저장하지 않고 서버·사용자·채널 ID와 감지된 단어, 시각만 남깁니다. 운영에 필요한 정보는 제공하면서 불필요한 개인정보 보관은 줄이기 위한 설계입니다.

로그 채널이 설정되지 않았거나 접근할 수 없는 상태라도 금지어 감지와 메시지 삭제 기능 자체는 계속 동작합니다.

## 선택한 채널의 하루 대화를 자동으로 요약합니다

일일 요약은 환경변수로 지정한 한 서버의 한 텍스트 채널에만 선택적으로 적용됩니다. 봇의 공지·금지어·서버 설정은 계속 모든 설치 서버에서 `guild_id`별로 분리되어 동작합니다. 지정 채널에서는 사람이 작성한 텍스트만 수집하며 봇, Webhook, 시스템 메시지, 빈 메시지는 제외합니다.

봇이 재시작되면 `Asia/Seoul` 기준 가장 최근 06:00부터 현재까지 Discord 메시지 기록을 백그라운드로 다시 읽습니다. 이미 저장된 `message_id`는 건너뛰므로 여러 번 실행해도 중복되지 않고, 백필 권한이나 API 오류가 발생해도 기존 봇 기능은 계속 실행됩니다. 기본 3일이 지난 원문은 정리하고 생성된 리포트와 통계만 유지합니다.

매일 06:01에 전날 06:00부터 당일 06:00까지의 대화를 집계해 최소 메시지·참여자 조건을 만족하면 Gemini로 전체 요약과 사용자별 요약을 생성합니다. 리포트 하단에는 집계 구간을 작게 표시합니다. 완료된 리포트 본문은 지정된 리포트 채널에 공개 게시하지만 `/하루요약 상태`, `/하루요약 오늘`, `/하루요약 어제`, `/하루요약 연결확인`의 진행·성공·실패 응답은 실행한 관리자에게만 보입니다. `연결확인`은 API 키 값이나 대화를 공개하지 않고 최소 요청 한 번으로 인증·모델 접근·응답 수신 여부만 확인합니다.

필요한 환경변수와 권한, 개인정보 보관 및 장애 처리 방식은 [일일 대화 요약 운영 가이드](docs/daily-summary.md)에 정리되어 있습니다.

## 누가 명령을 사용할 수 있나요?

설정을 바꾸거나 데이터를 추가·삭제하는 명령은 서버 소유자 또는 Discord의 Administrator 권한을 가진 사용자만 실행할 수 있습니다. 봇 자체에는 Administrator 권한이 필요하지 않습니다. `/금지어 목록`은 서버의 규칙을 누구나 확인할 수 있도록 일반 사용자에게도 열려 있습니다.

| 명령 | 사용 대상 | 하는 일 |
| --- | --- | --- |
| `Apps → 공지로 등록` | 소유자/관리자 | 기존 메시지를 공지로 등록 |
| `/공지 등록` | 소유자/관리자 | 새 원본 공지를 작성하고 등록 |
| `/공지 목록` | 소유자/관리자 | 현재 활성화된 공지 확인 |
| `/공지 삭제` | 소유자/관리자 | 공지 일정 삭제 |
| `/공지 즉시전송` | 소유자/관리자 | 선택한 공지를 바로 다시 알림 |
| `/금지어 추가` | 소유자/관리자 | 금지어 한 개 등록 |
| `/금지어 일괄추가` | 소유자/관리자 | 금지어 최대 500개 등록 |
| `/금지어 삭제` | 소유자/관리자 | 등록된 금지어 삭제 |
| `/금지어 목록` | 모든 서버 사용자 | 현재 금지어 목록 확인 |
| `/설정 로그채널` | 소유자/관리자 | 기존 채널을 관리자 감사 로그로 지정 |
| `/하루요약 상태` | 설정 서버의 소유자/관리자 | 설정·수집량·최근 리포트 상태 확인 |
| `/하루요약 오늘` | 설정 서버의 소유자/관리자 | 오늘 대화 미리보기 생성 |
| `/하루요약 어제` | 설정 서버의 소유자/관리자 | 직전 06:00~06:00 구간을 새 리포트로 게시 |
| `/하루요약 연결확인` | 설정 서버의 소유자/관리자 | Gemini 인증과 모델 접근 비공개 점검 |

관리 명령의 성공·실패·권한 없음 응답은 해당 사용자에게만 보이는 ephemeral 메시지로 전달됩니다.

## 서버에 바로 추가하기

직접 서버를 운영하거나 코드를 실행할 필요 없이 아래 링크에서 서버를 선택하고 승인하면 됩니다.

[**eslee-bot 서버 초대 화면 열기**](https://discord.com/oauth2/authorize?client_id=1525689872621240442&scope=bot+applications.commands&permissions=2147576832)

이 링크에는 특정 `guild_id`나 서버 선택 잠금이 없습니다. Discord Developer Portal의 **Bot → Public Bot**을 켜 두면 서버 관리 권한이 있는 사용자가 각자 원하는 서버를 선택해 설치할 수 있습니다. `bot` scope로 봇 계정을 서버에 추가하고 `applications.commands` scope로 애플리케이션 명령을 사용할 수 있게 합니다.

초대 링크에는 다음 기능에 필요한 최소 권한만 포함되어 있습니다: 채널 보기, 메시지 보내기, 메시지 관리, 링크 Embed, 메시지 기록 보기, 애플리케이션 명령 사용. 파일을 재업로드하거나 새로운 Poll을 만들지 않으므로 파일 첨부와 투표 만들기 권한은 요구하지 않습니다.

## 직접 실행하기

개발하거나 별도의 Bot 애플리케이션으로 직접 운영하려면 Python 3.12 이상이 필요합니다.

```powershell
git clone https://github.com/esleeeeee/eslee-discord-bot.git
Set-Location eslee-discord-bot
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Discord Developer Portal에서 Bot을 생성하고 **Message Content Intent**를 활성화한 뒤, `.env`의 `DISCORD_TOKEN`을 실제 토큰으로 변경합니다. Presence Intent와 Server Members Intent는 필요하지 않습니다.

```env
DISCORD_TOKEN=replace-with-your-bot-token
# 로컬 개발에서만 선택: 명령을 즉시 확인할 테스트 서버 ID
DISCORD_DEV_GUILD_ID=
DATABASE_URL=sqlite+aiosqlite:///./data/eslee_bot.db
LOG_LEVEL=INFO
SCHEDULER_POLL_SECONDS=60

# 선택: 특정 서버/채널의 일일 대화 요약
DAILY_SUMMARY_ENABLED=false
DAILY_SUMMARY_GUILD_ID=
DAILY_SUMMARY_SOURCE_CHANNEL_ID=
DAILY_SUMMARY_REPORT_CHANNEL_ID=
GEMINI_API_KEY=
DAILY_SUMMARY_AI_MODEL=gemini-3.5-flash
```

토큰은 코드나 GitHub에 올리지 마세요. 이 저장소는 `.env`, SQLite DB, 실행 로그를 Git에서 제외합니다. 설정이 끝났다면 다음 명령으로 실행합니다.

```powershell
python -m eslee_bot
```

특정 서버 ID는 필수가 아닙니다. 봇은 시작할 때 항상 global 명령을 먼저 동기화하므로, 프로덕션에서는 `DISCORD_DEV_GUILD_ID`를 설정하지 않습니다. 로컬 개발 중 명령 변경을 즉시 확인하고 싶을 때만 테스트 서버 ID를 넣으면 global 명령을 유지하면서 그 서버에도 개발용 사본을 동기화합니다. `DISCORD_GUILD_ID`, `GUILD_ID`, `TEST_GUILD_ID`는 사용하지 않습니다.

## Docker로 실행하기

Docker Compose를 사용하면 SQLite 데이터가 `bot-data` named volume에 보존됩니다. 컨테이너는 root가 아닌 전용 사용자로 실행됩니다.

```powershell
Copy-Item .env.example .env
# .env에 토큰을 입력한 뒤
docker compose up --build -d
docker compose logs -f bot
```

## Northflank Developer Sandbox에 배포하기

Northflank의 Developer Sandbox는 현재 항상 켜지는 무료 서비스 2개와 무료 데이터베이스 Addon 1개를 제공합니다. 이 플랜은 취미·시험용이며 Northflank는 실제 프로덕션 용도로는 권장하지 않습니다. 무료 정책과 한도는 바뀔 수 있으므로 배포 전에 [공식 요금 안내](https://northflank.com/docs/v1/application/billing/pricing-on-northflank)를 확인하세요.

이 봇은 HTTP 서버가 아니라 Discord Gateway에 외부 연결을 여는 프로세스입니다. Dockerfile에는 `EXPOSE`가 없으며, Northflank 서비스에도 public/private port나 HTTP health check를 추가하지 않습니다.

1. Northflank에서 Developer Sandbox 프로젝트를 만들고, 같은 프로젝트에 무료 PostgreSQL Addon을 하나 생성합니다.
2. **Secrets → Create secret group**에서 runtime 변수 그룹을 만들고 **Show addons**로 PostgreSQL Addon을 연결합니다. Addon이 제공하는 `POSTGRES_URI`의 alias를 정확히 `DATABASE_URL`로 지정한 뒤 이 그룹을 봇 서비스에 적용합니다. 원본 URI에는 사용자명과 비밀번호가 포함되므로 직접 복사해 GitHub에 올리지 않습니다.
3. 같은 secret group 또는 서비스의 runtime variables에 `DISCORD_TOKEN`을 secret 값으로 추가합니다. 필요하다면 `LOG_LEVEL`, `SCHEDULER_POLL_SECONDS`도 추가합니다. 프로덕션에서는 `DISCORD_DEV_GUILD_ID`를 만들지 않습니다. 이 값들은 build argument가 아니라 실행 컨테이너에만 전달되는 runtime 변수여야 합니다.
4. GitHub의 이 저장소와 `main` 브랜치를 선택해 **Combined Service**를 만들고 build type을 **Dockerfile**로 설정합니다. Dockerfile 경로는 저장소 루트의 `/Dockerfile`, 인스턴스 수는 `1`로 둡니다.
5. Networking/Ports 단계는 비워 두고 서비스를 생성합니다. 첫 실행 시 `Base.metadata.create_all()`이 빈 PostgreSQL DB에 테이블과 인덱스를 생성합니다. 배포 로그에서 `Database initialized`, Discord 로그인, scheduler 시작 메시지를 확인합니다.

Northflank가 제공하는 `postgresql://...` 형식은 실행 시 자동으로 `postgresql+asyncpg://...`로 정규화됩니다. `postgres://...`도 지원하며, 이미 `postgresql+asyncpg://...`인 scheme은 중복 변경하지 않습니다. TLS Addon URI의 `sslmode=require`는 asyncpg가 지원하는 `ssl=require` 연결 옵션으로 변환되어 TLS 요구사항을 유지합니다. 자세한 Addon 연결 과정은 [Northflank PostgreSQL 문서](https://northflank.com/docs/v1/application/databases-and-persistence/deploy-databases-on-northflank/deploy-postgresql-on-northflank)를 참고하세요.

Northflank 기본 운영에 필요한 환경변수는 `DISCORD_TOKEN`과 PostgreSQL `POSTGRES_URI`를 alias한 `DATABASE_URL` 두 개입니다. 일일 요약을 사용할 때만 `DAILY_SUMMARY_*` 채널 설정과 `GEMINI_API_KEY`를 추가합니다. `LOG_LEVEL`과 `SCHEDULER_POLL_SECONDS`는 선택사항이며, 봇 전체를 특정 서버에 묶는 Guild ID 환경변수는 필요하지 않습니다.

로컬 SQLite 데이터는 PostgreSQL로 자동 이전되지 않습니다. Northflank 배포가 정상적으로 Discord에 연결된 것을 확인한 뒤 로컬 봇을 종료하고, 같은 토큰으로 로컬과 Northflank에서 동시에 실행하지 마세요. 중복 리마인드를 피하기 위해 Northflank 인스턴스 수도 반드시 하나로 유지합니다.

## 코드 구조와 기술 선택

Discord 이벤트 처리와 테스트 가능한 규칙을 분리했습니다. Cog는 Discord 명령과 이벤트를 받아 서비스에 전달하고, 서비스는 공지 분류·미리보기·우회 표기 금지어 매칭·일일 요약 같은 규칙을 처리합니다. Repository는 SQLAlchemy async session을 통해 선택된 DB와 통신하며, Scheduler는 공지 리마인드와 일일 리포트 실행 시각을 관리합니다.

명령과 메시지 이벤트는 현재 Discord 서버의 `interaction.guild.id` 또는 `message.guild.id`를 DB 작업에 전달합니다. 서버 설정, 공지, 금지어, 위반 기록은 모두 `guild_id`를 저장하며 조회·삭제·수정도 같은 `guild_id` 범위 안에서만 수행합니다. 전체 서버의 마감 공지를 찾는 scheduler만 운영상 모든 행을 조회하고, 각 행에 저장된 서버와 채널로 개별 처리합니다.

```text
Discord 명령·이벤트
        ↓
      Cogs
        ↓
    Services  ← 순수 로직 테스트
        ↓
  Repositories
        ↓
 SQLite / PostgreSQL + Scheduler
```

프로젝트는 Python 3.12, discord.py 2.7.1, SQLAlchemy 2.x async, 로컬 SQLite용 aiosqlite, 운영 PostgreSQL용 asyncpg, pydantic-settings와 Google Gen AI SDK를 사용합니다. Ruff와 pytest로 품질을 검사하고 GitHub Actions에서 같은 검사를 자동 실행합니다.

```powershell
python -m ruff check .
python -m pytest
```

현재 테스트는 금지어 정규화·우회 표기·오탐 방지·다중 감지, 권한, 공지 콘텐츠 분류, Poll 남은 시간, 장시간 다운타임 보정, 리마인드 교체, DM 실패 fallback, 서버별 DB 격리, 일일 요약 수집·백필·스케줄·Gemini 오류 처리와 개인정보 정책을 검증합니다.

## 현재 범위와 운영 시 참고사항

v1은 소규모 서버에서 하나의 봇 프로세스를 실행하는 구성을 전제로 합니다. 여러 프로세스를 실행하면 PostgreSQL을 사용하더라도 Discord 리마인드가 중복될 수 있습니다. 자동 DB 스키마 마이그레이션은 아직 지원하지 않습니다. 모델을 직접 변경하기 전에는 DB를 백업하세요.

향후에는 공지별 주기, 시작·종료일, 야간 알림 제한, 역할 기반 관리자, 반복 위반자 제재 같은 기능을 확장할 수 있습니다. 현재 버전은 공지 재노출, 우회 표기를 포함한 기본 모더레이션, 선택 채널의 일일 대화 요약을 안정적으로 운영하는 데 집중합니다.

## License

MIT License
