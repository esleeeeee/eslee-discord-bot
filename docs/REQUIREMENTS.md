# 요구사항

## 기존 봇 보존

- Python 3.12, discord.py 2.7.1, SQLAlchemy async 구조를 유지한다.
- 로컬 SQLite와 Northflank PostgreSQL/asyncpg URL 정규화를 유지한다.
- 공지, 리마인드, 금지어, 관리자 기록, 일일 요약 기능을 회귀시키지 않는다.
- 실제 token, Discord 사용자 ID, DB 비밀번호, Authorization header를 저장소나 로그에 남기지 않는다.

## OneKey HTTP API

- `ONEKEY_DISCORD_USER_ID`와 `ONEKEY_API_TOKEN`을 함께 설정한 경우 API를 활성화한다.
- 둘 중 하나만 설정했거나 사용자 ID/`PORT`가 잘못된 경우 시작 시 명확히 실패한다.
- 둘 다 없으면 기존 배포와의 호환을 위해 API만 비활성화하고 나머지 봇은 그대로 실행한다.
- `GET /health`는 인증 없이 `status`와 `discord_ready`만 반환한다.
- `GET /api/voice-status`는 Bearer token을 요구하고 최소 `in_voice` boolean을 반환한다.
- 인증 실패는 401, Discord gateway 준비 전은 503, 정상은 200을 사용한다.
- token은 query parameter로 받지 않고 timing-safe 비교하며 로그에 남기지 않는다.

## 감지 범위

- 봇이 접근하는 모든 Guild의 discord.py 캐시에서 대상 사용자의 Voice State를 찾는다.
- 하나 이상의 Guild 음성채널에 참여 중이면 `in_voice: true`다.
- Members privileged intent나 Discord REST 조회에 의존하지 않는다.
- 봇이 없는 서버, 개인 DM 통화, 그룹 DM 통화는 감지하지 않는다.

## 운영

- aiohttp 서버와 Discord bot은 같은 asyncio 프로세스에서 실행한다.
- `0.0.0.0`과 Northflank `PORT`를 사용한다.
- 시작 실패를 숨기지 않고, 종료 시 HTTP runner와 기존 scheduler/DB를 정리한다.
- 실제 Discord/Northflank 검증은 별도 수동 계획으로 수행한다.
