# 개발 현황

기준일: 2026-08-01  
작업 브랜치: `feat/onekey-voice-status-api`  
기능 커밋: `ed80d83`  
테스트 커밋: `99827f7`  
main 병합 커밋: `6ebdb37` (충돌 없음)  
보안 보완 커밋: `3c8a935`

## 구현 완료

- 공개 `GET /health`와 `discord_ready` 상태 분리
- Bearer 인증 `GET /api/voice-status`
- timing-safe token 비교, 401/503/200 응답
- 모든 접근 가능 Guild의 cached Voice State 조회(`Member.voice` → `Guild._voice_state_for`)
- `VOICE_STATES` intent 활성화
- `ONEKEY_DISCORD_USER_ID`, secret `ONEKEY_API_TOKEN`, `PORT` 검증
- `0.0.0.0` bind, Northflank port, aiohttp/Discord 동일 프로세스 생명주기
- Windows ZoneInfo용 tzdata와 Docker 8080 명시
- `ONEKEY_API_TOKEN` 32자 이상·앞뒤 공백 금지를 설정 단계에서 강제
- `/api/voice-status` 정상 응답은 `in_voice` boolean 하나만 반환(guild·channel 비공개)
- 두 엔드포인트 `Cache-Control: no-store`, 인증 엔드포인트 `Vary: Authorization`
- 설정 검증 오류에 입력값을 노출하지 않음(`hide_input_in_errors`)
- 최신 `main`의 일일 요약 quota 예산·checkpoint 기능을 병합해 함께 유지

## 부분 구현 또는 실환경 미검증

- Northflank public port와 health check는 문서화했으나 실제 서비스 설정은 사용자 secret이 없어 미적용이다.
- Discord 실제 계정의 입장/퇴장 cache 갱신은 mock 자동 테스트만 완료했다.

## 미구현

- 봇이 없는 서버 및 DM/그룹 DM 통화 감지(계약상 제외)
- token rotation UI와 rate limiting(현재 개인용 MVP 범위 밖)

## 자동 테스트

- Ruff: 통과
- pytest: 271 passed, 기존 discord.py `audioop` deprecation warning 1건
- 인증 성공/실패/누락/형식 오류, ready 전 503, health, true/false, 다중 Guild, 설정 누락·오류, secret repr 비노출을 검증한다.
- aiohttp test client로 실제 라우팅(200/401/404/405)과 응답 헤더를 검증한다.
- 중복 start/close, close 후 재시작, bind 실패 시 runner 정리를 검증한다.
- token 길이·공백 규칙과 거부 오류에 값이 노출되지 않는지 검증한다.
- `.env.example`을 그대로 복사해도 기동되고 OneKey가 꺼진 상태인지 검증한다.
- `discord.Guild`에 실제로 존재하는 API만 사용하는지 계약 테스트로 고정한다.
- members intent 없이도 음성 상태를 찾는지, 비ASCII·손상된 Authorization 헤더가 401인지 검증한다.
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
