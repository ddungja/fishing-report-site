import json
import os
import re
import shutil
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "sources.json"
DOCS = ROOT / "docs"
BASE_URL = os.getenv(
    "BASE_URL",
    "https://ddungja.github.io/fishing-report-site",
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

SPECIES_ALIASES = [
    ("갑오징어", ("갑오징어", "갑이")),
    ("주꾸미", ("주꾸미", "쭈꾸미")),
    ("문어", ("문어",)),
    ("광어", ("광어",)),
    ("우럭", ("우럭",)),
    ("참돔", ("참돔",)),
    ("농어", ("농어",)),
    ("백조기", ("백조기", "조기")),
    ("갈치", ("갈치",)),
    ("쭈갑", ("쭈갑",)),
]


def esc(value):
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def page_url(url, page):
    if "page=" in url:
        return re.sub(r"([?&])page=\d+", rf"\1page={page}", url)
    return url + ("&" if "?" in url else "?") + f"page={page}"


def parse_date(text, start_dt, end_dt):
    text = str(text or "")

    for match in re.finditer(
        r"(?<!\d)(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})(?!\d)",
        text,
    ):
        try:
            dt = datetime(*map(int, match.groups()))
        except ValueError:
            continue
        if start_dt <= dt <= end_dt:
            return dt

    for match in re.finditer(r"(?<!\d)(\d{1,2})[./-](\d{1,2})(?!\d)", text):
        month, day = map(int, match.groups())
        try:
            dt = datetime(start_dt.year, month, day)
        except ValueError:
            continue
        if start_dt <= dt <= end_dt:
            return dt

    return None


def classify_species(text):
    lower = str(text or "").lower()
    found = []
    for name, aliases in SPECIES_ALIASES:
        if any(alias.lower() in lower for alias in aliases):
            found.append(name)
    if "쭈갑" in found:
        found = [x for x in found if x != "쭈갑"]
        for name in ["주꾸미", "갑오징어"]:
            if name not in found:
                found.append(name)
    return found[:4]


def candidate_blocks(soup):
    nodes = []
    for selector in [
        "li", "article", ".list", ".item",
        ".board-list", ".board_item", ".post", ".card", "tr",
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
        text = node.get_text(" ", strip=True)
        if len(text) >= 20:
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

        original_title = ""
        for selector in ["h1", "h2", "h3", "h4", ".title", ".subject", "a"]:
            tag = node.select_one(selector)
            if not tag:
                continue
            candidate = tag.get_text(" ", strip=True)
            if keyword in candidate.lower() or re.search(r"\d{1,2}[./-]\d{1,2}", candidate):
                original_title = candidate
                break

        detail_url = ""
        for anchor in node.find_all("a", href=True):
            href = str(anchor["href"])
            if "board_detail" in href:
                detail_url = urljoin(current_url, href)
                break

        key_seed = detail_url or f"{dt:%Y%m%d}|{text[:100]}"
        key = re.sub(r"\W+", "", key_seed)[-64:] or dt.strftime("%Y%m%d")

        result.setdefault(
            key_seed,
            {
                "date": dt.strftime("%Y-%m-%d"),
                "sort_key": dt.strftime("%Y%m%d"),
                "title": original_title or f"{source['name']} 새 조황",
                "body_text": text,
                "detail_url": detail_url,
                "photos": [],
                "key": key,
            },
        )

    return list(result.values())


def enrich_detail(post):
    if not post.get("detail_url"):
        post["species"] = classify_species(
            f"{post.get('title', '')} {post.get('body_text', '')}"
        )
        return post

    try:
        response = session.get(post["detail_url"], timeout=30)
        if response.status_code != 200:
            return post

        soup = BeautifulSoup(response.text, "html.parser")
        generic_titles = {
            "조황관리 HOME / 조황관리 / 상세정보",
            "조황관리",
            "상세정보",
        }

        for selector in [".title", ".view_title", ".subject", "h1", "h2", "h3"]:
            tag = soup.select_one(selector)
            if not tag:
                continue
            text = tag.get_text(" ", strip=True)
            if len(text) >= 5 and text not in generic_titles:
                post["title"] = text
                break

        for selector in [
            ".editor", ".view_content", ".view-content",
            ".board_view_content", ".content",
        ]:
            node = soup.select_one(selector)
            if not node:
                continue
            text = node.get_text(" ", strip=True)
            if len(text) >= 20:
                post["body_text"] = text
                break

    except Exception:
        pass

    post["species"] = classify_species(
        f"{post.get('title', '')} {post.get('body_text', '')}"
    )
    return post


def collect_source(source):
    start_dt = datetime.strptime(source["start_date"], "%Y-%m-%d")
    today = date.today()
    end_dt = datetime(today.year, today.month, today.day)
    max_posts = int(source.get("max_posts", 60))
    collected = {}

    for page in range(1, 11):
        if len(collected) >= max_posts:
            break

        url = page_url(source["board_url"], page)
        try:
            response = session.get(url, timeout=30)
        except Exception:
            break

        if response.status_code != 200:
            break

        posts = extract_list_posts(
            response.text,
            source,
            url,
            start_dt,
            end_dt,
        )
        for post in posts:
            collected.setdefault(post["key"], post)

        if not posts and page >= 3:
            break
        time.sleep(0.15)

    posts = sorted(
        collected.values(),
        key=lambda item: item["sort_key"],
        reverse=True,
    )[:max_posts]

    return [enrich_detail(post) for post in posts]


def fmt_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y.%m.%d")
    except Exception:
        return value


def nav(prefix=""):
    items = [
        ("index.html", "최신 업데이트"),
        ("boats.html", "선박 비교"),
        ("insights.html", "데이터 인사이트"),
        ("guide.html", "낚시 가이드"),
        ("notify.html", "카카오 알림"),
        ("about.html", "서비스 소개"),
    ]
    links = "".join(
        f'<a href="{prefix}{path}">{label}</a>'
        for path, label in items
    )
    return f'<nav class="nav">{links}</nav>'


def page_shell(title, description, body, prefix="", extra_head=""):
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="stylesheet" href="{prefix}assets/style.css">
{extra_head}
</head>
<body>
<header>
  <div class="wrap header-inner">
    <a class="logo" href="{prefix}index.html">
      <span class="logo-mark">🎣</span>
      <span>선상 조황 알리미</span>
    </a>
    {nav(prefix)}
  </div>
</header>
{body}
<footer>
  <div class="wrap footer-grid">
    <div><strong>선상 조황 알리미</strong><p>원문을 복제하지 않고, 조황 업데이트 시점과 선박별 활동 데이터를 정리합니다.</p></div>
    <div class="footer-links">
      <a href="{prefix}about.html">서비스 소개</a>
      <a href="{prefix}copyright.html">저작권·출처 정책</a>
      <a href="{prefix}privacy.html">개인정보처리방침</a>
      <a href="{prefix}terms.html">이용약관</a>
      <a href="{prefix}contact.html">문의</a>
    </div>
  </div>
</footer>
</body>
</html>"""


def source_link(url, label="원문 조황 보기"):
    return (
        f'<a class="btn btn-outline" href="{esc(url)}" '
        'target="_blank" rel="noopener noreferrer">'
        f'{esc(label)} ↗</a>'
    )


def stat_for(posts, days):
    cutoff = date.today() - timedelta(days=days - 1)
    return sum(
        1
        for post in posts
        if datetime.strptime(post["date"], "%Y-%m-%d").date() >= cutoff
    )


def build():
    cfg = load_json(CFG, {"sources": []})
    sources = [
        source for source in cfg.get("sources", [])
        if source.get("enabled", True)
    ]

    if DOCS.exists():
        shutil.rmtree(DOCS)
    (DOCS / "assets").mkdir(parents=True)
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    css = r"""
*{box-sizing:border-box}
:root{
  --bg:#f5f7f8;--card:#fff;--text:#17222a;--muted:#66757f;
  --accent:#075f87;--accent2:#0c7da8;--soft:#eaf5f9;--line:#dce5e9;
  --green:#177455;--warn:#8a5b00
}
html{scroll-behavior:smooth}
body{margin:0;font-family:Arial,'Malgun Gothic',sans-serif;background:var(--bg);color:var(--text)}
a{text-decoration:none;color:inherit}.wrap{width:min(1160px,calc(100% - 28px));margin:auto}
header{position:sticky;top:0;z-index:30;background:#ffffffee;backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.header-inner{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:15px 0}
.logo{display:flex;align-items:center;gap:9px;font-size:24px;font-weight:900;color:var(--accent);white-space:nowrap}
.logo-mark{font-size:25px}.nav{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.nav a{display:inline-flex;align-items:center;min-height:44px;padding:0 16px;border-radius:12px;font-size:15px;font-weight:800;color:#29414e}
.nav a:hover{background:var(--soft);color:var(--accent)}
.hero{padding:68px 0 34px}.eyebrow{font-size:13px;font-weight:900;color:var(--accent);letter-spacing:.08em;text-transform:uppercase}
.hero h1{font-size:clamp(34px,6vw,62px);line-height:1.08;letter-spacing:-2px;margin:10px 0 16px;max-width:850px}
.hero p{font-size:18px;line-height:1.8;color:var(--muted);max-width:780px}
.hero-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:24px}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:46px;padding:0 17px;border-radius:12px;font-weight:900;border:1px solid transparent}
.btn-primary{background:var(--accent);color:#fff}.btn-primary:hover{background:#044d6e}
.btn-outline{background:#fff;border-color:var(--line);color:var(--accent)}.btn-outline:hover{border-color:#9dbac7;background:#fbfdfe}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:10px 0 32px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:18px;padding:20px}.kpi strong{display:block;font-size:30px;letter-spacing:-1px}.kpi span{color:var(--muted);font-size:13px}
.section{padding:24px 0 46px}.section-head{display:flex;align-items:end;justify-content:space-between;gap:20px;margin-bottom:16px}
.section h2{font-size:28px;margin:0}.section-desc{color:var(--muted);line-height:1.7}
.report-list{display:grid;gap:12px}.report-card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:20px;display:grid;grid-template-columns:1fr auto;gap:18px;align-items:center}
.report-card:hover{border-color:#b6ccd5;box-shadow:0 10px 24px #102a3510}
.meta{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}.pill{display:inline-flex;align-items:center;padding:6px 9px;border-radius:999px;background:var(--soft);color:var(--accent);font-size:12px;font-weight:900}.pill.gray{background:#f0f2f3;color:#59666d}.pill.green{background:#e7f5ef;color:var(--green)}
.report-title{font-size:20px;font-weight:900;margin:0 0 7px}.report-sub{color:var(--muted);font-size:14px;line-height:1.65}
.boat-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}.boat-card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:22px}.boat-card h3{font-size:22px;margin:6px 0}.mini-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:18px 0}.mini-stat{background:#f7fafb;border-radius:12px;padding:12px}.mini-stat strong{display:block;font-size:20px}.mini-stat span{font-size:12px;color:var(--muted)}
.notice{background:#fff8e5;border:1px solid #edd9a4;color:#69511c;border-radius:16px;padding:17px;line-height:1.75}
.info-box{background:#fff;border:1px solid var(--line);border-radius:18px;padding:22px;margin:16px 0}.info-box h2,.info-box h3{margin-top:0}
.content{padding:42px 0 64px}.content h1{font-size:clamp(32px,5vw,48px);letter-spacing:-1.5px;margin-bottom:12px}.content h2{margin-top:34px}.content p,.content li{line-height:1.9;color:#3e4f59}.content ul,.content ol{padding-left:22px}
.data-table{width:100%;border-collapse:collapse;background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line)}.data-table th,.data-table td{padding:14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}.data-table th{background:#eef5f8;font-size:13px}.data-table tr:last-child td{border-bottom:0}
.guide-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.guide-card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:22px}.guide-card h3{margin:8px 0}.guide-card p{font-size:14px;color:var(--muted);line-height:1.75}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}.filters button{border:1px solid var(--line);background:#fff;padding:10px 14px;border-radius:999px;font-weight:800;cursor:pointer}.filters button.active{background:var(--accent);color:#fff;border-color:var(--accent)}
footer{background:#fff;border-top:1px solid var(--line);padding:34px 0;color:var(--muted);font-size:13px}.footer-grid{display:grid;grid-template-columns:1fr auto;gap:24px}.footer-grid p{max-width:580px;line-height:1.7}.footer-links{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start}
.notify-wrap{max-width:760px;margin:0 auto}.notify-box{background:#fff;border:1px solid var(--line);border-radius:18px;padding:24px;margin:18px 0}.kakao-login{display:inline-flex;align-items:center;justify-content:center;min-height:50px;padding:0 22px;border-radius:12px;background:#FEE500;color:#191919;font-weight:900;font-size:17px}.boat-row{display:flex;align-items:center;gap:12px;padding:14px 0;border-bottom:1px solid #eef2f4}.boat-row:last-child{border-bottom:0}.boat-row input{width:22px;height:22px}.save-btn{display:inline-flex;align-items:center;justify-content:center;min-height:48px;padding:0 22px;border:0;border-radius:12px;background:var(--accent);color:#fff;font-weight:900;font-size:16px;cursor:pointer}.muted{color:var(--muted)}.status{padding:12px 14px;border-radius:10px;background:#eef8fc;margin:12px 0;display:none}.consent{display:flex;gap:10px;align-items:flex-start;margin:16px 0}.consent input{width:20px;height:20px;margin-top:2px}
@media(max-width:900px){.header-inner{align-items:flex-start;flex-direction:column}.nav{justify-content:flex-start}.kpi-grid{grid-template-columns:repeat(2,1fr)}.guide-grid{grid-template-columns:1fr}.boat-grid{grid-template-columns:1fr}}
@media(max-width:640px){.hero{padding-top:42px}.nav a{min-height:40px;padding:0 12px;font-size:14px}.report-card{grid-template-columns:1fr}.kpi-grid{grid-template-columns:1fr 1fr}.mini-stats{grid-template-columns:1fr}.footer-grid{grid-template-columns:1fr}.data-table{font-size:13px}}
"""
    (DOCS / "assets" / "style.css").write_text(css, encoding="utf-8")

    all_posts = []
    source_results = {}

    for source in sources:
        posts = collect_source(source)
        source_results[source["id"]] = posts
        for post in posts:
            item = dict(post)
            item["source_id"] = source["id"]
            item["source_name"] = source["name"]
            item["region"] = source.get("region", "")
            item["harbor"] = source.get("harbor", "")
            item["source_board_url"] = source["board_url"]
            all_posts.append(item)

    all_posts.sort(key=lambda item: item["sort_key"], reverse=True)

    seven_day_count = stat_for(all_posts, 7) if all_posts else 0
    thirty_day_count = stat_for(all_posts, 30) if all_posts else 0
    latest_date = all_posts[0]["date"] if all_posts else "-"

    latest_cards = []
    for post in all_posts[:30]:
        species = post.get("species") or []
        species_html = "".join(
            f'<span class="pill green">{esc(name)}</span>' for name in species
        ) or '<span class="pill gray">어종 미분류</span>'
        target = post.get("detail_url") or post.get("source_board_url")
        latest_cards.append(
            f"""<article class="report-card" data-source="{esc(post['source_id'])}">
<div>
  <div class="meta">
    <span class="pill">{esc(post['source_name'])}</span>
    <span class="pill gray">{esc(post.get('region'))}</span>
    {species_html}
  </div>
  <h3 class="report-title">{fmt_date(post['date'])} 새 조황이 등록됐습니다</h3>
  <div class="report-sub">선사 원문의 사진과 본문은 복사하지 않습니다. 등록 시점·선박·어종 분류만 정리하고 원문으로 연결합니다.</div>
</div>
<div>{source_link(target)}</div>
</article>"""
        )

    boat_cards = []
    boat_rows = []
    for source in sources:
        posts = source_results.get(source["id"], [])
        last_date = posts[0]["date"] if posts else "-"
        count_7 = stat_for(posts, 7) if posts else 0
        count_30 = stat_for(posts, 30) if posts else 0
        active_days = len({post["date"] for post in posts})
        species_counter = Counter()
        for post in posts:
            species_counter.update(post.get("species") or [])
        top_species = ", ".join(x for x, _ in species_counter.most_common(3)) or "미분류"

        boat_cards.append(
            f"""<article class="boat-card">
<div class="eyebrow">{esc(source.get('region',''))}</div>
<h3>{esc(source['name'])}</h3>
<p class="section-desc">{esc(source.get('harbor',''))} 출항 · 원문 조황 게시 빈도를 기준으로 최근 활동을 요약합니다.</p>
<div class="mini-stats">
  <div class="mini-stat"><strong>{count_7}</strong><span>최근 7일 업데이트</span></div>
  <div class="mini-stat"><strong>{count_30}</strong><span>최근 30일 업데이트</span></div>
  <div class="mini-stat"><strong>{active_days}</strong><span>수집 기간 활동일</span></div>
</div>
<p><strong>최근 감지 어종:</strong> {esc(top_species)}</p>
<div class="hero-actions">{source_link(source['board_url'], '선사 조황판 보기')}</div>
</article>"""
        )

        boat_rows.append(
            f"""<tr>
<td><strong>{esc(source['name'])}</strong><br><span class="muted">{esc(source.get('region',''))} · {esc(source.get('harbor',''))}</span></td>
<td>{fmt_date(last_date) if last_date != '-' else '-'}</td>
<td>{count_7}건</td>
<td>{count_30}건</td>
<td>{active_days}일</td>
<td>{esc(top_species)}</td>
<td>{source_link(source['board_url'], '원문')}</td>
</tr>"""
        )

    home_body = f"""
<main class="wrap">
<section class="hero">
  <div class="eyebrow">Fishing report index & alert</div>
  <h1>원문은 선사에서,<br>비교와 알림은 여기서.</h1>
  <p>다른 사이트의 조황 사진이나 본문을 다시 게시하지 않습니다. 대신 <strong>언제 어떤 배의 새 조황이 올라왔는지</strong>를 모아 보여주고, 선박별 업데이트 흐름을 비교하고, 원하는 배만 알림 받을 수 있게 정리합니다.</p>
  <div class="hero-actions">
    <a class="btn btn-primary" href="notify.html">카카오 조황 알림 설정</a>
    <a class="btn btn-outline" href="boats.html">선박별 데이터 비교</a>
  </div>
</section>

<section class="kpi-grid">
  <div class="kpi"><strong>{len(sources)}</strong><span>현재 추적 선박</span></div>
  <div class="kpi"><strong>{seven_day_count}</strong><span>최근 7일 새 조황</span></div>
  <div class="kpi"><strong>{thirty_day_count}</strong><span>최근 30일 새 조황</span></div>
  <div class="kpi"><strong>{fmt_date(latest_date) if latest_date != '-' else '-'}</strong><span>가장 최근 업데이트</span></div>
</section>

<div class="notice"><strong>운영 원칙:</strong> 조황 사진·선장 작성 본문은 저장하거나 재게시하지 않습니다. 이 사이트에는 날짜, 선박명, 지역, 감지된 어종 같은 사실 정보와 자체 집계만 표시하며 상세 내용은 원문으로 연결합니다.</div>

<section class="section">
  <div class="section-head">
    <div><div class="eyebrow">Latest updates</div><h2>최신 조황 업데이트</h2></div>
    <p class="section-desc">새로 등록된 순서대로 확인하세요.</p>
  </div>
  <div class="filters" id="filters">
    <button class="active" data-filter="all">전체</button>
    {"".join(f'<button data-filter="{esc(source["id"])}">{esc(source["name"])}</button>' for source in sources)}
  </div>
  <div class="report-list" id="reportList">
    {"".join(latest_cards) if latest_cards else '<div class="info-box">현재 수집된 새 조황이 없습니다.</div>'}
  </div>
</section>

<section class="section">
  <div class="section-head">
    <div><div class="eyebrow">Boat activity</div><h2>선박별 최근 활동</h2></div>
    <a class="btn btn-outline" href="boats.html">전체 비교 보기</a>
  </div>
  <div class="boat-grid">{"".join(boat_cards)}</div>
</section>

<section class="section">
  <div class="section-head"><div><div class="eyebrow">Original content</div><h2>조황을 고를 때 도움이 되는 자체 가이드</h2></div></div>
  <div class="guide-grid">
    <a class="guide-card" href="guide-reading.html"><span class="pill">데이터 읽기</span><h3>조황사진 한 장보다 중요한 것</h3><p>최고 기록보다 업데이트의 연속성, 날짜 간격, 비슷한 조건의 반복성을 보는 방법을 정리했습니다.</p></a>
    <a class="guide-card" href="guide-autumn.html"><span class="pill">가을 시즌</span><h3>갑오징어·주꾸미·문어 출조 비교</h3><p>세 어종의 채비 성격과 출조 전에 확인할 포인트를 한 페이지에 비교합니다.</p></a>
    <a class="guide-card" href="guide-first-trip.html"><span class="pill">첫 출조</span><h3>예약 전 체크리스트</h3><p>집결 시간, 주차, 봉돌 호수, 장비 대여, 환불 조건처럼 실제로 놓치기 쉬운 항목을 정리했습니다.</p></a>
  </div>
</section>
</main>
<script>
document.querySelectorAll('#filters button').forEach(function(btn){
  btn.addEventListener('click',function(){
    document.querySelectorAll('#filters button').forEach(function(x){x.classList.remove('active')});
    btn.classList.add('active');
    var f=btn.dataset.filter;
    document.querySelectorAll('.report-card').forEach(function(card){
      card.style.display=(f==='all'||card.dataset.source===f)?'grid':'none';
    });
  });
});
</script>
"""
    index_html = page_shell(
        "선상 조황 알리미 | 선박별 업데이트·비교·알림",
        "선사 조황 원문을 복제하지 않고 선박별 새 조황 등록 시점, 업데이트 빈도, 어종 분류와 알림 기능을 제공합니다.",
        home_body,
    )
    (DOCS / "index.html").write_text(index_html, encoding="utf-8")

    boats_body = f"""
<main class="wrap content">
<div class="eyebrow">Boat comparison</div>
<h1>선박별 조황 업데이트 비교</h1>
<p>아래 수치는 사진 속 마릿수나 조과를 평가한 순위가 아닙니다. 각 선사의 공개 조황판에서 <strong>새 게시물이 등록된 빈도</strong>만 집계합니다. 좋은 배를 단정하기보다 내가 원하는 어종과 출조 일정에 맞는 배를 좁히는 참고자료로 사용하세요.</p>
<div class="info-box">
<table class="data-table">
<thead><tr><th>선박</th><th>최근 업데이트</th><th>7일</th><th>30일</th><th>활동일</th><th>감지 어종</th><th>출처</th></tr></thead>
<tbody>{"".join(boat_rows)}</tbody>
</table>
</div>
<div class="notice">업데이트 횟수는 조과의 우열을 뜻하지 않습니다. 선사가 한 번의 출조를 여러 게시물로 나누거나 게시 시점이 달라질 수 있으므로 실제 조황 판단은 반드시 원문과 기상·물때를 함께 확인하세요.</div>
</main>
"""
    (DOCS / "boats.html").write_text(
        page_shell(
            "선박별 조황 업데이트 비교 | 선상 조황 알리미",
            "선박별 최근 7일·30일 조황 게시 빈도와 최근 업데이트 날짜를 비교합니다.",
            boats_body,
        ),
        encoding="utf-8",
    )

    species_counter = Counter()
    for post in all_posts:
        species_counter.update(post.get("species") or [])
    species_rows = "".join(
        f"<tr><td><strong>{esc(name)}</strong></td><td>{count}건</td></tr>"
        for name, count in species_counter.most_common()
    ) or "<tr><td colspan='2'>아직 어종 분류 데이터가 충분하지 않습니다.</td></tr>"

    insights_body = f"""
<main class="wrap content">
<div class="eyebrow">Data insights</div>
<h1>조황 업데이트 데이터 인사이트</h1>
<p>이 페이지는 다른 사이트의 글이나 사진을 재게시하는 대신, 조황 게시물이 언제 등록됐는지와 어떤 어종 키워드가 확인됐는지를 자체적으로 집계합니다.</p>
<section class="kpi-grid">
  <div class="kpi"><strong>{len(all_posts)}</strong><span>현재 수집 범위 내 감지 게시물</span></div>
  <div class="kpi"><strong>{seven_day_count}</strong><span>최근 7일 업데이트</span></div>
  <div class="kpi"><strong>{thirty_day_count}</strong><span>최근 30일 업데이트</span></div>
  <div class="kpi"><strong>{len(species_counter)}</strong><span>감지된 어종 분류</span></div>
</section>

<div class="info-box">
<h2>어종 키워드 감지 현황</h2>
<table class="data-table"><thead><tr><th>어종</th><th>감지된 게시물 수</th></tr></thead><tbody>{species_rows}</tbody></table>
</div>

<div class="info-box">
<h2>이 수치로 알 수 있는 것</h2>
<ul>
<li>특정 선박이 최근에도 조황 게시를 꾸준히 업데이트하고 있는지</li>
<li>최근 게시물에서 어떤 대상어 키워드가 자주 나타나는지</li>
<li>내가 구독하려는 선박의 새 게시물이 올라왔는지</li>
</ul>
<h2>이 수치로 알 수 없는 것</h2>
<ul>
<li>실제 출조 인원 1인당 조과</li>
<li>사진에 잡힌 정확한 마릿수나 크기</li>
<li>선박의 서비스 품질이나 안전 수준</li>
<li>앞으로의 조황을 보장하는 예측</li>
</ul>
</div>

<div class="notice"><strong>집계 방식:</strong> 공개 조황판의 게시일과 링크를 기준으로 중복을 제거합니다. 어종은 게시물의 텍스트에서 키워드만 분류하며 원문 문장 자체는 사이트에 노출하지 않습니다.</div>
</main>
"""
    (DOCS / "insights.html").write_text(
        page_shell(
            "조황 데이터 인사이트 | 선상 조황 알리미",
            "조황 게시 빈도와 어종 키워드를 자체 집계해 선박별 최근 활동을 이해하기 쉽게 정리합니다.",
            insights_body,
        ),
        encoding="utf-8",
    )

    guide_body = """
<main class="wrap content">
<div class="eyebrow">Fishing guides</div>
<h1>실제 출조 전에 보는 가이드</h1>
<p>조황을 그대로 복사해 보여주는 대신, 선박을 고르고 출조를 준비할 때 직접 도움이 되는 내용을 자체적으로 정리합니다.</p>
<div class="guide-grid">
<a class="guide-card" href="guide-reading.html"><span class="pill">조황 읽기</span><h3>좋은 조황을 판단하는 순서</h3><p>한 장의 대박 사진보다 최근 며칠의 반복성, 날짜, 대상어, 출조 조건을 확인하는 방법.</p></a>
<a class="guide-card" href="guide-autumn.html"><span class="pill">가을낚시</span><h3>갑오징어·주꾸미·문어 비교</h3><p>세 어종의 장비 성격, 바닥 운용, 출조 전 질문을 비교해 처음 가는 사람도 준비하기 쉽게 정리.</p></a>
<a class="guide-card" href="guide-first-trip.html"><span class="pill">초보자</span><h3>첫 선상낚시 체크리스트</h3><p>예약부터 승선까지 놓치기 쉬운 준비사항을 출조 전날과 당일 기준으로 구분.</p></a>
</div>
</main>
"""
    (DOCS / "guide.html").write_text(
        page_shell(
            "선상낚시 실전 가이드 | 선상 조황 알리미",
            "조황 읽는 법, 가을 갑오징어·주꾸미·문어 비교, 첫 출조 체크리스트를 정리합니다.",
            guide_body,
        ),
        encoding="utf-8",
    )

    reading_body = """
<main class="wrap content">
<div class="eyebrow">Guide · 조황 읽기</div>
<h1>조황사진 한 장보다 중요한 것</h1>
<p>조황을 볼 때 가장 흔한 실수는 가장 많이 잡힌 날의 사진 한 장을 그 배의 평균 실력처럼 받아들이는 것입니다. 실제 예약 판단에서는 '얼마나 많이 잡혔나'보다 <strong>언제, 얼마나 자주, 비슷한 조건에서 반복됐나</strong>를 먼저 보는 편이 실용적입니다.</p>

<div class="info-box">
<h2>1. 최근성부터 확인</h2>
<p>게시 날짜가 오래됐다면 지금의 수온, 물때, 대상어 이동과 맞지 않을 수 있습니다. 같은 시즌이라도 며칠 사이 상황이 바뀔 수 있으므로 최근 게시물이 꾸준한지 먼저 확인합니다.</p>
<h2>2. 최고 기록보다 연속성</h2>
<p>한 번의 강한 조황보다 최근 3~5회 출조에서 비슷한 대상어가 계속 확인되는지가 더 유용한 정보일 수 있습니다. 우리 사이트의 7일·30일 업데이트 수치는 이 '활동의 연속성'을 빠르게 보는 용도입니다.</p>
<h2>3. 비교 조건을 맞추기</h2>
<p>서로 다른 지역, 다른 어종, 다른 출조 시간을 한 줄로 비교하면 왜곡됩니다. 갑오징어 오전 출조라면 비슷한 지역의 갑오징어 출조끼리, 문어라면 문어 출조끼리 비교하는 편이 낫습니다.</p>
<h2>4. 사진 밖의 정보 확인</h2>
<p>원문에서는 집결 시간, 사용 봉돌, 채비 제한, 출항 취소 기준, 주차 위치 같은 운영 정보를 반드시 확인하세요. 실제 만족도에는 조과만큼 이런 조건이 크게 작용합니다.</p>
</div>

<div class="notice">이 사이트는 특정 선박의 조과 우열을 평가하거나 순위를 매기지 않습니다. 최신 게시 여부와 활동 흐름을 정리하고, 최종 판단은 원문과 당일 조건을 확인하도록 연결합니다.</div>
</main>
"""
    (DOCS / "guide-reading.html").write_text(
        page_shell(
            "조황사진 한 장보다 중요한 것 | 선상 조황 알리미",
            "선상 조황을 볼 때 최근성, 연속성, 비교 조건, 원문 운영정보를 확인하는 방법을 설명합니다.",
            reading_body,
        ),
        encoding="utf-8",
    )

    autumn_body = """
<main class="wrap content">
<div class="eyebrow">Guide · 가을 시즌</div>
<h1>갑오징어·주꾸미·문어 출조 비교</h1>
<p>세 어종은 모두 바닥을 의식하는 선상낚시지만 장비에 요구하는 성격과 하루 운용 방식은 꽤 다릅니다. 처음 예약할 때는 '어떤 어종이 더 좋다'보다 내 체력과 장비, 원하는 낚시 방식에 맞는지를 보는 편이 좋습니다.</p>
<table class="data-table">
<thead><tr><th>구분</th><th>갑오징어</th><th>주꾸미</th><th>문어</th></tr></thead>
<tbody>
<tr><td>장비 느낌</td><td>감도와 허리힘의 균형</td><td>가벼움과 바닥 감도</td><td>강한 파워와 권상력</td></tr>
<tr><td>운용 핵심</td><td>에기 움직임과 미세한 무게 변화</td><td>바닥을 반복적으로 찍고 변화 감지</td><td>무거운 채비와 바닥 걸림 대응</td></tr>
<tr><td>초보 준비</td><td>에기 여분, 먹물 대비</td><td>봉돌·채비 여분, 가벼운 장비</td><td>튼튼한 채비, 장갑, 충분한 라인</td></tr>
</tbody>
</table>
<div class="info-box">
<h2>예약 전에 선사에 확인할 질문</h2>
<ul>
<li>당일 주 대상어와 포인트 수심</li>
<li>권장 봉돌 호수와 합사 굵기</li>
<li>에기 수량이나 채비 형태 제한 여부</li>
<li>장비 대여 가능 여부와 대여 범위</li>
<li>집결 시간, 주차 위치, 귀항 예상 시간</li>
</ul>
</div>
</main>
"""
    (DOCS / "guide-autumn.html").write_text(
        page_shell(
            "갑오징어·주꾸미·문어 출조 비교 | 선상 조황 알리미",
            "가을 선상낚시 주요 대상어인 갑오징어, 주꾸미, 문어의 장비와 운용 차이를 비교합니다.",
            autumn_body,
        ),
        encoding="utf-8",
    )

    first_trip_body = """
<main class="wrap content">
<div class="eyebrow">Guide · 첫 출조</div>
<h1>첫 선상낚시 예약 전 체크리스트</h1>
<p>첫 출조는 장비보다 동선에서 실수가 많이 납니다. 예약을 끝낸 뒤 아래 순서대로 확인하면 새벽 출항 때 생기는 혼선을 크게 줄일 수 있습니다.</p>
<div class="info-box">
<h2>예약 직후</h2>
<ol><li>정확한 출항 항구와 선박명 저장</li><li>집결 시각과 출항 시각을 구분해 확인</li><li>취소·환불 및 기상 취소 기준 확인</li><li>장비 대여가 필요하면 사전 예약 여부 확인</li></ol>
<h2>출조 전날</h2>
<ol><li>선사의 최신 공지에서 봉돌 호수와 채비 규정 재확인</li><li>기상 변화와 출항 여부 확인</li><li>주차 위치와 선착장까지 도보 동선 확인</li><li>신분증, 멀미 대비품, 여벌 옷과 방수 준비</li></ol>
<h2>당일</h2>
<ol><li>집결 시간을 출항 시간으로 착각하지 않기</li><li>승선명부 작성과 안전 안내 우선</li><li>선장의 채비·안전 지시를 현장 기준으로 따르기</li></ol>
</div>
<div class="notice">구명조끼 등 안전장비와 승선 관련 사항은 현행 규정과 선사의 안내가 가장 우선합니다.</div>
</main>
"""
    (DOCS / "guide-first-trip.html").write_text(
        page_shell(
            "첫 선상낚시 체크리스트 | 선상 조황 알리미",
            "첫 선상낚시 예약 후 출조 전날과 당일에 확인할 항목을 정리합니다.",
            first_trip_body,
        ),
        encoding="utf-8",
    )

    about_body = """
<main class="wrap content">
<div class="eyebrow">About</div>
<h1>조황 복제 사이트가 아니라<br>업데이트 인덱스입니다.</h1>
<p>선상 조황 알리미는 여러 선사의 조황 사진과 글을 한곳에 복사해 쌓는 서비스를 지향하지 않습니다. 이용자가 원하는 배의 <strong>새 조황 등록 여부를 빠르게 발견</strong>하고, 최근 게시 활동을 비교하고, 알림을 받을 수 있게 만드는 것이 목적입니다.</p>
<div class="info-box">
<h2>공개 페이지에 표시하는 것</h2>
<ul><li>선박명과 지역</li><li>게시 날짜와 원문 링크</li><li>게시물에서 감지한 대상어 분류</li><li>최근 7일·30일 업데이트 횟수 같은 자체 집계</li></ul>
<h2>공개 페이지에 표시하지 않는 것</h2>
<ul><li>선사가 촬영한 조황 사진의 복사본</li><li>선장이 작성한 조황 본문 전체 또는 요약 재작성</li><li>사진을 근거로 임의 계산한 정확한 조과</li><li>근거 없는 선박 순위나 추천 점수</li></ul>
</div>
<p>원문에 대한 권리는 각 원문 제공자에게 있으며, 상세 조황은 각 선사의 원문 페이지에서 확인하도록 연결합니다.</p>
</main>
"""
    (DOCS / "about.html").write_text(
        page_shell(
            "서비스 소개 | 선상 조황 알리미",
            "선상 조황 알리미의 데이터 수집 범위와 원문 비복제 운영 원칙을 설명합니다.",
            about_body,
        ),
        encoding="utf-8",
    )

    copyright_body = """
<main class="wrap content">
<div class="eyebrow">Source & rights</div>
<h1>저작권·출처 정책</h1>
<p>본 서비스는 외부 선사의 조황 사진이나 작성 글을 복제해 제공하지 않는 것을 원칙으로 합니다. 공개 페이지에는 게시 날짜, 선박명, 지역, 대상어 분류, 원문 링크와 자체 집계정보를 중심으로 표시합니다.</p>
<div class="info-box">
<h2>원문 링크</h2><p>각 조황의 상세 내용과 사진은 원문 제공 사이트에서 확인할 수 있도록 외부 링크를 제공합니다.</p>
<h2>권리자의 요청</h2><p>링크 노출이나 선박 정보 표시에 관해 권리자 또는 운영자의 수정·제외 요청이 있으면 확인 후 반영할 수 있습니다. 문의 페이지의 공개 저장소 Issues를 이용해 요청해 주세요.</p>
<h2>자동 분류의 한계</h2><p>어종 표시는 게시물에서 확인되는 키워드를 기계적으로 분류한 것으로, 실제 출조의 전체 대상어를 뜻하지 않을 수 있습니다.</p>
</div>
</main>
"""
    (DOCS / "copyright.html").write_text(
        page_shell(
            "저작권·출처 정책 | 선상 조황 알리미",
            "외부 조황 콘텐츠를 복제하지 않는 운영 원칙과 출처·수정 요청 정책을 안내합니다.",
            copyright_body,
        ),
        encoding="utf-8",
    )

    privacy_body = """
<main class="wrap content">
<div class="eyebrow">Privacy</div>
<h1>개인정보처리방침</h1>
<p>카카오 조황 알림 기능을 사용하는 경우 서비스 운영에 필요한 최소한의 정보를 저장합니다.</p>
<div class="info-box">
<h2>수집·이용 항목</h2><ul><li>카카오 로그인으로 확인되는 앱 사용자 식별값</li><li>사용자가 선택한 알림 선박</li><li>조황 알림 수신 동의 여부</li><li>알림 발송 이력 및 오류 기록</li></ul>
<h2>이용 목적</h2><p>로그인 상태 유지, 선박 구독 설정, 중복 알림 방지, 서비스 장애 확인에 사용합니다.</p>
<h2>보관</h2><p>서비스 이용 중 필요한 범위에서 보관하며, 향후 알림 해지·탈퇴 기능을 통해 삭제할 수 있도록 운영 기능을 보완합니다.</p>
<h2>외부 서비스</h2><p>데이터 저장과 서버 기능에는 Supabase를 사용하며, 카카오 로그인 및 향후 메시지 전송에는 카카오 서비스를 사용할 수 있습니다.</p>
</div>
</main>
"""
    (DOCS / "privacy.html").write_text(
        page_shell(
            "개인정보처리방침 | 선상 조황 알리미",
            "카카오 로그인과 조황 알림 기능에서 처리하는 정보와 이용 목적을 안내합니다.",
            privacy_body,
        ),
        encoding="utf-8",
    )

    terms_body = """
<main class="wrap content">
<div class="eyebrow">Terms</div>
<h1>이용약관</h1>
<div class="info-box">
<h2>서비스 성격</h2><p>본 서비스는 선박별 조황 게시 여부와 자체 집계정보를 편리하게 확인하기 위한 정보 제공 도구입니다.</p>
<h2>정보의 한계</h2><p>외부 사이트의 게시 상태, 네트워크 오류, 자동 분류 오차 등으로 정보가 늦거나 누락될 수 있습니다. 예약·출항·기상·안전 관련 최종 정보는 반드시 해당 선사와 공식 안내에서 확인해야 합니다.</p>
<h2>외부 링크</h2><p>외부 사이트의 내용과 서비스 운영은 해당 사이트의 책임 범위에 있으며, 본 서비스는 외부 사이트의 거래나 예약을 대신하지 않습니다.</p>
</div>
</main>
"""
    (DOCS / "terms.html").write_text(
        page_shell(
            "이용약관 | 선상 조황 알리미",
            "선상 조황 알리미의 정보 제공 범위와 외부 링크, 자동 수집 정보의 한계를 안내합니다.",
            terms_body,
        ),
        encoding="utf-8",
    )

    contact_body = """
<main class="wrap content">
<div class="eyebrow">Contact</div>
<h1>문의</h1>
<p>선박 정보 수정, 링크 제외, 권리 관련 요청, 서비스 오류 제보는 공개 프로젝트 저장소의 Issues 기능을 이용할 수 있습니다.</p>
<div class="info-box">
<h2>문의 시 포함하면 좋은 내용</h2>
<ul><li>대상 선박 또는 페이지 주소</li><li>수정·삭제를 원하는 정보</li><li>운영자·권리자 확인에 필요한 설명</li></ul>
<p><a class="btn btn-primary" href="https://github.com/ddungja/fishing-report-site/issues" target="_blank" rel="noopener noreferrer">GitHub Issues에서 문의하기 ↗</a></p>
</div>
</main>
"""
    (DOCS / "contact.html").write_text(
        page_shell(
            "문의 | 선상 조황 알리미",
            "선박 정보 수정, 링크 제외, 권리 관련 요청과 서비스 오류 문의 방법을 안내합니다.",
            contact_body,
        ),
        encoding="utf-8",
    )

    notify_body = """
<main class="wrap content">
<div class="notify-wrap">
<div class="eyebrow">Kakao alert</div>
<h1>카카오 조황 알림</h1>
<p class="muted">카카오로 로그인한 뒤 원하는 선박을 선택하세요. 여러 건이 한꺼번에 올라와도 사용자별로 시간당 최대 1회 묶어서 알림을 보내는 구조입니다.</p>

<div class="notify-box" id="loginBox">
<h2>1. 카카오 로그인</h2>
<p>로그인 후 내 알림 선박을 선택할 수 있습니다.</p>
<a class="kakao-login" id="loginBtn" href="#">카카오로 시작하기</a>
</div>

<div class="notify-box" id="settingsBox" style="display:none">
<h2>2. 알림 받을 선박 선택</h2>
<p id="hello" class="muted"></p>
<div id="boats"></div>
<label class="consent">
<input type="checkbox" id="consent">
<span><strong>조황 알림 수신에 동의합니다.</strong><br><small class="muted">선택한 선박의 새 조황을 시간당 최대 1회 묶음으로 전송합니다.</small></span>
</label>
<button class="save-btn" id="saveBtn">알림 설정 저장</button>
<div class="status" id="status"></div>
</div>

<div class="notify-box">
<h2>알림 운영 기준</h2>
<ul>
<li>선택한 선박의 새 조황 등록만 안내</li>
<li>같은 시간에 여러 건이 등록되면 최대 1회로 묶음</li>
<li>새 조황이 없으면 메시지를 보내지 않음</li>
<li>야간에는 발송을 쉬고 다음 허용 시간에 합산</li>
</ul>
</div>
</div>
</main>

<script>
const API='https://umdkypcbdcrerhfnnybi.supabase.co/functions/v1/kakao-auth';
const loginBox=document.getElementById('loginBox');
const settingsBox=document.getElementById('settingsBox');
const boatsEl=document.getElementById('boats');
const consentEl=document.getElementById('consent');
const statusEl=document.getElementById('status');
document.getElementById('loginBtn').href=API+'?op=login';

const hash=new URLSearchParams(location.hash.replace(/^#/,''));
const incoming=hash.get('session');
if(incoming){
  localStorage.setItem('kakao_notify_session',incoming);
  history.replaceState(null,'',location.pathname);
}
function token(){return localStorage.getItem('kakao_notify_session')||''}
function showStatus(msg){statusEl.textContent=msg;statusEl.style.display='block'}

async function load(){
  if(!token()) return;
  const r=await fetch(API+'?op=status',{headers:{'x-session-token':token()}});
  if(!r.ok){localStorage.removeItem('kakao_notify_session');return}
  const data=await r.json();
  loginBox.style.display='none';
  settingsBox.style.display='block';
  document.getElementById('hello').textContent=(data.user?.nickname?data.user.nickname+'님, ':'')+'알림 받을 선박을 선택하세요.';
  consentEl.checked=!!data.user?.notification_consent;
  boatsEl.innerHTML='';
  (data.boats||[]).forEach(function(b){
    const label=document.createElement('label');
    label.className='boat-row';
    label.innerHTML='<input type="checkbox" value="'+b.id+'" '+(b.selected?'checked':'')+'><strong>'+b.name+'</strong>';
    boatsEl.appendChild(label);
  });
}
document.getElementById('saveBtn').onclick=async function(){
  const boat_ids=[...boatsEl.querySelectorAll('input:checked')].map(function(x){return x.value});
  if(!consentEl.checked){showStatus('조황 알림 수신 동의에 체크해주세요.');return}
  const r=await fetch(API+'?op=save',{
    method:'POST',
    headers:{'content-type':'application/json','x-session-token':token()},
    body:JSON.stringify({boat_ids:boat_ids,notification_consent:true})
  });
  if(r.ok) showStatus('저장되었습니다. 선택한 선박의 새 조황 등록을 묶어서 알려드립니다.');
  else showStatus('저장 중 오류가 발생했습니다.');
};
load();
</script>
"""
    (DOCS / "notify.html").write_text(
        page_shell(
            "카카오 조황 알림 | 선상 조황 알리미",
            "카카오 로그인 후 원하는 선박을 선택해 새 조황 등록 알림을 받을 수 있습니다.",
            notify_body,
        ),
        encoding="utf-8",
    )

    pages = [
        "index.html", "boats.html", "insights.html", "guide.html",
        "guide-reading.html", "guide-autumn.html", "guide-first-trip.html",
        "notify.html", "about.html", "copyright.html", "privacy.html",
        "terms.html", "contact.html",
    ]

    (DOCS / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n",
        encoding="utf-8",
    )

    sitemap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path in pages:
        sitemap.append(f"<url><loc>{BASE_URL}/{quote(path)}</loc></url>")
    sitemap.append("</urlset>")
    (DOCS / "sitemap.xml").write_text("\n".join(sitemap), encoding="utf-8")

    (DOCS / "ads.txt").write_text(
        "# AdSense publisher ID 발급 후 공식 ads.txt 항목을 추가합니다.\n",
        encoding="utf-8",
    )

    public_data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": [
            {
                "id": source["id"],
                "name": source["name"],
                "region": source.get("region", ""),
                "harbor": source.get("harbor", ""),
                "latest_date": (
                    source_results.get(source["id"], [{}])[0].get("date")
                    if source_results.get(source["id"])
                    else None
                ),
                "updates_7d": stat_for(source_results.get(source["id"], []), 7),
                "updates_30d": stat_for(source_results.get(source["id"], []), 30),
            }
            for source in sources
        ],
    }
    (DOCS / "data.json").write_text(
        json.dumps(public_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"docs 생성 완료: 선박 {len(sources)}척, "
        f"수집 게시물 {len(all_posts)}건, 외부 이미지 저장 0건"
    )


if __name__ == "__main__":
    build()
