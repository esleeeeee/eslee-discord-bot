# 개발 현황

기준일: 2026-07-20  
작업 브랜치: `feat/onekey-voice-status-api`  
기능 커밋: `ed80d83`  
테스트 커밋: `99827f7`

## 구현 완료

- 공개 `GET /health`와 `discord_ready` 상태 분리
- Bearer 인증 `GET /api/voice-status`
- timing-safe token 비교, 401/503/200 응답
- 모든 접근 가능 Guild의 cached Voice State 조회
- `VOICE_STATES` intent 활성화
- `ONEKEY_DISCORD_USER_ID`, secret `ONEKEY_API_TOKEN`, `PORT` 검증
- `0.0.0.0` bind, Northflank port, aiohttp/Discord 동일 프로세스 생명주기
- Windows ZoneInfo용 tzdata와 Docker 8080 명시

## 부분 구현 또는 실환경 미검증

- Northflank public port와 health check는 문서화했으나 실제 서비스 설정은 사용자 secret이 없어 미적용이다.
- Discord 실제 계정의 입장/퇴장 cache 갱신은 mock 자동 테스트만 완료했다.

## 미구현

- 봇이 없는 서버 및 DM/그룹 DM 통화 감지(계약상 제외)
- token rotation UI와 rate limiting(현재 개인용 MVP 범위 밖)

## 자동 테스트

- Ruff: 통과
- pytest: 196 passed, 기존 discord.py `audioop` deprecation warning 1건
- 인증 성공/실패/누락/형식 오류, ready 전 503, health, true/false, 다중 Guild, 설정 누락·오류, secret repr 비노출을 검증한다.
- 기존 SQLite/PostgreSQL schema/URL, 공지·스케줄러·금지어·일일 요약 회귀 테스트도 함께 통과했다.

## 회사 PC에서 직접 검증

- Python 3.12.13 가상환경 생성 및 의존성 설치
- Windows에서 ZoneInfo 누락을 발견해 tzdata 추가 후 전체 테스트 통과
- 실제 token 없이 handler와 Discord cache fake 기반 검증

## 집 PC·Northflank에서 검증 필요

- 실제 Discord 사용자 음성채널 입장/퇴장 false → true → false
- Northflank `/health`, 인증 401, voice status 200/503
- 재배포와 graceful shutdown, public URL/TLS

## 필요한 설정

- Discord Developer Portal: 기존 Message Content Intent 유지. Voice States는 privileged toggle이 아니므로 추가 승인 없음.
- 서버 권한: 기존 View Channels만 유지. Connect/Speak/Administrator 권한 불필요.
- Northflank runtime secret: `ONEKEY_DISCORD_USER_ID`, `ONEKEY_API_TOKEN`; runtime variable `PORT`.

## 알려진 제한과 다음 작업

- cache 기반이므로 봇이 접근 가능한 Guild만 보인다.
- 다음 작업은 이 API 계약을 사용하는 `eslee-onekey` Windows MVP와 실제 Northflank 통합 검증이다.
