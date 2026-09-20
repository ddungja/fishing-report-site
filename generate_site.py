import json, re, shutil, requests, time, os
from pathlib import Path
from datetime import datetime, date
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "sources.json"
DOCS = ROOT / "docs"
BASE_URL = os.getenv(
    "BASE_URL",
    "https://ddungja.github.io/fishing-report-site"
).rstrip("/")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36"
)

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
})

def esc(v):
    return (
        str(v or "")
        .replace("&","&amp;")
        .replace("<","&lt;")
        .replace(">","&gt;")
        .replace('"',"&quot;")
        .replace("'","&#039;")
    )

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def page_url(url, page):
    if "page=" in url:
        return re.sub(r'([?&])page=\d+', rf'\1page={page}', url)
    return url + ("&" if "?" in url else "?") + f"page={page}"

def parse_date(text, start_dt, end_dt):
    text = str(text or "")

    for m in re.finditer(
        r'(?<!\d)(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})(?!\d)',
        text
    ):
        try:
            dt = datetime(*map(int, m.groups()))
        except ValueError:
            continue
        if start_dt <= dt <= end_dt:
            return dt

    for m in re.finditer(
        r'(?<!\d)(\d{1,2})[./-](\d{1,2})(?!\d)',
        text
    ):
        mo, d = map(int, m.groups())
        try:
            dt = datetime(start_dt.year, mo, d)
        except ValueError:
            continue
        if start_dt <= dt <= end_dt:
            return dt

    return None

def extract_image_urls(blob):
    blob = (
        str(blob or "")
        .replace("\\/","/")
        .replace("\\u002F","/")
        .replace("\\u002f","/")
    )

    found = re.findall(
        r'https?://imga\.sunsang24\.com/ship_board/[^"\'\s<>\\]+',
        blob,
        re.I
    )

    out = []
    seen = set()

    for url in found:
        url = url.rstrip("),;]}>")
        key = re.sub(
            r'_(?:smini|mini|thumb)(?=\.[A-Za-z0-9]+(?:\?|$))',
            '',
            url
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(url)

    return out

def full_image(url):
    return re.sub(
        r'_(?:smini|mini|thumb)(?=\.[A-Za-z0-9]+(?:\?|$))',
        '',
        url
    )

def download_image(url, path):
    attempts = list(dict.fromkeys([full_image(url), url]))

    for candidate in attempts:
        try:
            r = session.get(
                candidate,
                timeout=30,
                headers={
                    "User-Agent": UA,
                    "Accept": "image/*,*/*;q=0.8"
                }
            )

            ct = (r.headers.get("Content-Type") or "").lower()

            if r.status_code != 200:
                continue

            if not (
                ct.startswith("image/")
                or r.content[:3] == b"\xff\xd8\xff"
                or r.content[:8] == b"\x89PNG\r\n\x1a\n"
            ):
                continue

            path.write_bytes(r.content)
            return True
        except Exception:
            pass

    return False

def candidate_blocks(soup):
    nodes = []

    for selector in [
        "li","article",".list",".item",
        ".board-list",".board_item",
        ".post",".card","tr"
    ]:
        try:
            nodes.extend(soup.select(selector))
        except Exception:
            pass

    result = []
    seen = set()

    for node in nodes:
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)

        if len(node.get_text(" ", strip=True)) < 20:
            continue

        result.append(node)

    return result

def extract_list_posts(html, source, current_url, start_dt, end_dt):
    soup = BeautifulSoup(html, "html.parser")
    keyword = source["keyword"].lower()

    result = {}
    for node in candidate_blocks(soup):
        text = node.get_text(" ", strip=True)

        if keyword not in text.lower():
            continue

        dt = parse_date(text, start_dt, end_dt)
        if not dt:
            continue

        title = ""
        for selector in [
            "h1","h2","h3","h4",
            ".title",".subject","a"
        ]:
            tag = node.select_one(selector)
            if not tag:
                continue
            candidate = tag.get_text(" ", strip=True)
            if (
                keyword in candidate.lower()
                or re.search(r'\d{1,2}[./-]\d{1,2}', candidate)
            ):
                title = candidate
                break

        if not title:
            title = text[:160]

        detail_url = ""
        for a in node.find_all("a", href=True):
            href = str(a["href"])
            if "board_detail" in href:
                detail_url = urljoin(current_url, href)
                break

        photos = extract_image_urls(str(node))
        key = detail_url or f"{dt:%Y%m%d}|{title}"

        result.setdefault(
            key,
            {
                "date": dt.strftime("%Y-%m-%d"),
                "sort_key": dt.strftime("%Y%m%d"),
                "title": title.strip(),
                "body_text": text.strip(),
                "detail_url": detail_url,
                "photos": photos,
                "key": re.sub(r'\W+', '', key)[-50:] or dt.strftime("%Y%m%d")
            }
        )

    return list(result.values())

def enrich_detail(post):
    if not post["detail_url"]:
        return post

    try:
        r = session.get(post["detail_url"], timeout=30)
        if r.status_code != 200:
            return post

        soup = BeautifulSoup(r.text, "html.parser")

        generic_titles = {
            "조황관리 HOME / 조황관리 / 상세정보",
            "조황관리",
            "상세정보",
        }

        for selector in [
            ".title",".view_title",".subject",
            "h1","h2","h3"
        ]:
            tag = soup.select_one(selector)
            if tag:
                text = tag.get_text(" ", strip=True)
                if len(text) >= 5 and text not in generic_titles:
                    post["title"] = text
                    break

        for selector in [
            ".editor",".view_content",
            ".view-content",".board_view_content",
            ".content"
        ]:
            node = soup.select_one(selector)
            if node:
                text = node.get_text("\n", strip=True)
                if len(text) >= 20:
                    lines = [
                        line.strip()
                        for line in text.splitlines()
                        if line.strip()
                    ]
                    post["body_text"] = "\n".join(lines)

                    if (
                        post.get("title") in generic_titles
                        or post.get("title","").startswith("조황관리 ")
                    ):
                        for line in lines:
                            if (
                                len(line) >= 8
                                and "예약문의" not in line
                                and "오시는길" not in line
                                and "홈페이지" not in line
                            ):
                                post["title"] = line[:100]
                                break
                    break

        detail_photos = extract_image_urls(r.text)
        if detail_photos:
            post["photos"] = detail_photos

    except Exception:
        pass

    return post

def collect_source(source):
    start_dt = datetime.strptime(
        source["start_date"],
        "%Y-%m-%d"
    )

    today = date.today()
    end_dt = datetime(today.year, today.month, today.day)
    max_posts = int(source.get("max_posts", 30))

    collected = {}

    for page in range(1, 11):
        if len(collected) >= max_posts:
            break

        url = page_url(source["board_url"], page)

        try:
            r = session.get(url, timeout=30)
        except Exception:
            break

        if r.status_code != 200:
            break

        posts = extract_list_posts(
            r.text,
            source,
            url,
            start_dt,
            end_dt
        )

        for post in posts:
            collected.setdefault(post["key"], post)

        if not posts and page >= 3:
            break

        time.sleep(0.15)

    posts = sorted(
        collected.values(),
        key=lambda x: x["sort_key"],
        reverse=True
    )[:max_posts]

    output = []

    for post in posts:
        post = enrich_detail(post)

        month = post["date"][:7].replace("-", "")
        filtered = []

        for url in post["photos"]:
            m = re.search(r'/ship_board/(\d{6})/', url)
            if not m or m.group(1) == month:
                filtered.append(url)

        post["photos"] = list(dict.fromkeys(filtered))

        if post["photos"]:
            output.append(post)

    return output

def safe_filename(title, key):
    value = re.sub(r'[\\/:*?"<>|]', '_', title or "조황")
    value = re.sub(r'\s+', ' ', value).strip()
    return f"{value[:80]}_{key}.html"

def nav(prefix=""):
    return (
        '<nav class="nav">'
        f'<a href="{prefix}index.html">조황</a>'
        f'<a href="{prefix}guide.html">이용안내</a>'
        f'<a href="{prefix}about.html">소개</a>'
        f'<a href="{prefix}privacy.html">개인정보처리방침</a>'
        f'<a href="{prefix}articles.html">낚시가이드</a>'
        f'<a href="{prefix}contact.html">문의</a>'
        '</nav>'
    )

def build():
    cfg = load_json(CFG, {"sources":[]})
    sources = [
        x for x in cfg.get("sources", [])
        if x.get("enabled", True)
    ]

    if DOCS.exists():
        shutil.rmtree(DOCS)

    (DOCS / "assets" / "photos").mkdir(parents=True)
    (DOCS / "posts").mkdir()
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    css = """
*{box-sizing:border-box}
:root{--bg:#f4f7f9;--card:#fff;--text:#14202a;--muted:#677681;--accent:#076b98;--line:#dce5ea}
body{margin:0;font-family:Arial,'Malgun Gothic',sans-serif;background:var(--bg);color:var(--text)}
a{text-decoration:none;color:inherit}.wrap{width:min(1180px,calc(100% - 30px));margin:auto}
header{background:#fff;border-bottom:1px solid var(--line)}header .wrap{display:flex;justify-content:space-between;gap:18px;align-items:center;padding:17px 0}
.logo{font-weight:900;color:var(--accent);font-size:22px}.nav{display:flex;gap:14px;flex-wrap:wrap;font-size:14px;font-weight:700}
.hero{padding:36px 0 20px}.hero h1{font-size:clamp(30px,6vw,50px);margin-bottom:10px}.hero p{color:var(--muted);line-height:1.7}
.tabs{display:flex;gap:9px;flex-wrap:wrap}.tab{padding:10px 15px;border:1px solid var(--line);border-radius:999px;background:#fff;font-weight:800;cursor:pointer}.tab.active{background:var(--accent);color:#fff}
.filters{display:flex;gap:10px;margin:16px 0}.filters input{padding:11px;border:1px solid var(--line);border-radius:10px;background:#fff}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;padding:12px 0 50px}.card{background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 10px 28px #0001}
.thumb{aspect-ratio:4/3;position:relative;background:#ddd}.thumb img{width:100%;height:100%;object-fit:cover}.badge{position:absolute;right:10px;bottom:10px;background:#000a;color:#fff;border-radius:999px;padding:6px 9px;font-size:12px}
.body{padding:17px}.date{color:var(--accent);font-weight:800;font-size:13px}.body h2{font-size:18px;line-height:1.45}.preview{color:var(--muted);line-height:1.65;font-size:14px}
.hidden{display:none!important}.content{padding:36px 0 60px}.content p,.content li{line-height:1.9;color:#3f505b}.detail-text{background:#fff;padding:24px;border-radius:18px;line-height:1.95;margin:20px 0}
.gallery{display:grid;gap:16px}.gallery img{width:100%;border-radius:14px}.source{color:var(--accent);font-weight:800}footer{background:#fff;border-top:1px solid var(--line);padding:28px 0;color:var(--muted);font-size:13px}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:650px){header .wrap{flex-direction:column;align-items:flex-start}.grid{grid-template-columns:1fr}.filters{display:grid}}
"""
    (DOCS/"assets"/"style.css").write_text(css, encoding="utf-8")

    tabs = ['<button class="tab active" data-source="all">전체</button>']
    cards = []
    sitemap_paths = ["index.html","about.html","guide.html","privacy.html","contact.html","articles.html","article-catch-reading.html","article-trip-checklist.html","article-comparison.html"]

    for source in sources:
        posts = collect_source(source)

        tabs.append(
            f'<button class="tab" data-source="{esc(source["id"])}">'
            f'{esc(source["name"])} ({len(posts)})</button>'
        )

        for post in posts:
            local_photos = []

            for idx, image_url in enumerate(post["photos"], 1):
                ext = ".png" if ".png" in image_url.lower() else ".jpg"
                filename = f'{source["id"]}_{post["key"]}_{idx:02d}{ext}'
                output_path = DOCS / "assets" / "photos" / filename

                if download_image(image_url, output_path):
                    local_photos.append(filename)

            if not local_photos:
                continue

            filename = safe_filename(
                post["title"],
                post["key"]
            )
            sitemap_paths.append("posts/" + filename)

            detail = f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(post["title"])}</title>
<meta name="description" content="{esc(post["body_text"][:150])}">
<link rel="stylesheet" href="../assets/style.css">
</head>
<body>
<header><div class="wrap"><a class="logo" href="../index.html">선상 조황 모아보기</a>{nav("../")}</div></header>
<main class="wrap content">
<div class="date">{post["date"]} · {esc(source["name"])}</div>
<h1>{esc(post["title"])}</h1>
<p><a class="source" href="{esc(post["detail_url"])}" target="_blank" rel="noopener">선상24 원문 보기</a></p>
<div class="detail-text">{'<br>'.join(esc(post["body_text"]).splitlines())}</div>
<div class="gallery">{''.join(f'<img src="../assets/photos/{esc(x)}" alt="{esc(post["title"])}" loading="lazy">' for x in local_photos)}</div>
</main>
<footer><div class="wrap">© 2026 선상 조황 모아보기</div></footer>
</body></html>'''

            (DOCS/"posts"/filename).write_text(
                detail,
                encoding="utf-8"
            )

            search = (
                source["name"] + " " +
                post["title"] + " " +
                post["body_text"]
            ).lower()

            cards.append(
                f'''<article class="card source-card"
data-source="{esc(source["id"])}"
data-date="{post["date"]}"
data-search="{esc(search)}">
<a href="posts/{quote(filename)}">
<div class="thumb">
<img src="assets/photos/{esc(local_photos[0])}" alt="{esc(post["title"])}" loading="lazy">
<span class="badge">사진 {len(local_photos)}장</span>
</div>
<div class="body">
<div class="date">{post["date"]} · {esc(source["name"])}</div>
<h2>{esc(post["title"])}</h2>
<p class="preview">{esc(post["body_text"].replace(chr(10)," ")[:140])}</p>
</div>
</a>
</article>'''
            )

    index = f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>선상 조황 모아보기</title>
<meta name="description" content="안흥 스페이스호와 군산 뚱스호의 최신 선상낚시 조황을 날짜별·선박별로 정리합니다.">
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header><div class="wrap"><a class="logo" href="index.html">선상 조황 모아보기</a>{nav("")}</div></header>
<main class="wrap">
<section class="hero">
<h1>최신 선상 조황을 한눈에</h1>
<p>선박별·날짜별 공개 조황을 정리하고 각 상세페이지에서 원문 출처를 확인할 수 있습니다.</p>
</section>
<div class="tabs">{''.join(tabs)}</div>
<div class="filters"><input id="search" type="search" placeholder="선박명·제목·본문 검색"><input id="date" type="date"></div>
<p><strong id="count"></strong></p>
<section class="grid">{''.join(cards)}</section>
</main>
<footer><div class="wrap">© 2026 선상 조황 모아보기</div></footer>
<script>
const tabs=[...document.querySelectorAll('.tab')],cards=[...document.querySelectorAll('.source-card')];
const q=document.getElementById('search'),d=document.getElementById('date'),count=document.getElementById('count');
let current='all';
function apply(){{
 let n=0;
 const query=(q.value||'').toLowerCase();
 cards.forEach(card=>{{
  const ok=(current==='all'||card.dataset.source===current)&&(!query||card.dataset.search.includes(query))&&(!d.value||card.dataset.date===d.value);
  card.classList.toggle('hidden',!ok);
  if(ok)n++;
 }});
 count.textContent='조황 '+n+'건';
}}
tabs.forEach(tab=>tab.onclick=()=>{{current=tab.dataset.source;tabs.forEach(x=>x.classList.toggle('active',x===tab));apply();}});
q.oninput=apply;d.onchange=apply;apply();
</script>
</body></html>'''

    (DOCS/"index.html").write_text(index, encoding="utf-8")

    def simple_page(name, title, body):
        html = f'''<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} | 선상 조황 모아보기</title>
<link rel="stylesheet" href="assets/style.css">
</head><body>
<header><div class="wrap"><a class="logo" href="index.html">선상 조황 모아보기</a>{nav("")}</div></header>
<main class="wrap content"><h1>{esc(title)}</h1>{body}</main>
<footer><div class="wrap">© 2026 선상 조황 모아보기</div></footer>
</body></html>'''
        (DOCS/name).write_text(html, encoding="utf-8")

    simple_page(
        "about.html",
        "사이트 소개",
        "<p>여러 선사의 공개 조황을 선박과 날짜 기준으로 정리해 최근 현장 상황을 편하게 비교할 수 있도록 돕는 정보 사이트입니다.</p>"
        "<p>각 조황 상세페이지에는 원문 출처 링크를 제공하며, 정보 탐색을 돕기 위한 검색과 날짜 필터 기능을 운영합니다.</p>"
    )

    simple_page(
        "guide.html",
        "이용안내",
        "<p>선박 탭, 검색창, 날짜 선택 기능으로 원하는 조황을 찾을 수 있습니다.</p>"
        "<ul><li>실제 조과는 기상, 물때, 포인트, 승선 인원 등에 따라 달라질 수 있습니다.</li>"
        "<li>예약·출항 여부·준비물은 해당 선사의 최신 안내를 다시 확인하세요.</li>"
        "<li>각 상세페이지에서 선상24 원문을 확인할 수 있습니다.</li></ul>"
    )

    simple_page(
        "privacy.html",
        "개인정보처리방침",
        "<p>본 사이트는 서비스 운영 과정에서 호스팅 제공업체가 보안 및 운영 목적으로 접속 로그를 처리할 수 있습니다.</p>"
        "<p>향후 Google AdSense 등 제3자 광고 서비스를 사용할 경우 쿠키가 광고 제공 및 측정을 위해 사용될 수 있으며, 관련 내용을 본 방침에 반영합니다.</p>"
    )

    simple_page(
        "contact.html",
        "문의",
        "<p>게시물 수정·삭제, 권리 관련 요청, 잘못된 정보 제보를 위한 운영 이메일을 정식 공개 전에 이 페이지에 추가할 예정입니다.</p>"
        "<p>문의 시 게시물 제목과 페이지 주소를 함께 적어 주시면 확인에 도움이 됩니다.</p>"
    )

    simple_page(
        "articles.html",
        "낚시가이드",
        "<p>조황을 단순히 사진 수로만 판단하지 않고 실제 출조에 도움이 되도록 읽는 방법을 정리했습니다.</p>"
        "<h2><a class='source' href='article-catch-reading.html'>조황 사진과 글을 읽는 방법</a></h2>"
        "<p>사진 장수, 마릿수 표현, 날짜, 출조 형태를 함께 보는 기준을 설명합니다.</p>"
        "<h2><a class='source' href='article-trip-checklist.html'>선상낚시 출조 전 체크리스트</a></h2>"
        "<p>예약 전부터 출항 당일까지 확인하면 좋은 항목을 정리했습니다.</p>"
        "<h2><a class='source' href='article-comparison.html'>여러 선박 조황을 비교할 때 보는 기준</a></h2>"
        "<p>같은 날짜의 조황도 조건이 다를 수 있으므로 공정하게 비교하는 방법을 안내합니다.</p>"
    )

    simple_page(
        "article-catch-reading.html",
        "조황 사진과 글을 읽는 방법",
        "<p>조황 게시물은 다음 출조지를 고르는 데 유용하지만, 사진 한 장이나 제목만 보고 판단하면 실제 상황과 차이가 날 수 있습니다.</p>"
        "<h2>1. 날짜와 출조 형태를 먼저 확인</h2>"
        "<p>종일반·오전반·야간반처럼 출조 시간이 다르면 같은 마릿수라도 의미가 달라집니다. 먼저 실제 출조 날짜와 운항 형태를 확인하세요.</p>"
        "<h2>2. 장원 기록과 전체 평균을 구분</h2>"
        "<p>‘장원 300수’처럼 가장 많이 잡은 한 사람의 기록과 전체 승선객의 평균 조과는 다릅니다. 제목의 최고 기록만으로 전체 조황을 판단하지 않는 것이 좋습니다.</p>"
        "<h2>3. 사진 장수는 조과의 절대 기준이 아님</h2>"
        "<p>선사가 사진을 많이 올리는 날도 있고 대표사진만 올리는 날도 있습니다. 사진 수보다 본문의 마릿수 표현과 현장 설명을 함께 확인하세요.</p>"
        "<h2>4. 연속된 여러 날짜를 확인</h2>"
        "<p>하루의 좋은 조황보다 3~5일 정도의 흐름을 같이 보면 해당 시기의 안정적인 조과인지 판단하기 쉽습니다.</p>"
    )

    simple_page(
        "article-trip-checklist.html",
        "선상낚시 출조 전 체크리스트",
        "<p>조황이 좋아 보여도 실제 출조 준비가 맞지 않으면 불편할 수 있습니다. 예약 전과 출항 전에 아래 항목을 확인하세요.</p>"
        "<h2>예약 전</h2>"
        "<ul><li>대상 어종과 출조 시간</li><li>출항지와 집결 시간</li><li>선비와 취소·환불 규정</li><li>장비 대여 가능 여부</li></ul>"
        "<h2>출항 전날</h2>"
        "<ul><li>기상과 출항 여부</li><li>신분증과 필요한 개인 장비</li><li>멀미약 복용 시점</li><li>방수·방풍 의류와 여벌 옷</li></ul>"
        "<h2>현장 도착 후</h2>"
        "<p>선장의 안전 안내와 자리 배정, 채비 규정을 먼저 확인하고 다른 승선객의 낚시 공간을 침범하지 않도록 준비하는 것이 좋습니다.</p>"
    )

    simple_page(
        "article-comparison.html",
        "여러 선박 조황을 비교할 때 보는 기준",
        "<p>같은 지역의 선박이라도 조황 게시물은 서로 다른 방식으로 작성됩니다. 비교할 때는 동일한 기준을 맞추는 것이 중요합니다.</p>"
        "<h2>같은 날짜 또는 비슷한 물때끼리 비교</h2>"
        "<p>일주일 이상 차이가 나는 조황은 수온과 어군 상황이 달라질 수 있습니다. 가능한 한 같은 날짜나 가까운 날짜끼리 비교하세요.</p>"
        "<h2>대상 어종과 출조 시간을 맞추기</h2>"
        "<p>쭈꾸미 종일반과 갑오징어 오후반처럼 조건이 다른 출조는 단순 마릿수로 비교하기 어렵습니다.</p>"
        "<h2>최고 기록보다 반복성을 보기</h2>"
        "<p>한 번의 큰 조과보다 여러 날짜에 걸쳐 꾸준히 비슷한 결과가 나오는지 확인하면 보다 현실적인 판단에 도움이 됩니다.</p>"
        "<h2>원문 확인</h2>"
        "<p>본 사이트는 빠른 비교를 돕기 위한 정리 서비스입니다. 예약이나 출항 결정을 내리기 전에는 각 상세페이지의 선상24 원문 링크에서 최신 안내를 다시 확인하세요.</p>"
    )

    (DOCS/"robots.txt").write_text(
        f"User-agent: *\\nAllow: /\\nSitemap: {BASE_URL}/sitemap.xml\\n",
        encoding="utf-8"
    )

    sitemap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    for path in sitemap_paths:
        sitemap.append(
            f"<url><loc>{BASE_URL}/{quote(path)}</loc></url>"
        )
    sitemap.append("</urlset>")

    (DOCS/"sitemap.xml").write_text(
        "\\n".join(sitemap),
        encoding="utf-8"
    )

    (DOCS/"ads.txt").write_text(
        "# AdSense 승인 전입니다. Publisher ID 발급 후 업데이트합니다.\\n",
        encoding="utf-8"
    )

    print("docs 생성 완료")

if __name__ == "__main__":
    build()
