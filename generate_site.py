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
        f'<a href="{prefix}boats.html">지역별 선박</a>'
        f'<a href="{prefix}gear.html">장비 선택</a>'
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
.gallery{display:grid;gap:16px}.gallery img{width:100%;border-radius:14px}.source{color:var(--accent);font-weight:800}
.info-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin:22px 0}.info-card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 8px 20px #0000000a}.info-card h2,.info-card h3{margin-top:0}.tag{display:inline-block;padding:5px 9px;border-radius:999px;background:#e9f5fa;color:var(--accent);font-size:12px;font-weight:800;margin:2px}.spec-table{width:100%;border-collapse:collapse;background:#fff;margin:18px 0}.spec-table th,.spec-table td{border:1px solid var(--line);padding:12px;text-align:left;vertical-align:top}.spec-table th{background:#eef5f8}.note{background:#fff7df;border:1px solid #f0dfad;border-radius:14px;padding:16px;line-height:1.7}
footer{background:#fff;border-top:1px solid var(--line);padding:28px 0;color:var(--muted);font-size:13px}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:650px){header .wrap{flex-direction:column;align-items:flex-start}.grid,.info-grid{grid-template-columns:1fr}.filters{display:grid}.spec-table{font-size:14px}}
"""
    (DOCS/"assets"/"style.css").write_text(css, encoding="utf-8")

    tabs = ['<button class="tab active" data-source="all">전체</button>']
    cards = []
    sitemap_paths = ["index.html","about.html","guide.html","privacy.html","contact.html","articles.html","article-catch-reading.html","article-trip-checklist.html","article-comparison.html","boats.html","boat-anheung.html","boat-gunsan.html","gear.html","gear-jjukkumi.html","gear-cuttlefish.html","gear-flounder.html","gear-octopus.html"]

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

    simple_page(
        "boats.html",
        "지역별 인기 선박",
        "<p>지역별 선박을 찾을 때는 단순히 한 번의 대박 조황보다 최근 조황 업데이트, 대상 어종, 출항지, 출조 형태를 함께 확인하는 것이 좋습니다.</p>"
        "<div class='note'>현재 이 페이지의 '인기/주요 선박' 표시는 우리 사이트가 실제로 조황을 수집·추적 중인 선박을 중심으로 구성합니다. 향후 예약 빈도, 조황 업데이트 빈도, 이용자 관심도 같은 객관 지표를 추가해 고도화할 예정입니다.</div>"
        "<div class='info-grid'>"
        "<div class='info-card'><span class='tag'>충남 태안</span><span class='tag'>안흥항</span><h2>안흥 스페이스호</h2><p>현재 갑오징어·주꾸미 조황을 추적 중입니다. 최근 조황과 사진을 메인 조황 탭에서 날짜별로 확인할 수 있습니다.</p><p><a class='source' href='boat-anheung.html'>안흥 지역 보기 →</a></p></div>"
        "<div class='info-card'><span class='tag'>전북 군산</span><h2>군산 뚱스호</h2><p>군산권 조황을 추적 중인 선박입니다. 메인 조황과 연동해 최근 게시물 흐름을 확인할 수 있습니다.</p><p><a class='source' href='boat-gunsan.html'>군산 지역 보기 →</a></p></div>"
        "</div>"
        "<h2>선박을 고를 때 보는 기준</h2>"
        "<ul><li>최근 3~5회 조황이 꾸준한지</li><li>내가 노리는 대상 어종을 주력으로 출조하는지</li><li>집결 시간과 출항지가 이동 동선에 맞는지</li><li>예약 취소 규정과 장비 대여 여부가 명확한지</li><li>조황 게시물이 날짜·마릿수·현장 상황을 구체적으로 설명하는지</li></ul>"
    )

    simple_page(
        "boat-anheung.html",
        "안흥·태안 선박 가이드",
        "<p>안흥항은 서해 중부권 선상낚시 출항지 중 하나로, 시즌에 따라 주꾸미·갑오징어 등 다양한 출조가 운영됩니다.</p>"
        "<h2>현재 추적 선박</h2><div class='info-card'><h3>안흥 스페이스호</h3><p>우리 사이트에서 최신 조황을 자동 수집 중입니다. 메인 화면의 ‘안흥 스페이스호’ 탭에서 최근 날짜 순으로 확인할 수 있습니다.</p><p><a class='source' href='index.html'>최신 조황 보기 →</a></p></div>"
        "<h2>출조 전 확인</h2><ul><li>안흥항 집결 위치와 주차 안내</li><li>대상 어종별 봉돌 호수와 권장 채비</li><li>기상 악화 시 출항 취소 여부</li><li>신분증 및 개인 구명장비 관련 안내</li></ul>"
    )

    simple_page(
        "boat-gunsan.html",
        "군산 선박 가이드",
        "<p>군산권은 서해 남부권 선상낚시의 대표적인 출항 지역 중 하나입니다. 출조 어종과 포인트는 시즌에 따라 달라질 수 있습니다.</p>"
        "<h2>현재 추적 선박</h2><div class='info-card'><h3>군산 뚱스호</h3><p>우리 사이트에서 최신 조황을 자동 수집 중입니다. 메인 화면의 ‘군산 뚱스호’ 탭에서 최근 흐름을 확인할 수 있습니다.</p><p><a class='source' href='index.html'>최신 조황 보기 →</a></p></div>"
        "<h2>비교할 때 체크할 점</h2><ul><li>같은 날짜 또는 비슷한 물때의 조황끼리 비교</li><li>장원 기록과 전체 평균을 구분</li><li>출조 시간과 대상 어종이 같은 조건인지 확인</li></ul>"
    )

    simple_page(
        "gear.html",
        "낚시 장비 선택",
        "<p>선상낚시는 대상 어종에 따라 로드, 릴, 합사, 쇼크리더, 봉돌과 에기의 조합이 달라집니다. 아래 가이드는 제품 추천보다 '선택 기준'에 초점을 맞춥니다.</p>"
        "<div class='info-grid'>"
        "<div class='info-card'><h2>주꾸미</h2><p>가벼운 채비와 바닥 감도가 중요합니다.</p><a class='source' href='gear-jjukkumi.html'>주꾸미 장비 가이드 →</a></div>"
        "<div class='info-card'><h2>갑오징어</h2><p>입질 감도와 에기 운용, 채비 밸런스를 봅니다.</p><a class='source' href='gear-cuttlefish.html'>갑오징어 장비 가이드 →</a></div>"
        "<div class='info-card'><h2>광어 다운샷</h2><p>봉돌 중량 대응력과 허리힘, 릴링 안정성이 중요합니다.</p><a class='source' href='gear-flounder.html'>광어 장비 가이드 →</a></div>"
        "<div class='info-card'><h2>문어</h2><p>무거운 채비와 바닥 걸림에 버틸 파워가 필요합니다.</p><a class='source' href='gear-octopus.html'>문어 장비 가이드 →</a></div>"
        "</div>"
        "<div class='note'>배마다 권장 봉돌 호수와 채비 규정이 다를 수 있으므로 출조 전 선사의 공지를 우선 확인하세요.</div>"
    )

    simple_page(
        "gear-jjukkumi.html",
        "주꾸미 선상낚시 장비 선택",
        "<p>주꾸미는 바닥을 읽고 작은 무게 변화를 느끼는 낚시라 전체 장비를 지나치게 무겁게 구성하지 않는 것이 편합니다.</p>"
        "<table class='spec-table'><tr><th>항목</th><th>선택 기준</th></tr>"
        "<tr><td>로드</td><td>짧고 가벼우며 초릿대 감도가 좋은 선상 전용대. 봉돌 운용 범위를 먼저 확인합니다.</td></tr>"
        "<tr><td>릴</td><td>소형 베이트릴이 일반적이며 장시간 사용 시 무게와 손에 잡히는 크기를 중요하게 봅니다.</td></tr>"
        "<tr><td>합사</td><td>가늘수록 조류 저항은 줄지만 내구성과 엉킴 대응도 고려해야 합니다.</td></tr>"
        "<tr><td>에기/채비</td><td>물색과 조류에 따라 색상과 크기를 바꿀 수 있도록 여러 종류를 준비합니다.</td></tr></table>"
        "<h2>초보자가 우선할 것</h2><p>고가 장비보다 내가 자주 타는 배의 봉돌 범위를 안정적으로 커버하고, 하루 종일 들고 있어도 부담이 적은 조합이 우선입니다.</p>"
    )

    simple_page(
        "gear-cuttlefish.html",
        "갑오징어 선상낚시 장비 선택",
        "<p>갑오징어는 주꾸미보다 채비 무게 변화와 당기는 힘이 크게 느껴질 수 있어 감도와 버티는 힘의 균형이 중요합니다.</p>"
        "<table class='spec-table'><tr><th>항목</th><th>선택 기준</th></tr>"
        "<tr><td>로드</td><td>초릿대는 입질을 표현하고 허리 부분은 갑오징어의 무게를 안정적으로 들어 올릴 수 있어야 합니다.</td></tr>"
        "<tr><td>릴</td><td>소형 베이트릴을 많이 사용하며 드랙 조절과 일정한 릴링이 편한 제품이 유리합니다.</td></tr>"
        "<tr><td>라인</td><td>조류 저항과 바닥 감도를 고려해 합사를 선택하고 필요하면 쇼크리더를 사용합니다.</td></tr>"
        "<tr><td>에기</td><td>활성도와 물색 변화에 대응할 수 있도록 색상·침강 특성이 다른 에기를 준비합니다.</td></tr></table>"
    )

    simple_page(
        "gear-flounder.html",
        "광어 다운샷 장비 선택",
        "<p>광어 다운샷은 상대적으로 무거운 봉돌을 사용하고 큰 개체의 저항을 받아내야 하므로 로드의 허리힘과 릴의 안정성이 중요합니다.</p>"
        "<table class='spec-table'><tr><th>항목</th><th>선택 기준</th></tr>"
        "<tr><td>로드</td><td>출조선이 사용하는 봉돌 중량을 충분히 소화하면서 입질 표현도 확인할 수 있는 다운샷 전용대가 편합니다.</td></tr>"
        "<tr><td>릴</td><td>중형 베이트릴 계열이 많이 쓰이며 충분한 라인 용량과 안정적인 드랙을 봅니다.</td></tr>"
        "<tr><td>라인</td><td>합사 강도는 대상 크기와 포인트 여건에 맞추고, 쓸림이 많은 곳은 리더 내구성을 신경 씁니다.</td></tr></table>"
    )

    simple_page(
        "gear-octopus.html",
        "문어 선상낚시 장비 선택",
        "<p>문어 낚시는 무거운 봉돌과 문어의 흡착력을 동시에 상대해야 해서 전체적으로 강한 장비가 필요합니다.</p>"
        "<table class='spec-table'><tr><th>항목</th><th>선택 기준</th></tr>"
        "<tr><td>로드</td><td>무거운 채비를 반복적으로 들어 올려도 버틸 수 있는 파워와 허리힘을 우선합니다.</td></tr>"
        "<tr><td>릴</td><td>강한 권상력과 내구성, 충분한 라인 용량을 중점적으로 봅니다.</td></tr>"
        "<tr><td>라인</td><td>바닥 걸림과 강제 제압 상황이 있으므로 지나치게 가는 합사보다 내구성을 우선합니다.</td></tr>"
        "<tr><td>채비</td><td>포인트의 바닥 형태와 선사의 권장 봉돌 호수를 확인해 여유 있게 준비합니다.</td></tr></table>"
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
