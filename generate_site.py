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
        f'<a href="{prefix}autumn.html">가을낚시</a>'
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
header{background:#fff;border-bottom:1px solid var(--line);box-shadow:0 3px 14px rgba(0,0,0,.035)}header .wrap{display:flex;justify-content:space-between;gap:28px;align-items:center;padding:22px 0}
.logo{display:inline-flex;align-items:center;font-weight:900;color:#075f87;font-size:40px;letter-spacing:-1px;white-space:nowrap;text-shadow:0 1px 0 rgba(255,255,255,.7)}
.nav{display:flex;gap:14px;flex-wrap:wrap;align-items:center}
.nav a{display:inline-flex;align-items:center;justify-content:center;min-height:68px;padding:0 28px;border-radius:999px;background:#0a6792;border:2px solid #05577c;color:#fff;font-size:22px;font-weight:900;letter-spacing:-.3px;box-shadow:0 6px 14px rgba(7,95,135,.24);transition:all .16s ease}
.nav a:hover{background:#064968;border-color:#064968;color:#fff;transform:translateY(-2px);box-shadow:0 10px 20px rgba(7,95,135,.30)}
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
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:650px){header .wrap{flex-direction:column;align-items:flex-start;padding:18px 0}.logo{font-size:32px}.nav{gap:10px}.nav a{min-height:54px;padding:0 20px;font-size:18px}.grid,.info-grid{grid-template-columns:1fr}.filters{display:grid}.spec-table{font-size:14px}}
"""
    (DOCS/"assets"/"style.css").write_text(css, encoding="utf-8")

    tabs = ['<button class="tab active" data-source="all">전체</button>']
    cards = []
    sitemap_paths = ["index.html","about.html","guide.html","privacy.html","contact.html","articles.html","article-catch-reading.html","article-trip-checklist.html","article-comparison.html","boats.html","boat-anheung.html","boat-gunsan.html","boat-samgilpo.html","boat-ocheon.html","boat-muchangpo.html","boat-incheon.html","boat-pyeongtaek.html","boat-mokpo.html","boat-yeosu.html","autumn.html","autumn-cuttlefish.html","autumn-jjukkumi.html","autumn-octopus.html","autumn-cuttlefish-egi.html","autumn-jjukkumi-sinker.html","autumn-octopus-rig.html","autumn-tide.html","autumn-beginner-mistakes.html","autumn-checklist.html","gear.html","gear-jjukkumi.html","gear-cuttlefish.html","gear-octopus.html","gear-beginner.html","gear-intermediate.html","gear-buying-checklist.html"]

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
<meta name="description" content="가을 선상낚시 갑오징어·주꾸미·문어 조황과 지역별 선박, 장비 선택 정보를 정리합니다.">
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header><div class="wrap"><a class="logo" href="index.html">선상 조황 모아보기</a>{nav("")}</div></header>
<main class="wrap">
<section class="hero">
<h1>최신 선상 조황을 한눈에</h1>
<p>가을 선상낚시의 핵심 어종인 갑오징어·주꾸미·문어를 중심으로 조황, 지역별 선박, 장비 선택 정보를 정리합니다.</p>
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
        "지역별 선박·출항지",
        "<p>지역별 선박을 찾을 때는 단순히 한 번의 대박 조황보다 최근 조황 업데이트, 대상 어종, 출항지, 출조 형태를 함께 확인하는 것이 좋습니다.</p>"
        "<div class='note'>현재 이 페이지의 '인기/주요 선박' 표시는 우리 사이트가 실제로 조황을 수집·추적 중인 선박을 중심으로 구성합니다. 향후 예약 빈도, 조황 업데이트 빈도, 이용자 관심도 같은 객관 지표를 추가해 고도화할 예정입니다.</div>"
        "<div class='info-grid'>"
        "<div class='info-card'><span class='tag'>충남 태안</span><span class='tag'>안흥항</span><h2>안흥 스페이스호</h2><p>현재 갑오징어·주꾸미 조황을 추적 중입니다. 최근 조황과 사진을 메인 조황 탭에서 날짜별로 확인할 수 있습니다.</p><p><a class='source' href='boat-anheung.html'>안흥 지역 보기 →</a></p></div>"
        "<div class='info-card'><span class='tag'>전북 군산</span><h2>군산 뚱스호</h2><p>군산권 조황을 추적 중인 선박입니다. 메인 조황과 연동해 최근 게시물 흐름을 확인할 수 있습니다.</p><p><a class='source' href='boat-gunsan.html'>군산 지역 보기 →</a></p></div>"
        "<div class='info-card'><span class='tag'>충남 서산</span><h2>삼길포</h2><p>서해권 선상낚시 출항지로 많이 찾는 지역 중 하나입니다. 선박별 출조 어종과 집결 조건을 확인해 비교하세요.</p><p><a class='source' href='boat-samgilpo.html'>삼길포 가이드 →</a></p></div>"
        "<div class='info-card'><span class='tag'>충남 보령</span><h2>오천·무창포</h2><p>보령권 선상낚시를 찾을 때 함께 비교하기 좋은 출항지입니다.</p><p><a class='source' href='boat-ocheon.html'>오천 →</a> · <a class='source' href='boat-muchangpo.html'>무창포 →</a></p></div>"
        "<div class='info-card'><span class='tag'>수도권</span><h2>인천·평택</h2><p>수도권에서 접근성을 중시할 때 살펴볼 수 있는 출항권입니다.</p><p><a class='source' href='boat-incheon.html'>인천 →</a> · <a class='source' href='boat-pyeongtaek.html'>평택 →</a></p></div>"
        "<div class='info-card'><span class='tag'>서남해</span><h2>목포·여수</h2><p>서남해권은 계절과 대상 어종에 따라 출조 선택지가 달라집니다.</p><p><a class='source' href='boat-mokpo.html'>목포 →</a> · <a class='source' href='boat-yeosu.html'>여수 →</a></p></div>"
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
        "autumn.html",
        "가을 선상낚시 특집",
        "<p>가을에는 서해권을 중심으로 갑오징어·주꾸미·문어 출조를 많이 찾습니다. 이 사이트는 당분간 이 세 어종의 조황과 장비, 출조 준비 정보를 집중적으로 다룹니다.</p>"
        "<div class='info-grid'>"
        "<div class='info-card'><span class='tag'>갑오징어</span><h2>갑오징어 시즌 가이드</h2><p>조황을 읽는 법, 장비 밸런스, 에기 운용 포인트를 정리합니다.</p><a class='source' href='autumn-cuttlefish.html'>갑오징어 가이드 →</a></div>"
        "<div class='info-card'><span class='tag'>주꾸미</span><h2>주꾸미 시즌 가이드</h2><p>마릿수 조황을 볼 때 주의할 점과 가벼운 장비 구성 기준을 설명합니다.</p><a class='source' href='autumn-jjukkumi.html'>주꾸미 가이드 →</a></div>"
        "<div class='info-card'><span class='tag'>문어</span><h2>문어 시즌 가이드</h2><p>강한 장비와 무거운 채비를 사용하는 문어 낚시의 준비 포인트를 정리합니다.</p><a class='source' href='autumn-octopus.html'>문어 가이드 →</a></div>"
        "</div>"
        "<h2>가을 조황을 비교할 때</h2>"
        "<ul><li>갑오징어와 주꾸미는 같은 날이라도 포인트와 물때에 따라 차이가 큽니다.</li><li>장원 기록과 전체 평균을 구분해서 봅니다.</li><li>사진 장수보다 본문의 마릿수·씨알·활성도 설명을 같이 확인합니다.</li><li>문어는 개체 크기와 채비 중량, 바닥 형태를 함께 고려해야 합니다.</li></ul>"
        "<h2>세부 실전 가이드</h2>"
        "<div class='info-grid'>"
        "<div class='info-card'><h3>갑오징어 에기 선택</h3><p>색상, 크기, 침강 특성을 어떻게 나눠 준비할지 정리합니다.</p><a class='source' href='autumn-cuttlefish-egi.html'>에기 선택 보기 →</a></div>"
        "<div class='info-card'><h3>주꾸미 봉돌·채비</h3><p>봉돌 무게와 조류, 바닥감의 관계를 설명합니다.</p><a class='source' href='autumn-jjukkumi-sinker.html'>봉돌·채비 보기 →</a></div>"
        "<div class='info-card'><h3>문어 채비</h3><p>강한 채비와 바닥 걸림에 대응하는 기준을 정리합니다.</p><a class='source' href='autumn-octopus-rig.html'>문어 채비 보기 →</a></div>"
        "<div class='info-card'><h3>물때별 조황 보는 법</h3><p>조황을 물때와 함께 볼 때 주의할 점을 정리합니다.</p><a class='source' href='autumn-tide.html'>물때 가이드 →</a></div>"
        "<div class='info-card'><h3>초보자 실수</h3><p>가을 선상낚시 초보자가 자주 겪는 실수를 미리 정리합니다.</p><a class='source' href='autumn-beginner-mistakes.html'>초보 실수 보기 →</a></div>"
        "<div class='info-card'><h3>출조 준비물</h3><p>전날부터 승선 직전까지 확인할 준비물을 정리합니다.</p><a class='source' href='autumn-checklist.html'>출조 준비물 보기 →</a></div>"
        "</div>"
        "<div class='note'>출항 여부와 봉돌 호수, 채비 규정은 실제 승선할 선사의 최신 공지를 가장 우선해서 확인하세요.</div>"
    )

    simple_page(
        "autumn-cuttlefish.html",
        "가을 갑오징어 낚시 가이드",
        "<p>가을 갑오징어 조황은 단순 마릿수뿐 아니라 씨알, 평균 조과, 출조 시간, 포인트 이동 횟수까지 함께 보면 더 정확하게 읽을 수 있습니다.</p>"
        "<h2>조황에서 볼 항목</h2><ul><li>장원 마릿수와 평균 마릿수의 차이</li><li>씨알 표현과 사진에서 보이는 크기</li><li>오전·오후·종일반 등 실제 낚시 시간</li><li>연속된 날짜의 조황 흐름</li></ul>"
        "<h2>장비 핵심</h2><p>초릿대 감도만 보는 것보다 갑오징어 무게를 들어 올릴 수 있는 허리힘과 전체 밸런스를 함께 보는 것이 좋습니다.</p>"
        "<p><a class='source' href='gear-cuttlefish.html'>갑오징어 장비 선택 자세히 보기 →</a></p>"
    )

    simple_page(
        "autumn-jjukkumi.html",
        "가을 주꾸미 낚시 가이드",
        "<p>주꾸미는 마릿수 조황이 자주 강조되지만, 하루 총마릿수만으로 배나 날짜를 단순 비교하기는 어렵습니다.</p>"
        "<h2>조황에서 볼 항목</h2><ul><li>장원과 평균 조과</li><li>낚시 시간과 이동 거리</li><li>주꾸미 크기와 활성도 표현</li><li>바람·조류가 강했던 날인지 여부</li></ul>"
        "<h2>장비 핵심</h2><p>가벼운 세팅과 바닥 감도가 중요하고, 하루 종일 반복해서 들었다 놓는 낚시라 피로도가 낮은 구성이 실전에서 편합니다.</p>"
        "<p><a class='source' href='gear-jjukkumi.html'>주꾸미 장비 선택 자세히 보기 →</a></p>"
    )

    simple_page(
        "autumn-octopus.html",
        "가을 문어 낚시 가이드",
        "<p>문어는 주꾸미·갑오징어와 달리 무거운 채비와 강한 제압력이 요구되는 경우가 많아 장비 구성이 확실히 다릅니다.</p>"
        "<h2>조황에서 볼 항목</h2><ul><li>마릿수뿐 아니라 평균 크기</li><li>바닥 걸림이 많은 포인트인지</li><li>사용 봉돌 중량</li><li>출조 시간과 이동 거리</li></ul>"
        "<h2>장비 핵심</h2><p>로드의 허리힘, 릴의 권상력, 합사 내구성을 우선하며 출조선의 권장 봉돌을 안정적으로 사용할 수 있는지 확인해야 합니다.</p>"
        "<p><a class='source' href='gear-octopus.html'>문어 장비 선택 자세히 보기 →</a></p>"
    )

    simple_page(
        "autumn-cuttlefish-egi.html",
        "갑오징어 에기 선택 가이드",
        "<p>갑오징어 에기는 특정 색 하나가 항상 정답이라기보다 물색, 빛, 수심, 활성도에 따라 반응이 달라질 수 있습니다. 처음부터 너무 많은 종류를 사기보다 서로 성격이 다른 에기를 준비하는 방식이 실용적입니다.</p>"
        "<h2>색상 구성</h2><ul><li>밝은 계열: 탁한 물색이나 존재감을 보여주고 싶을 때 준비</li><li>어두운 계열: 실루엣 대비를 노릴 때 활용</li><li>자연색 계열: 경계심이 높은 상황에 대비</li></ul>"
        "<h2>크기와 침강</h2><p>조류가 빠르거나 깊은 수심에서는 채비 전체가 밀릴 수 있으므로 에기 자체의 크기보다 봉돌과 전체 밸런스를 함께 봐야 합니다.</p>"
        "<h2>초보자 구성</h2><p>서로 다른 색 계열 3~5개와 예비 에기부터 시작한 뒤, 실제 출조에서 반응이 좋았던 타입을 추가하는 방식이 좋습니다.</p>"
        "<div class='note'>실제 선박마다 허용 채비와 에기 개수 규정이 다를 수 있으니 승선 전 공지를 우선 확인하세요.</div>"
    )

    simple_page(
        "autumn-jjukkumi-sinker.html",
        "주꾸미 봉돌과 채비 선택",
        "<p>주꾸미 낚시에서 봉돌은 바닥을 읽는 감도와 채비 안정성에 직접 영향을 줍니다. 너무 가벼우면 바닥을 놓치고, 너무 무거우면 피로도가 커질 수 있습니다.</p>"
        "<h2>봉돌을 고르는 기준</h2><ul><li>조류가 빠를수록 더 무거운 봉돌이 필요할 수 있음</li><li>수심이 깊을수록 채비 정렬이 중요함</li><li>선박 전체가 같은 봉돌 호수를 맞추는 경우가 많음</li></ul>"
        "<h2>채비 밸런스</h2><p>가벼운 에기와 무거운 봉돌 조합은 바닥 확인에는 유리할 수 있지만 전체 움직임이 둔해질 수 있습니다. 선장의 권장 호수를 우선하고 그 안에서 조절하는 것이 안전합니다.</p>"
        "<h2>여분 준비</h2><p>바닥 걸림이 있는 날은 봉돌 손실이 생길 수 있으므로 동일 호수 여분을 충분히 준비하는 편이 좋습니다.</p>"
    )

    simple_page(
        "autumn-octopus-rig.html",
        "문어 채비 구성 가이드",
        "<p>문어 채비는 바닥 걸림과 강한 흡착력에 대응해야 해서 주꾸미·갑오징어보다 튼튼한 구성이 필요합니다.</p>"
        "<h2>핵심 요소</h2><ul><li>강한 원줄과 리더</li><li>충분한 권상력을 가진 릴</li><li>무거운 봉돌을 견디는 로드</li><li>바닥 걸림을 고려한 여분 채비</li></ul>"
        "<h2>운용</h2><p>바닥을 오래 끌기보다 선장의 운용 방식과 포인트 특성에 맞춰 짧게 들어 올리고 다시 바닥을 확인하는 식으로 운용하는 경우가 많습니다.</p>"
        "<div class='note'>문어 채비는 지역과 선박에 따라 규정 차이가 큰 편이므로 실제 출조선 공지가 최우선입니다.</div>"
    )

    simple_page(
        "autumn-tide.html",
        "가을 선상낚시 물때별 조황 보는 법",
        "<p>물때는 조황을 이해할 때 참고할 요소 중 하나지만, 물때 하나만으로 조과를 단정하기는 어렵습니다.</p>"
        "<h2>같이 봐야 할 요소</h2><ul><li>바람과 파고</li><li>조류 세기</li><li>수심과 포인트</li><li>낚시 시간</li><li>대상 어종의 활성도</li></ul>"
        "<h2>조황 비교 방법</h2><p>가능하면 같은 지역·같은 어종·비슷한 물때의 조황을 여러 날짜 묶어서 보는 편이 낫습니다. 하루의 대박 조황만으로 다음 출조 결과를 예상하지 않는 것이 좋습니다.</p>"
        "<h2>초보자가 흔히 하는 오해</h2><p>‘이 물때면 무조건 잘 잡힌다’처럼 단순화하면 실제 현장과 차이가 생길 수 있습니다. 물때는 여러 조건 중 하나로 보는 것이 적절합니다.</p>"
    )

    simple_page(
        "autumn-beginner-mistakes.html",
        "가을 선상낚시 초보자 실수",
        "<p>갑오징어·주꾸미·문어 시즌에는 처음 선상낚시를 시작하는 사람도 많습니다. 아래 실수를 줄이면 첫 출조가 훨씬 편해집니다.</p>"
        "<h2>자주 하는 실수</h2><ol><li>선박의 봉돌 호수를 확인하지 않고 장비를 준비함</li><li>에기와 채비를 너무 적게 가져감</li><li>집결 시간과 승선 장소를 출발 직전에 확인함</li><li>장원 마릿수만 보고 전체 조황으로 오해함</li><li>멀미 대비를 늦게 함</li><li>너무 무거운 장비를 하루 종일 사용함</li></ol>"
        "<h2>가장 먼저 확인할 것</h2><p>예약한 선박의 최신 공지, 집결 장소, 출항 여부, 봉돌 규정 네 가지를 출발 전날 다시 확인하세요.</p>"
    )

    simple_page(
        "autumn-checklist.html",
        "가을 선상낚시 출조 준비물",
        "<p>가을 선상낚시는 새벽 집결과 긴 운항이 많아 장비뿐 아니라 방풍·보온과 개인 준비물도 중요합니다.</p>"
        "<table class='spec-table'><tr><th>구분</th><th>확인 항목</th></tr>"
        "<tr><td>필수</td><td>신분증, 휴대폰, 예약 확인, 개인 상비약</td></tr>"
        "<tr><td>낚시</td><td>로드, 릴, 합사, 에기, 봉돌, 채비, 라인커터, 여분 소모품</td></tr>"
        "<tr><td>의류</td><td>방풍 겉옷, 미끄럼 방지 신발, 여벌 옷, 장갑</td></tr>"
        "<tr><td>편의</td><td>물, 간단한 간식, 수건, 비닐봉투, 보조배터리</td></tr></table>"
        "<h2>전날 체크</h2><ul><li>출항 여부</li><li>집결 시간과 주차 위치</li><li>권장 봉돌 호수</li><li>기상 변화</li></ul>"
        "<div class='note'>구명조끼 등 안전장비는 선박 안내와 현행 규정을 우선해 준비하세요.</div>"
    )

    simple_page(
        "gear.html",
        "가을 선상낚시 장비 선택",
        "<p>현재 사이트의 장비 콘텐츠는 가을 선상낚시에서 많이 찾는 <strong>갑오징어 · 주꾸미 · 문어</strong>를 중심으로 구성합니다. 제품명보다 로드·릴·합사·봉돌·에기 선택 기준을 먼저 정리합니다.</p>"
        "<div class='info-grid'>"
        "<div class='info-card'><span class='tag'>가을 핵심</span><h2>갑오징어</h2><p>입질 감도와 에기 운용, 채비 밸런스를 중심으로 봅니다.</p><a class='source' href='gear-cuttlefish.html'>갑오징어 장비 가이드 →</a></div>"
        "<div class='info-card'><span class='tag'>가을 핵심</span><h2>주꾸미</h2><p>가벼운 채비와 바닥 감도, 장시간 운용 피로도가 중요합니다.</p><a class='source' href='gear-jjukkumi.html'>주꾸미 장비 가이드 →</a></div>"
        "<div class='info-card'><span class='tag'>가을 대상어</span><h2>문어</h2><p>무거운 채비와 바닥 걸림에 버틸 파워가 필요합니다.</p><a class='source' href='gear-octopus.html'>문어 장비 가이드 →</a></div>"
        "</div>"
        "<h2>경험 수준별 장비 선택</h2>"
        "<div class='info-grid'>"
        "<div class='info-card'><h3>처음 시작하는 경우</h3><p>범용성, 무게, 사용 편의성, A/S를 우선해 첫 세트를 구성합니다.</p><a class='source' href='gear-beginner.html'>초보자 장비 구성 →</a></div>"
        "<div class='info-card'><h3>중급자로 넘어갈 때</h3><p>자주 가는 어종과 봉돌 범위가 정해졌다면 감도와 밸런스를 세분화할 수 있습니다.</p><a class='source' href='gear-intermediate.html'>중급 장비 구성 →</a></div>"
        "</div>"
        "<p><a class='source' href='gear-buying-checklist.html'>낚시 장비 구매 전 체크리스트 →</a></p>"
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
        "gear-octopus.html",
        "문어 선상낚시 장비 선택",
        "<p>문어 낚시는 무거운 봉돌과 문어의 흡착력을 동시에 상대해야 해서 전체적으로 강한 장비가 필요합니다.</p>"
        "<table class='spec-table'><tr><th>항목</th><th>선택 기준</th></tr>"
        "<tr><td>로드</td><td>무거운 채비를 반복적으로 들어 올려도 버틸 수 있는 파워와 허리힘을 우선합니다.</td></tr>"
        "<tr><td>릴</td><td>강한 권상력과 내구성, 충분한 라인 용량을 중점적으로 봅니다.</td></tr>"
        "<tr><td>라인</td><td>바닥 걸림과 강제 제압 상황이 있으므로 지나치게 가는 합사보다 내구성을 우선합니다.</td></tr>"
        "<tr><td>채비</td><td>포인트의 바닥 형태와 선사의 권장 봉돌 호수를 확인해 여유 있게 준비합니다.</td></tr></table>"
    )

    simple_page(
        "boat-samgilpo.html",
        "삼길포 선상낚시 가이드",
        "<p>삼길포에서 선박을 고를 때는 출조 어종, 집결 시간, 주차·승선 동선, 최근 조황 업데이트를 함께 보는 것이 좋습니다.</p>"
        "<h2>비교 순서</h2><ol><li>내가 원하는 어종 출조 여부 확인</li><li>최근 3~5회 조황 확인</li><li>출항·귀항 시간 비교</li><li>장비 대여와 채비 규정 확인</li></ol>"
        "<div class='note'>이 페이지는 특정 선박의 우열이나 순위를 정하지 않습니다. 실제 선박 데이터가 추가되면 객관적인 조황 기록을 연결할 예정입니다.</div>"
    )

    simple_page(
        "boat-ocheon.html",
        "오천 선상낚시 가이드",
        "<p>오천권 출조를 비교할 때는 같은 지역이라도 선박별 대상 어종과 운항 방식이 다를 수 있다는 점을 먼저 확인하세요.</p>"
        "<h2>예약 전 확인할 항목</h2><ul><li>집결 시간과 출항지</li><li>대상 어종과 예상 운항 시간</li><li>권장 봉돌·채비</li><li>취소·환불 규정</li></ul>"
    )

    simple_page(
        "boat-muchangpo.html",
        "무창포 선상낚시 가이드",
        "<p>무창포권은 출조 일정과 대상 어종을 먼저 정한 뒤 선박별 최근 조황과 공지를 비교하면 선택이 쉬워집니다.</p>"
        "<h2>조황을 볼 때</h2><p>최고 기록 한 건보다 비슷한 조건에서 며칠간 조황이 이어지는지 확인하는 편이 실전적인 판단에 도움이 됩니다.</p>"
    )

    simple_page(
        "boat-incheon.html",
        "인천 선상낚시 가이드",
        "<p>인천권은 수도권에서 접근성을 중시하는 이용자가 살펴볼 수 있는 출항 지역입니다.</p>"
        "<h2>선박 선택 기준</h2><ul><li>이동 거리와 집결 시간</li><li>출조 어종과 운항 시간</li><li>선박별 예약 방식</li><li>최근 조황 게시 빈도</li></ul>"
    )

    simple_page(
        "boat-pyeongtaek.html",
        "평택 선상낚시 가이드",
        "<p>평택권 출조는 출항지 접근성과 대상 어종, 출조 시간을 함께 비교하는 것이 좋습니다.</p>"
        "<p>처음 이용하는 배라면 예약 전에 주차 위치와 승선 장소를 다시 확인하세요.</p>"
    )

    simple_page(
        "boat-mokpo.html",
        "목포 선상낚시 가이드",
        "<p>목포권은 서남해 출조를 계획할 때 살펴볼 수 있는 지역입니다. 계절과 대상 어종에 따라 운항 방식이 달라질 수 있습니다.</p>"
        "<h2>비교할 때</h2><p>같은 어종·비슷한 출조 시간끼리 조황을 비교하고, 기상과 이동 시간을 함께 고려하세요.</p>"
    )

    simple_page(
        "boat-yeosu.html",
        "여수 선상낚시 가이드",
        "<p>여수권은 다양한 선상낚시 출조가 운영되는 지역으로, 원하는 어종과 출항 시간대에 맞춰 선박을 좁혀가는 방식이 편합니다.</p>"
        "<h2>예약 전에</h2><ul><li>출항 항구</li><li>대상 어종</li><li>예상 귀항 시간</li><li>장비·채비 규정</li></ul>"
    )

    simple_page(
        "gear-beginner.html",
        "초보자 선상낚시 장비 구성",
        "<p>처음 장비를 살 때는 최고 사양보다 '내가 실제로 자주 탈 배와 어종에 맞는지'를 먼저 보는 것이 좋습니다.</p>"
        "<h2>우선순위</h2><ol><li>출조선의 권장 봉돌 범위를 커버하는 로드</li><li>손에 잘 잡히고 조작이 쉬운 릴</li><li>기본 합사와 여분 쇼크리더</li><li>자주 쓰는 채비의 여분</li></ol>"
        "<h2>처음부터 많이 살 필요 없는 것</h2><p>색상만 다른 에기나 세부 스펙이 비슷한 로드를 여러 개 사기보다, 한 세트를 충분히 사용해 본 뒤 부족한 점을 기준으로 추가하는 편이 효율적입니다.</p>"
    )

    simple_page(
        "gear-intermediate.html",
        "중급자 선상낚시 장비 구성",
        "<p>출조 횟수가 늘면 장비 선택 기준이 '쓸 수 있느냐'에서 '어떤 상황에서 더 편하고 정확하냐'로 바뀝니다.</p>"
        "<h2>세분화 포인트</h2><ul><li>얕은 수심과 깊은 수심용 로드 분리</li><li>자주 쓰는 봉돌 중량 중심으로 로드 파워 선택</li><li>릴 기어비와 핸들 길이 비교</li><li>합사 굵기와 조류 저항의 균형</li></ul>"
        "<p>스펙을 세분화할수록 범용성은 줄어들 수 있으므로 자주 가는 출조 조건을 먼저 정하는 것이 좋습니다.</p>"
    )

    simple_page(
        "gear-buying-checklist.html",
        "낚시 장비 구매 전 체크리스트",
        "<p>온라인 후기나 가격만 보고 장비를 고르기보다 아래 항목을 순서대로 확인하면 불필요한 중복 구매를 줄일 수 있습니다.</p>"
        "<table class='spec-table'><tr><th>확인 항목</th><th>질문</th></tr>"
        "<tr><td>대상 어종</td><td>주로 어떤 어종을 낚을 것인가?</td></tr>"
        "<tr><td>봉돌 범위</td><td>내가 타는 배에서 자주 쓰는 봉돌을 로드가 감당하는가?</td></tr>"
        "<tr><td>장시간 사용</td><td>무게와 그립이 하루 종일 사용하기 편한가?</td></tr>"
        "<tr><td>릴 호환</td><td>로드와 릴의 무게 균형이 맞는가?</td></tr>"
        "<tr><td>소모품</td><td>합사·리더·바늘·에기 등 유지 비용도 감당 가능한가?</td></tr>"
        "<tr><td>A/S</td><td>초릿대나 소모 부품을 구하기 쉬운가?</td></tr></table>"
        "<div class='note'>제품명 자체보다 사용 조건과 규격을 먼저 정한 뒤 제품을 고르는 방식이 실패를 줄이는 데 도움이 됩니다.</div>"
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
