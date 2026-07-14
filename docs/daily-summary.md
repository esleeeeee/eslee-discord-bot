# Discord 일일 대화 요약 운영 가이드

이 기능은 환경변수로 지정한 **한 Discord 서버의 한 텍스트 채널**만 수집한다. 기존
금지어·공지·서버 설정·위반 기록은 계속 모든 설치 서버에서 `guild_id`별로 동작하며,
글로벌 슬래시 명령 동기화 구조도 바뀌지 않는다.

## 작동 방식

- 지정 채널의 사람이 작성한 텍스트만 저장한다. 봇, 웹훅, 시스템 메시지, 빈 텍스트,
  첨부파일만 있는 메시지는 DB에 넣지 않는다.
- 메시지가 수정되면 최신 텍스트로 갱신하고, 빈 텍스트가 되거나 삭제되면 요약 대상에서도
  제거한다.
- Discord 답장의 `reply_to_message_id`를 저장하고, 같은 날 원문이 있으면 요약 입력에 원 작성자도
  함께 표시한다.
- `Asia/Seoul` 기준 00:02에 전날 00:00 이상, 당일 00:00 미만 대화를 집계한다.
- 전체 10개 이상이고 참여자가 2명 이상일 때만 Gemini를 호출한다. 조건 미달은 정상
  `skipped`이며 Discord에는 게시하지 않는다.
- 총 메시지, 참여자, 최다 시간대, 최다 작성자는 코드가 계산한다. Gemini는 전체 요약과
  3개 이상 작성한 사용자 최대 20명의 개인 요약만 생성한다.
- 평상시에는 하루 전체 대화를 단 한 번의 API 요청으로 요약한다. 보수적인 문자 길이 한도를
  넘는 비정상적으로 큰 날만 시간순 청크 요약 후 최종 통합한다.

## 시작 시 당일 메시지 백필

`DAILY_SUMMARY_ENABLED=true`이고 설정이 유효하면, 봇이 준비된 직후 실시간 수집과 스케줄러를
먼저 시작하고 백그라운드로 백필을 실행한다. Discord history pagination을 사용해 서울 기준
오늘 00:00부터 현재까지 소스 채널을 읽고, 실시간 수집과 같은 필터를 적용한다.

`message_id`가 unique라서 재시작하거나 백필을 다시 실행해도 중복 저장되지 않는다. 채널 권한이나
Discord API 문제로 백필이 실패해도 오류 요약과 개수만 로그로 남기고 봇 프로세스와 기존
기능은 계속 실행한다. 원문 내용은 로그로 출력하지 않는다.

## 필수 환경변수

| 키 | 설명 |
| --- | --- |
| `DAILY_SUMMARY_ENABLED` | `true`로 설정해 기능 활성화 |
| `DAILY_SUMMARY_GUILD_ID` | 요약을 사용할 Discord 서버 ID |
| `DAILY_SUMMARY_SOURCE_CHANNEL_ID` | 원문을 수집할 텍스트 채널 ID |
| `DAILY_SUMMARY_REPORT_CHANNEL_ID` | 완성 리포트를 게시할 텍스트 채널 ID |
| `GEMINI_API_KEY` | Google AI Studio에서 발급한 secret. 로그나 Git에 넣지 않음 |

`DAILY_SUMMARY_AI_MODEL`은 코드에서 기본 `gemini-3.5-flash`를 제공하지만, 운영 모델을 바꿔야 할 때
사용할 것을 권장한다.

## 선택 환경변수와 기본값

| 키 | 기본값 | 설명 |
| --- | --- | --- |
| `DAILY_SUMMARY_AI_MODEL` | `gemini-3.5-flash` | Gemini 모델명 |
| `DAILY_SUMMARY_TIMEZONE` | `Asia/Seoul` | 날짜 경계 시간대 |
| `DAILY_SUMMARY_RUN_TIME` | `00:02` | 전날 리포트 시작 시간 |
| `DAILY_SUMMARY_RAW_RETENTION_DAYS` | `3` | 요약 원문 보관 일수 |
| `DAILY_SUMMARY_MIN_TOTAL_MESSAGES` | `10` | 리포트 최소 메시지 수 |
| `DAILY_SUMMARY_MIN_PARTICIPANTS` | `2` | 리포트 최소 참여자 수 |
| `DAILY_SUMMARY_MIN_USER_MESSAGES` | `3` | 개인 요약 포함 최소 작성 수 |
| `DAILY_SUMMARY_MAX_USERS` | `20` | 개인 요약 최대 인원 |

필수값이 없거나 ID·시간대·시간 형식이 잘못되면 일일 요약만 비활성화되며 기존 봇은 종료하지
않는다. `DAILY_SUMMARY_ENABLED=false`인 때는 ID와 API key가 없어도 정상이다.

## Discord 준비

1. Discord의 **사용자 설정 → 고급 → 개발자 모드**를 켠다.
2. 서버를 우클릭해 `DAILY_SUMMARY_GUILD_ID`를, 수집/리포트 채널을 우클릭해 각 채널 ID를
   복사한다.
3. Developer Portal에서 **Message Content Intent**가 켜져 있어야 한다.
4. 봇 역할에 소스 채널의 **채널 보기**, **메시지 기록 보기**를 허용한다. 리포트 채널에는
   **채널 보기**, **메시지 보내기**, **링크 임베드**, **메시지 기록 보기**를 허용한다.

소스 채널의 메시지 기록 권한이 없으면 실시간 수집은 되더라도 재시작 백필은 실패할 수 있다.

## Gemini API key

Google AI Studio에서 API key를 만들고 Northflank Secret Group의 `GEMINI_API_KEY`로만 저장한다.
월간 무료 할당량, 속도 제한, 유료 과금은 모델과 Google 계정 설정에 따라 바뀔 수 있으므로
배포 전 [Google AI Studio](https://aistudio.google.com/app/apikey)와 Google의 현재 가격/할당량을 확인한다.
큰 대화가 자주 생기면 입력 토큰과 청크 fallback의 추가 요청으로 비용이 늘어날 수 있다.

## Northflank 배포

기존 Combined Service의 Secret Group에 위 필수 키를 추가한다. 기존 PostgreSQL Addon의
`POSTGRES_URI` → `DATABASE_URL` alias는 그대로 유지한다. Discord 봇은 HTTP 서버가 아니므로 public
port는 만들지 않는다. main 배포 후 다음을 확인한다.

- 스키마 초기화 로그 후 `daily_summary_messages`, `daily_reports` 새 테이블 생성
- 일일 요약 scheduler와 startup backfill 시작 로그
- `/하루요약 상태`에서 활성, 서버/채널, 시간대, 모델 확인
- `/하루요약 오늘`로 현재까지의 미리보기 리포트 확인

`오늘` 미리보기는 `preview_*` 상태로 구분된다. 같은 날짜의 중복 수동 호출은 막지만, 다음 날
00:02 자동 실행은 미리보기를 최종 리포트로 교체한다.

## 명령어

- `/하루요약 상태`: 설정과 오늘 저장량, 최근 리포트 상태 표시
- `/하루요약 오늘`: 오늘 00:00부터 현재까지 미리보기 생성
- `/하루요약 어제`: 어제 전체 리포트 수동 생성
- `재생성:true`: 이미 있는 DB 리포트를 새로 생성하고, 추적 중인 Discord 메시지는 가능하면 수정

모든 `/하루요약` 명령은 기존 관리 권한 helper를 사용하며, 해당 서버의 관리자 또는 봇 소유자만
실행할 수 있다. 다른 서버에서 실행하면 해당 서버는 미지원임을 ephemeral로 안내한다.

## 개인정보, 보관, 안전성

- 원문은 설정값 기본 3일이 지나면 일일 정리에서 삭제된다. 장기 보관되는 것은 생성된 리포트와
  통계다. 금지어, 공지, 위반 기록, 서버 설정은 이 정리 대상이 아니다.
- 원문은 Gemini API로 전송되므로 서버 구성원에게 수집·요약 정책을 알리고, Google의 현재 데이터
  처리 정책을 확인한다.
- Discord 원문은 신뢰할 수 없는 데이터로 표시되고, 원문 안의 "이전 지시를 무시해" 같은 문장은
  명령으로 실행하지 않도록 system instruction과 JSON 입력 경계를 둔다.
- 요약은 없는 사실을 만들지 않고 발언자를 섞지 않도록 지시하며, 요청한 `user_id`가 빠지거나 중복되면
  잘못된 AI 응답으로 간주한다.
- Discord token, Gemini key, DB URL, 원문 대화는 정상 로그에 출력하지 않는다.

## 장애 처리

- Gemini는 timeout, 연결, 429, 일부 5xx만 exponential backoff와 jitter로 최대 3회 시도한다. 인증,
  모델명, 요청 형식, schema 오류는 즉시 실패한다.
- Gemini가 최종 실패하거나 Discord 게시가 완료되지 않으면 `daily_reports.status=failed`로 남기고
  봇 프로세스는 계속 실행한다.
- 같은 `guild_id + report_date`는 asyncio lock과 DB unique constraint로 중복 AI 호출/게시를 방지한다.
- Discord에 일부 embed만 올라간 상태에서 실패하면 그 ID를 저장하고, `재생성:true`에서 수정 또는
  이어 게시할 수 있게 한다.
