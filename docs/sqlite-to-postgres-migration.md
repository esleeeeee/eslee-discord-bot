# SQLite → PostgreSQL 데이터 이전

이 도구는 기존 SQLite 파일을 읽기 전용으로 열고 현재 PostgreSQL 데이터와 병합합니다. SQLite
파일을 수정하거나 삭제하지 않으며, PostgreSQL 작업은 하나의 트랜잭션 안에서 수행됩니다. 오류,
스키마 불일치, 공지 ID 충돌 또는 검증 누락이 발생하면 전체 작업을 롤백합니다.

## 이전 전 준비

1. 로컬 SQLite 파일을 별도 위치에 한 번 더 복사해 백업합니다.
2. Northflank PostgreSQL도 스냅샷 또는 백업을 준비합니다.
3. 마이그레이션 중 새 공지나 위반 기록이 생기지 않도록 Northflank 봇 서비스를 잠시 중지합니다.
4. 로컬 PC에서 접근 가능한 PostgreSQL 연결 URI를 준비합니다. Northflank 내부 alias만 사용할 수
   있다면 보안 터널/포트 포워딩을 사용하거나, SQLite 파일과 스크립트를 실행할 수 있는 일회성
   Northflank 작업 환경에서 실행해야 합니다.
5. 연결 URI와 비밀번호는 `.env`, 코드, 로그 또는 Git에 저장하지 않습니다.

프로젝트 의존성이 설치되어 있지 않다면 먼저 다음을 실행합니다.

```powershell
python -m pip install -e ".[dev]"
```

## 실행

프로젝트 루트에서 현재 PowerShell 프로세스에만 PostgreSQL URI를 주입합니다. 이 스크립트는
프로젝트의 `.env`를 자동으로 읽지 않으므로, 로컬 SQLite용 `.env`는 그대로 둘 수 있습니다.

```powershell
$env:DATABASE_URL = "<로컬에서 접근 가능한 Northflank PostgreSQL URI>"
python scripts/migrate_sqlite_to_postgres.py --dry-run
```

드라이런 결과에서 모든 테이블의 `missing`이 `0`인지 확인합니다. 드라이런은 실제 INSERT와
검증까지 수행한 뒤 PostgreSQL 트랜잭션을 롤백합니다. 결과가 정상이면 실제 이전을 실행합니다.

```powershell
python scripts/migrate_sqlite_to_postgres.py
Remove-Item Env:DATABASE_URL
```

SQLite 파일이 기본 위치가 아니라면 다음과 같이 지정합니다.

```powershell
python scripts/migrate_sqlite_to_postgres.py `
  --sqlite-path "C:\path\to\source.db" `
  --dry-run
```

다른 환경변수 이름을 사용하려면 다음 옵션을 사용할 수 있습니다.

```powershell
python scripts/migrate_sqlite_to_postgres.py `
  --database-url-env NORTHFLANK_DATABASE_URL `
  --dry-run
```

`postgresql://`, `postgres://`, `postgresql+asyncpg://` URI를 모두 지원합니다. Northflank URI의
`sslmode=require`는 기존 설정 정규화 로직을 통해 asyncpg가 지원하는 `ssl=require`로 변환됩니다.

## 중복 및 충돌 처리

- `guild_settings`: `guild_id`가 같으면 기존 PostgreSQL 행을 유지하고 건너뜁니다.
- `forbidden_words`: `(guild_id, normalized_word)`가 같으면 건너뜁니다.
- `moderation_violations`: 서버, 사용자, 채널, 감지 단어, 발생 시각이 모두 같으면 건너뜁니다.
- `announcements`: `(guild_id, channel_id, source_message_id)`와 `id`가 모두 같으면 건너뜁니다.
  공지 ID 또는 자연키 중 하나만 충돌하면 `announcement_id` 보존을 위해 전체 작업을 중단합니다.
- 공지를 제외한 테이블에서 숫자 ID만 충돌하고 실제 데이터는 다르면, PostgreSQL의 비어 있는 새
  ID로 재배정해 두 기록을 모두 보존합니다.

이 정책 때문에 스크립트를 여러 번 실행해도 같은 데이터가 중복 삽입되지 않습니다. 기존
PostgreSQL 행을 덮어쓰거나 삭제하지 않습니다.

## 결과 확인

스크립트는 테이블별로 다음 값을 출력합니다.

- `SQLite`: 원본 행 수
- `PostgreSQL(before)`: 이전 전 대상 행 수
- `inserted`: 이번 실행에서 삽입한 행 수
- `remapped-id`: ID 충돌 때문에 새 ID를 부여한 행 수
- `skipped`: 이미 존재하여 건너뛴 행 수
- `PostgreSQL(after)`: 이전 후 행 수(드라이런에서는 롤백 직전의 예상 행 수)
- `missing`: 이전 후 찾지 못한 SQLite 행 수

실제 이전이 끝난 뒤 `Result: COMMITTED`와 `Missing source rows: 0`을 확인하고 봇 서비스를 다시
시작합니다. 같은 명령을 다시 실행했을 때 `inserted`가 `0`이고 기존 행이 `skipped`로 집계되면
재실행 안전성도 확인할 수 있습니다.
