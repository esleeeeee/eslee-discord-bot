# 설계 결정

## 2026-07-20: 기존 프로세스에 aiohttp 결합

별도 서비스나 무거운 framework 대신 discord.py의 기존 asyncio loop에 aiohttp runner를 붙였다. 배포 단위와 Discord cache 소유권이 하나라서 상태 동기화가 단순하고 Northflank Combined Service를 그대로 사용할 수 있다.

## 2026-07-20: OneKey 설정은 선택적인 완전한 쌍

`ONEKEY_DISCORD_USER_ID`와 `ONEKEY_API_TOKEN`은 둘 다 없으면 API 비활성, 둘 다 있으면 활성, 하나만 있으면 시작 실패로 처리한다. 기존 운영자를 갑자기 중단시키지 않으면서 부분 설정을 조용히 무시하지 않기 위한 결정이다.

## 2026-07-20: Guild voice cache 사용

`VOICE_STATES` intent를 코드에서 켜고 `Guild.voice_states`를 조회한다. Members privileged intent, 별도 승인, 음성채널 직접 접속, 반복 REST 호출은 필요 없다. 봇이 접근하지 못하는 Guild와 DM 계열 통화는 의도적으로 범위 밖이다.

## 2026-07-20: 실패와 보안 계약

- health는 운영 probe를 위해 공개하지만 민감정보를 반환하지 않는다.
- voice status는 Bearer token만 허용하고 query token은 지원하지 않는다.
- Discord가 준비되지 않으면 false로 오인시키지 않고 503을 반환한다.
- token과 Authorization header는 access/application log에 남기지 않는다.
