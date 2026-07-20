# 회사 PC → 집 PC 구현 인계 프롬프트

## 목표

`eslee-discord-bot`의 `feat/onekey-voice-status-api` 브랜치에서 인증된 Discord 음성 상태 API를 유지·검증하고, 별도 `eslee-onekey` 저장소의 Windows MVP와 연동한다.

## 절대 조건

- 실제 Discord bot token, 사용자 ID, OneKey API token, DB URL/비밀번호, Authorization header, 개인 PC 절대경로와 오디오 endpoint ID를 코드·문서·테스트·로그·커밋에 넣지 않는다.
- 기존 SQLite와 PostgreSQL/Northflank, 공지·리마인드·금지어·관리자 기록·일일 요약 기능을 유지한다.
- force push, 기존 원격 브랜치 삭제, Git 이력 재작성을 하지 않는다.
- 실환경 값이 없으면 placeholder와 fake로 자동 테스트하고 미검증 항목을 명시한다.

## 고정 API 계약

```http
GET /health

GET /api/voice-status
Authorization: Bearer <ONEKEY_API_TOKEN>
```

health는 `status`와 `discord_ready`를, voice status는 정상 시 최소 `in_voice` boolean을 반환한다. 인증 실패는 401, Discord 준비 전은 503이다. 감지 범위는 봇이 접근하는 Guild이고 DM 통화는 제외한다.

## 완료 전 확인

1. Ruff와 전체 pytest 회귀 테스트
2. 실제 Discord 입장/퇴장 및 Northflank port/TLS/health
3. token/header 로그 비노출
4. 두 저장소의 API 문서 일치
5. `DEVELOPMENT_STATUS.md`의 브랜치·커밋·미검증·다음 작업 갱신
