# Fishing Report Site

선사 조황 콘텐츠를 재게시하지 않고, **새 조황 등록 시점·선박별 활동 빈도·어종 분류·원문 링크·사용자 알림**을 제공하는 메타데이터 중심 서비스입니다.

## 운영 원칙
- 외부 선사의 조황 사진을 다운로드하거나 공개 페이지에 재게시하지 않습니다.
- 선장이 작성한 조황 본문을 공개 페이지에 복사하지 않습니다.
- 날짜, 선박명, 지역, 감지된 어종, 원문 URL 같은 사실 정보와 자체 집계만 표시합니다.
- 상세 조황은 선사의 원문 페이지로 연결합니다.

## 구성
- `generate_site.py`: 공개 조황판에서 메타데이터를 감지하고 사이트 생성
- `sources.json`: 추적 선박과 지역 정보
- `notification_worker.py`: 새 조황 감지 및 사용자별 묶음 알림 작업
- `notifications/schema.sql`: 알림용 데이터베이스 스키마
- `docs/`: GitHub Pages 공개 폴더
- `.github/workflows/update-site.yml`: 사이트 자동 갱신

GitHub Pages: https://ddungja.github.io/fishing-report-site/
