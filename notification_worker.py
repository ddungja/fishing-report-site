import os
import json
import hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import generate_site as site

KST = ZoneInfo("Asia/Seoul")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

KAKAO_BUSINESS_ACCESS_TOKEN = os.getenv("KAKAO_BUSINESS_ACCESS_TOKEN", "")
KAKAO_AD_ACCOUNT_ID = os.getenv("KAKAO_AD_ACCOUNT_ID", "")
KAKAO_CREATIVE_ID = os.getenv("KAKAO_CREATIVE_ID", "")

PUBLIC_SITE_URL = os.getenv(
    "PUBLIC_SITE_URL",
    "https://ddungja.github.io/fishing-report-site/"
).rstrip("/") + "/"

DRY_RUN = os.getenv("NOTIFICATION_DRY_RUN", "1") != "0"

# 서비스 운영 정책: 밤에는 발송하지 않고 다음 허용 시간에 묶어서 전송.
SEND_START_HOUR = int(os.getenv("SEND_START_HOUR", "8"))
SEND_END_HOUR = int(os.getenv("SEND_END_HOUR", "21"))

MAX_LINES = int(os.getenv("MAX_DIGEST_LINES", "6"))


def supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def sb(method, path, *, params=None, payload=None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Supabase secrets are not configured")

    r = requests.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers=supabase_headers(),
        params=params,
        json=payload,
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase {method} {path}: {r.status_code} {r.text}")
    if r.text.strip():
        return r.json()
    return None


def report_key(source_id, post):
    raw = f"{source_id}|{post.get('key','')}|{post.get('detail_url','')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def sync_latest_reports():
    cfg = site.load_json(site.CFG, {"sources": []})
    inserted = []

    for source in cfg.get("sources", []):
        if not source.get("enabled", True):
            continue

        posts = site.collect_source(source)

        for post in posts:
            key = report_key(source["id"], post)

            existing = sb(
                "GET",
                "notify_reports",
                params={
                    "select": "id",
                    "boat_id": f"eq.{source['id']}",
                    "report_key": f"eq.{key}",
                    "limit": "1",
                },
            )

            if existing:
                continue

            row = {
                "boat_id": source["id"],
                "report_key": key,
                "title": post.get("title") or "새 조황",
                "report_date": post.get("date") or None,
                "source_url": post.get("detail_url") or source.get("board_url"),
            }

            created = sb("POST", "notify_reports", payload=row)
            if created:
                inserted.extend(created)

    return inserted


def in_send_window(now_kst):
    return SEND_START_HOUR <= now_kst.hour < SEND_END_HOUR


def get_active_users():
    return sb(
        "GET",
        "notify_users",
        params={
            "select": "id,kakao_user_id,nickname,last_digest_at",
            "active": "eq.true",
            "notification_consent": "eq.true",
        },
    ) or []


def get_user_boats(user_id):
    rows = sb(
        "GET",
        "notify_subscriptions",
        params={
            "select": "boat_id,notify_boats(name)",
            "user_id": f"eq.{user_id}",
            "active": "eq.true",
        },
    ) or []

    return {
        row["boat_id"]: (
            (row.get("notify_boats") or {}).get("name")
            or row["boat_id"]
        )
        for row in rows
    }


def get_pending_reports(last_digest_at, boat_ids):
    if not boat_ids:
        return []

    return sb(
        "GET",
        "notify_reports",
        params={
            "select": "id,boat_id,title,report_date,source_url,discovered_at",
            "boat_id": f"in.({','.join(boat_ids)})",
            "discovered_at": f"gt.{last_digest_at}",
            "order": "discovered_at.asc",
        },
    ) or []


def build_digest(reports, boat_names):
    grouped = {}

    for report in reports:
        boat_id = report["boat_id"]
        grouped.setdefault(boat_id, []).append(report)

    title = f"새 조황 {len(reports)}건"

    lines = []
    for boat_id, items in grouped.items():
        name = boat_names.get(boat_id, boat_id)
        lines.append(f"• {name} {len(items)}건")

    recent_titles = []
    for report in reports[:MAX_LINES]:
        recent_titles.append(f"- {report['title'][:55]}")

    more = len(reports) - len(recent_titles)
    body_parts = lines + recent_titles
    if more > 0:
        body_parts.append(f"외 {more}건")

    body = "\n".join(body_parts)
    return title, body


def kakao_send(app_user_id, title, body):
    if DRY_RUN:
        print("[DRY-RUN]", app_user_id, title)
        print(body)
        return {"status": "DRY_RUN", "requestId": None}

    required = [
        KAKAO_BUSINESS_ACCESS_TOKEN,
        KAKAO_AD_ACCOUNT_ID,
        KAKAO_CREATIVE_ID,
    ]
    if not all(required):
        raise RuntimeError("Kakao Moment secrets are not configured")

    url = (
        "https://apis.moment.kakao.com/openapi/v4/messages/creatives/"
        f"{KAKAO_CREATIVE_ID}/sendPersonalMessage"
    )

    now = datetime.now(KST)
    serial = (
        f"{now:%Y%m%d}-{KAKAO_CREATIVE_ID}-"
        f"{hashlib.md5((app_user_id + now.isoformat()).encode()).hexdigest()[:8]}"
    )[:39]

    payload = {
        "messageSerialNumber": serial,
        "receiverType": "APP_USER_ID",
        "receiverKey": str(app_user_id),
        "variables": {
            "digest_title": title,
            "digest_body": body,
            "digest_url": PUBLIC_SITE_URL,
        },
    }

    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {KAKAO_BUSINESS_ACCESS_TOKEN}",
            "adAccountId": KAKAO_AD_ACCOUNT_ID,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if not r.ok:
        raise RuntimeError(f"Kakao send failed: {r.status_code} {r.text}")

    return r.json()


def digest_bucket(now_kst):
    return now_kst.replace(minute=0, second=0, microsecond=0).isoformat()


def send_hourly_digests():
    now_kst = datetime.now(KST)

    if not in_send_window(now_kst):
        print("Quiet hours: reports will be carried to the next send window.")
        return

    for user in get_active_users():
        boats = get_user_boats(user["id"])
        if not boats:
            continue

        reports = get_pending_reports(
            user["last_digest_at"],
            list(boats.keys()),
        )
        if not reports:
            continue

        bucket = digest_bucket(now_kst)

        already = sb(
            "GET",
            "notify_delivery_log",
            params={
                "select": "id",
                "user_id": f"eq.{user['id']}",
                "digest_bucket": f"eq.{bucket}",
                "limit": "1",
            },
        )
        if already:
            continue

        title, body = build_digest(reports, boats)

        try:
            result = kakao_send(
                user["kakao_user_id"],
                title,
                body,
            )

            sb(
                "POST",
                "notify_delivery_log",
                payload={
                    "user_id": user["id"],
                    "digest_bucket": bucket,
                    "report_count": len(reports),
                    "status": result.get("status", "SUCCEEDED"),
                    "kakao_request_id": result.get("requestId"),
                },
            )

            sb(
                "PATCH",
                "notify_users",
                params={"id": f"eq.{user['id']}"},
                payload={
                    "last_digest_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        except Exception as e:
            print("SEND ERROR:", user["id"], e)
            sb(
                "POST",
                "notify_delivery_log",
                payload={
                    "user_id": user["id"],
                    "digest_bucket": bucket,
                    "report_count": len(reports),
                    "status": "FAILED",
                    "error_message": str(e)[:1000],
                },
            )


def main():
    print("1) Sync fishing reports")
    new_reports = sync_latest_reports()
    print(f"New reports discovered: {len(new_reports)}")

    print("2) Build and send hourly user digests")
    send_hourly_digests()


if __name__ == "__main__":
    main()
