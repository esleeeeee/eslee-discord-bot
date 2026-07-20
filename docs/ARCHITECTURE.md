# 아키텍처

## 실행 구조

```text
Settings
  ├─ 기존 Discord/DB/요약 설정
  └─ OneKey 사용자 ID, SecretStr token, PORT
          │
          ▼
EsleeBot.setup_hook
  ├─ Database.initialize
  ├─ Cog load
  ├─ OneKeyApiServer.start
  └─ application command sync
          │
          ├─ Discord Gateway / cached Guild.voice_states
          └─ aiohttp 0.0.0.0:PORT
                ├─ GET /health
                └─ GET /api/voice-status
```

`OneKeyApiServer`는 별도 thread나 event loop를 만들지 않는다. discord.py와 aiohttp가 같은 asyncio loop를 공유하므로 voice state cache를 동기화 장치 없이 읽을 수 있다. `EsleeBot.close()`가 API runner를 먼저 정리한 뒤 기존 daily summary, announcement scheduler, database, Discord client를 닫는다.

## 음성 상태 조회

`find_voice_status()`는 각 `Guild.voice_states` mapping에서 환경변수로 검증된 정수 사용자 ID를 찾는다. `VoiceState.channel`이 존재하는 첫 항목을 발견하면 `in_voice`, guild/channel ID와 표시 이름을 반환한다. 없으면 `{ "in_voice": false }`만 반환한다. 네트워크 REST 호출은 수행하지 않는다.

## 인증과 설정

Pydantic Settings는 `ONEKEY_API_TOKEN`을 `SecretStr`로 보관해 객체 표현과 validation context에서 평문 노출을 줄인다. HTTP handler는 `Authorization: Bearer ...` 형식을 검사하고 `hmac.compare_digest`로 비교한다. aiohttp access log는 비활성화하며 애플리케이션 로그에도 header/token을 전달하지 않는다.

## 의존성과 배포

- `aiohttp==3.14.1`: discord.py가 이미 사용하는 비동기 HTTP stack을 직접 runtime dependency로 명시한다.
- `tzdata==2026.3`: Windows Python에서 `Asia/Seoul` ZoneInfo를 안정적으로 로드한다.
- Docker는 8080을 문서상 기본 포트로 노출하지만 실제 bind 값은 `PORT`다.
- API 설정 두 값이 모두 없으면 서버를 만들지 않아 기존 비-HTTP 배포가 동작한다.
