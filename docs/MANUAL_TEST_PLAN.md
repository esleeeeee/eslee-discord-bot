# 집 PC 및 Northflank 수동 테스트 계획

## 준비

1. 저장소를 clone하고 `git fetch origin`을 실행한다.
2. `git switch feat/onekey-voice-status-api` 후 최신 상태를 pull한다.
3. Python 3.12+ 가상환경을 만들고 `python -m pip install -e ".[dev]"`를 실행한다.
4. 실제 Discord 사용자 ID를 `ONEKEY_DISCORD_USER_ID`에 runtime secret으로 입력한다.
5. 새롭고 충분히 긴 무작위 값을 생성해 `ONEKEY_API_TOKEN`에 저장한다. token 자체를 문서·로그·명령 이력에 복사하지 않는다.
6. 기존 `DISCORD_TOKEN`, `DATABASE_URL`을 Northflank secret group에 유지한다.

## Discord 설정

1. Discord Developer Portal → Applications → 해당 bot → Bot으로 이동한다.
2. 기존 **Message Content Intent**가 켜져 있는지 확인한다.
3. Presence Intent와 Server Members Intent는 켜지 않는다.
4. Voice States는 privileged intent가 아니므로 별도 toggle이나 승인이 필요 없음을 확인한다.
5. 초대 권한에는 기존 View Channels 등을 유지하며 Connect/Speak/Administrator를 추가하지 않는다.

## Northflank 배포

1. 기능 브랜치로 Dockerfile build를 배포한다.
2. `PORT=8080` 또는 Northflank가 지정한 동일 port를 runtime에 설정한다.
3. 해당 container port를 HTTP public port로 노출하고 `/health` health check를 설정한다.
4. `GET <public-url>/health`가 200과 `status: ok`를 반환하는지 확인한다.
5. Discord 연결 전/재시작 구간에는 `discord_ready: false`, 연결 후에는 true인지 확인한다.

## API 계약

1. Authorization 없이 `/api/voice-status`가 401인지 확인한다.
2. 잘못된 scheme/token이 401인지 확인한다.
3. 음성채널 미참여 상태에서 올바른 Bearer token 요청이 200과 `in_voice: false`인지 확인한다.
4. 봇이 설치된 서버의 음성채널에 대상 사용자가 입장하면 `in_voice: true`인지 확인한다.
5. 퇴장 후 false로 돌아오는지 확인한다.
6. 봇 재시작 중 인증된 요청이 503인지 확인한다.
7. 응답/배포/application log에 token이나 Authorization header가 없는지 확인한다.
8. OneKey에 최종 public URL과 같은 token을 안전 저장한 뒤 계약을 재검증한다.

## 회귀와 장애

1. `python -m ruff check .`와 `python -m pytest`를 실행한다.
2. 기존 공지·금지어·일일 요약 명령을 smoke test한다.
3. SQLite 로컬 실행과 PostgreSQL Northflank 초기화를 각각 확인한다.
4. API port 충돌이나 잘못된 필수 설정 시 조용히 실행되지 않고 명확히 종료되는지 확인한다.
