# Fishing Report Site

안흥 스페이스호와 군산 뚱스호의 선상24 조황을 수집해 정적 홈페이지로 생성하는 프로젝트입니다.

## 구성
- `generate_site.py`: 선상24 수집 + 홈페이지 생성
- `sources.json`: 선박별 조황 주소
- `docs/`: GitHub Pages 공개 폴더
- `.github/workflows/update-site.yml`: 매일 자동 갱신

GitHub Pages는 Settings → Pages에서 **Deploy from a branch / main / docs**로 설정합니다.
