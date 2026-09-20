# 카카오 조황 알림 시스템

## 목표

사용자가 카카오톡 채널을 추가하고 원하는 선박을 선택하면,
해당 선박의 새 조황을 **사용자별 최대 시간당 1회** 묶어서 발송합니다.

예:

[새 조황 4건]
• 안흥 스페이스호 3건
• 군산 뚱스호 1건
- 9월20일 갑오징어 조황...
- 오늘 주꾸미 장원...
[조황 전체 보기]

새 글이 없으면 메시지를 보내지 않습니다.

## 구조

1. GitHub Pages: 가입/선박선택 UI
2. Kakao Login: 사용자를 APP_USER_ID로 식별
3. Kakao Talk Channel: 채널 추가
4. Supabase: 사용자/선박구독/조황/발송로그 저장
5. GitHub Actions: 매시 07분 notification_worker.py 실행
6. Kakao Moment Personalized Message: 사용자별 1개 묶음 메시지 발송

## 스팸 방지 규칙

- 사용자 1명당 한 시간에 최대 1개
- 선택한 선박의 새 조황만 포함
- 새 조황이 없으면 발송 없음
- 야간(21:00~07:59)은 발송하지 않고 다음 오전에 합산
- 같은 조황 중복 발송 방지
- 광고/할인/예약 홍보문구는 조황 알림과 섞지 않음
- 알림 수신 동의와 광고성 정보 수신 동의는 분리

## 필요한 GitHub Secrets

SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
KAKAO_BUSINESS_ACCESS_TOKEN
KAKAO_AD_ACCOUNT_ID
KAKAO_CREATIVE_ID

처음에는 Repository variable NOTIFICATION_DRY_RUN=1 로 테스트합니다.
실제 카카오 발송 준비가 끝난 후 0으로 변경합니다.

## Kakao 개인화 메시지 템플릿 변수

소재(creative)에서 다음 사용자 변수를 사용하도록 구성:

- digest_title
- digest_body
- digest_url

## 다음 단계

1. 카카오톡 채널 개설/비즈니스 채널 준비
2. Kakao Developers 앱을 비즈 앱으로 전환
3. 앱과 채널 연결
4. 카카오 로그인 활성화
5. 개인화 메시지 API 권한 및 카카오모먼트 광고계정 준비
6. Supabase 프로젝트 생성 후 schema.sql 실행
7. 가입/선박 선택 UI를 GitHub Pages에 연결
8. 테스트 사용자 1명으로 DRY_RUN → 실제 테스트 발송
