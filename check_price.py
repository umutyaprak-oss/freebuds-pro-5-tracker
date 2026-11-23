"""
Huawei FreeBuds Pro 5 - 7/24 Fiyat Takip ve Gmail Bildirim Scripti
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# --- Ayarlar ---
AKAKCE_URL = (
    "https://www.akakce.com/bluetooth-kulaklik/"
    "en-ucuz-huawei-freebuds-pro-5-anc-tws-beyaz-kulak-ici-fiyati,1430910638.html"
)
VATAN_URL = (
    "https://www.vatanbilgisayar.com/"
    "huawei-freebuds-pro-5-bluetooth-kablosuz-kulak-ici-kulaklik-gri.html"
)

GOOD_DEAL_THRESHOLD = 7500.0
URGENT_THRESHOLD = 7000.0

SUBJECT_GOOD_DEAL = "[İYİ İNDİRİM] FreeBuds Pro 5"
SUBJECT_URGENT = "[ACİL - 7000 TL ALTI!] FreeBuds Pro 5"
SUBJECT_TEST = "[TEST] FreeBuds Pro 5 Takip Sistemi Çalışıyor"

EXCLUDED_KEYWORDS = [
    "ambalaj hasarlı",
    "ambalaj hasarli",
    "teşhir",
    "teshir",
    "ikinci el",
    "2. el",
    "tek kulaklık",
    "tek kulaklik",
    "şarj kutusu",
    "sarj kutusu",
    "kılıf",
    "kilif",
]

STATE_FILE = Path(__file__).resolve().parent / "last_price.json"


@dataclass
class Offer:
    source: str
    seller: str
    title: str
    price: float
    shipping: str
    url: str


def normalize_turkish(text: str) -> str:
    """Türkçe büyük/küçük harf duyarlılığını normalize eder."""
    return (
        text.replace("İ", "i")
        .replace("I", "ı")
        .lower()
        .strip()
    )


def is_excluded(text: str) -> Optional[str]:
    """Metinde yasaklı kelime varsa eşleşen kelimeyi döndürür."""
    norm = normalize_turkish(text)
    for kw in EXCLUDED_KEYWORDS:
        if normalize_turkish(kw) in norm:
            return kw
    return None


def parse_tr_price(raw: str) -> Optional[float]:
    """
    Türkçe fiyat metnini float'a çevirir.
    Örnekler: '8.499,00 TL' -> 8499.0, '8.489' -> 8489.0, '8489' -> 8489.0
    """
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.,]", "", raw.strip())
    if not cleaned:
        return None

    # Hem '.' hem ',' varsa: '.' binlik, ',' ondalık ayırıcıdır (ör: 8.499,00)
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        # Sadece ',' varsa: ondalık ayırıcıdır (ör: 8499,00)
        cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        # Sadece '.' varsa: son grupta 3 rakam varsa binlik ayırıcıdır (ör: 8.489)
        parts = cleaned.split(".")
        if len(parts[-1]) == 3:
            cleaned = "".join(parts)

    try:
        value = float(cleaned)
        return value if value > 0 else None
    except ValueError:
        return None


def fetch_html_playwright(url: str) -> Optional[str]:
    """curl_cffi engellenirse opsiyonel Playwright fallback."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    print(f"[INFO] Playwright fallback deneniyor: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="tr-TR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=35000)
        page.wait_for_timeout(2500)
        content = page.content()
        browser.close()
        return content


def fetch_html(url: str) -> str:
    """Bot korumasını aşmak için curl_cffi (Chrome TLS impersonation) kullanır."""
    headers = {
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
    }
    profiles = ["chrome", "chrome124", "safari15_5"]
    last_error: Optional[Exception] = None

    for profile in profiles:
        try:
            resp = curl_requests.get(
                url,
                impersonate=profile,
                headers=headers,
                timeout=25,
            )
            if resp.status_code == 200 and len(resp.text) > 5000:
                # Cloudflare challenge sayfası mı kontrol et
                low = resp.text[:3000].lower()
                if "just a moment" not in low and "cf-browser-verification" not in low:
                    return resp.text
            last_error = RuntimeError(f"HTTP {resp.status_code} (profil={profile})")
        except Exception as exc:
            last_error = exc

    pw_html = fetch_html_playwright(url)
    if pw_html:
        return pw_html

    raise RuntimeError(f"Sayfa çekilemedi ({url}): {last_error}")


def parse_akakce(page_html: str, url: str) -> tuple[Optional[Offer], int, int]:
    """
    Akakçe satıcı listesini (#PL li) tarar, yasaklı ilanları eler
    ve en ucuz geçerli teklifi döndürür.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    items = soup.select("#PL li")

    valid_offers: list[Offer] = []
    filtered_count = 0

    for li in items:
        price_el = li.select_one(".pt_v8")
        if not price_el:
            continue
        price = parse_tr_price(price_el.get_text(" ", strip=True))
        if price is None:
            continue

        title_el = li.select_one(".pn_v8")
        title = title_el.get_text(" ", strip=True) if title_el else ""

        desc_el = li.select_one(".vds_v8")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""

        badge_el = li.select_one(".bdgv_v8")
        badge = badge_el.get_text(" ", strip=True) if badge_el else ""

        seller_el = li.select_one(".v_v8")
        seller_parts: list[str] = []
        if seller_el:
            img = seller_el.find("img", alt=True)
            if img and img.get("alt"):
                seller_parts.append(img["alt"].strip())
            extra_seller = seller_el.get_text(" ", strip=True)
            if extra_seller:
                seller_parts.append(extra_seller)
        seller = " ".join(seller_parts).strip() or "Akakçe Satıcısı"

        shipping_el = li.select_one(".uk_v8, .n_uk_v8")
        shipping = shipping_el.get_text(" ", strip=True) if shipping_el else ""

        check_text = f"{title} {desc} {badge} {seller}"
        matched_kw = is_excluded(check_text)
        if matched_kw:
            filtered_count += 1
            print(f"  [FİLTRE - Akakçe] '{matched_kw}' nedeniyle elendi: {title} ({price:.2f} TL)")
            continue

        valid_offers.append(
            Offer(
                source="Akakçe",
                seller=seller,
                title=title or "Huawei FreeBuds Pro 5",
                price=price,
                shipping=shipping or "Belirtilmemiş",
                url=url,
            )
        )

    if not valid_offers:
        return None, 0, filtered_count

    best = min(valid_offers, key=lambda o: o.price)
    return best, len(valid_offers), filtered_count


def parse_vatan(page_html: str, url: str) -> Optional[Offer]:
    """
    Vatan Bilgisayar ürün sayfasından JSON-LD (ve gerekirse DOM fallback) ile
    fiyat ve stok durumunu okur.
    """
    soup = BeautifulSoup(page_html, "html.parser")

    # 1) Öncelik: JSON-LD Product bloğu
    for script in soup.find_all("script", type="application/ld+json"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw or '"Product"' not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(data, list):
            data = next((d for d in data if isinstance(d, dict) and d.get("@type") == "Product"), None)
        if not isinstance(data, dict) or data.get("@type") != "Product":
            continue

        name = html.unescape(str(data.get("name", "")).strip())
        offers = data.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if not isinstance(offers, dict):
            continue

        availability = str(offers.get("availability", ""))
        if availability and "outofstock" in availability.lower():
            print("  [BİLGİ - Vatan] Ürün stokta değil (OutOfStock).")
            return None

        condition = str(offers.get("itemCondition", ""))
        if condition and "newcondition" not in condition.lower():
            print(f"  [FİLTRE - Vatan] Ürün durumu sıfır değil: {condition}")
            return None

        matched_kw = is_excluded(name)
        if matched_kw:
            print(f"  [FİLTRE - Vatan] '{matched_kw}' nedeniyle elendi: {name}")
            return None

        price = parse_tr_price(str(offers.get("price", "")))
        if price is not None:
            return Offer(
                source="Vatan Bilgisayar",
                seller="Vatan Bilgisayar",
                title=name or "Huawei FreeBuds Pro 5",
                price=price,
                shipping="Ücretsiz kargo",
                url=url,
            )

    # 2) Fallback: DOM seçicileri
    h1 = soup.select_one("h1")
    title = h1.get_text(" ", strip=True) if h1 else "Huawei FreeBuds Pro 5"
    matched_kw = is_excluded(title)
    if matched_kw:
        print(f"  [FİLTRE - Vatan] '{matched_kw}' nedeniyle elendi: {title}")
        return None

    price_el = soup.select_one(".product-list__price, .product-detail__price")
    if price_el:
        price = parse_tr_price(price_el.get_text(" ", strip=True))
        if price is not None:
            return Offer(
                source="Vatan Bilgisayar",
                seller="Vatan Bilgisayar",
                title=title,
                price=price,
                shipping="Belirtilmemiş",
                url=url,
            )

    return None


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_notified_price": None}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_notified_price": None}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def determine_alert(price: float) -> Optional[tuple[str, str]]:
    """Fiyat eşik altındaysa (tier, subject) döndürür."""
    if price < URGENT_THRESHOLD:
        return "urgent_7000", SUBJECT_URGENT
    if price < GOOD_DEAL_THRESHOLD:
        return "good_7500", SUBJECT_GOOD_DEAL
    return None


def send_email_alert(
    subject: str,
    best: Offer,
    all_offers: list[Offer],
    prev_price: Optional[float],
    is_test: bool = False,
) -> bool:
    gmail_user = os.environ.get("GMAIL_USER", "").strip()
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    gmail_to = os.environ.get("GMAIL_TO", "").strip() or gmail_user

    if not gmail_user or not gmail_app_password:
        pass

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    prev_text = f"{prev_price:,.2f} TL" if prev_price is not None else "Yok (ilk kez eşik altı)"

    header_line = (
        "Bu bir TEST e-postasıdır. Takip sistemi ve Gmail bildirim bağlantısı sorunsuz çalışıyor! "
        "(last_price.json güncellenmedi.)"
        if is_test
        else "Huawei FreeBuds Pro 5 için hedef fiyat uyarısı!"
    )

    lines = [
        header_line,
        "",
        f"En Ucuz Geçerli Fiyat : {best.price:,.2f} TL",
        f"Kaynak                : {best.source} ({best.seller})",
        f"Ürün Başlığı          : {best.title}",
        f"Kargo                 : {best.shipping}",
        f"Önceki Bildirilen Dip : {prev_text}",
        f"Link                  : {best.url}",
        "",
        "--- Tüm Kontrol Edilen Kaynaklar ---",
    ]
    for offer in all_offers:
        lines.append(
            f"- {offer.source} ({offer.seller}): {offer.price:,.2f} TL | {offer.shipping} | {offer.url}"
        )
    lines.append("")
    lines.append(f"Kontrol Zamanı: {now_str}")

    body_text = "\n".join(lines)
    sent_any = False

    # 1) GitHub Issue bildirimi (Şifresiz / Secret gerektirmez — GitHub otomatik mail atar)
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    gh_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    gh_owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()

    if gh_token and gh_repo:
        issue_body = f"@{gh_owner} " + body_text if gh_owner else body_text
        payload = json.dumps({"title": subject, "body": issue_body}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.github.com/repos/{gh_repo}/issues",
            data=payload,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {gh_token}",
                "Content-Type": "application/json",
                "User-Agent": "freebuds-pro-5-tracker",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                issue_data = json.loads(resp.read().decode("utf-8"))
                print(f"[GITHUB ISSUE AÇILDI] #{issue_data.get('number')} -> {issue_data.get('html_url')}")
                sent_any = True
        except urllib.error.URLError as exc:
            print(f"[UYARI] GitHub Issue açılamadı: {exc}", file=sys.stderr)

    # 2) Opsiyonel Gmail SMTP bildirimi (GMAIL_USER ve GMAIL_APP_PASSWORD tanımlıysa)
    if gmail_user and gmail_app_password:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = gmail_user
        msg["To"] = gmail_to
        msg.set_content(body_text)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(gmail_user, gmail_app_password)
            smtp.send_message(msg)

        print(f"[GMAIL GÖNDERİLDİ] '{subject}' -> {gmail_to}")
        sent_any = True
    elif not sent_any:
        print(
            "[UYARI] Ne GITHUB_TOKEN ne de GMAIL_USER/GMAIL_APP_PASSWORD tanımlı; "
            "bildirim gönderilemedi."
        )

    return sent_any


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Huawei FreeBuds Pro 5 fiyat takip ve Gmail bildirim scripti."
    )
    parser.add_argument(
        "--test-mail",
        action="store_true",
        help=(
            "Fiyat eşiğin üzerinde olsa bile mevcut en ucuz fiyatla test maili gönderir "
            "(last_price.json dosyasını değiştirmez)."
        ),
    )
    return parser.parse_args(argv)


def is_test_mail_enabled(args: argparse.Namespace) -> bool:
    if args.test_mail:
        return True
    env_val = os.environ.get("SEND_TEST_MAIL", "").strip().lower()
    return env_val in {"1", "true", "yes", "on"}


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    test_mail_mode = is_test_mail_enabled(args)

    print("=" * 60)
    print("Huawei FreeBuds Pro 5 Fiyat Kontrolü Başlatılıyor...")
    if test_mail_mode:
        print("[MOD] Test maili modu aktif (last_price.json değiştirilmeyecek).")
    print("=" * 60)

    offers: list[Offer] = []

    # 1. Akakçe
    try:
        akakce_html = fetch_html(AKAKCE_URL)
        akakce_offer, valid_cnt, filtered_cnt = parse_akakce(akakce_html, AKAKCE_URL)
        if akakce_offer:
            offers.append(akakce_offer)
            print(
                f"[Akakçe] En ucuz geçerli fiyat: {akakce_offer.price:,.2f} TL "
                f"| Satıcı: {akakce_offer.seller} | Kargo: {akakce_offer.shipping} "
                f"(Geçerli: {valid_cnt}, Elenen: {filtered_cnt})"
            )
        else:
            print(f"[Akakçe] Geçerli ilan bulunamadı (Elenen: {filtered_cnt}).")
    except Exception as exc:
        print(f"[HATA - Akakçe] {exc}", file=sys.stderr)

    # 2. Vatan Bilgisayar
    try:
        vatan_html = fetch_html(VATAN_URL)
        vatan_offer = parse_vatan(vatan_html, VATAN_URL)
        if vatan_offer:
            offers.append(vatan_offer)
            print(
                f"[Vatan]  Güncel fiyat: {vatan_offer.price:,.2f} TL "
                f"| Ürün: {vatan_offer.title}"
            )
        else:
            print("[Vatan]  Geçerli veya stokta ürün bulunamadı.")
    except Exception as exc:
        print(f"[HATA - Vatan] {exc}", file=sys.stderr)

    if not offers:
        print("[HATA] Hiçbir kaynaktan geçerli fiyat çekilemedi.", file=sys.stderr)
        return 1

    best_offer = min(offers, key=lambda o: o.price)
    print("-" * 60)
    print(
        f"GENEL EN UCUZ: {best_offer.price:,.2f} TL "
        f"({best_offer.source} - {best_offer.seller})"
    )

    state = load_state()
    last_notified_price = state.get("last_notified_price")

    if test_mail_mode:
        print(f"[TEST MAİLİ] Gönderiliyor -> {SUBJECT_TEST} ({best_offer.price:,.2f} TL)")
        sent = send_email_alert(
            SUBJECT_TEST,
            best_offer,
            offers,
            last_notified_price,
            is_test=True,
        )
        print("[BİLGİ] Test maili modu nedeniyle last_price.json güncellenmedi.")
        return 0 if sent else 1

    alert = determine_alert(best_offer.price)
    if alert is None:
        print(
            f"[BİLGİ] Fiyat ({best_offer.price:,.2f} TL) alarm eşiğinin "
            f"({GOOD_DEAL_THRESHOLD:,.0f} TL) üzerinde. Mail gönderilmeyecek."
        )
        return 0

    tier, subject = alert
    should_notify = (
        last_notified_price is None or best_offer.price < float(last_notified_price)
    )

    if not should_notify:
        print(
            f"[SPAM KORUMASI] Fiyat eşik altında ({best_offer.price:,.2f} TL) ancak "
            f"son bildirilen dipten ({float(last_notified_price):,.2f} TL) daha düşük değil. "
            "Mail gönderilmeyecek."
        )
        return 0

    print(f"[ALARM] Tetiklendi -> {subject} ({best_offer.price:,.2f} TL)")
    sent = send_email_alert(subject, best_offer, offers, last_notified_price)
    if sent:
        new_state = {
            "last_notified_price": best_offer.price,
            "last_notified_source": best_offer.source,
            "last_notified_seller": best_offer.seller,
            "last_notified_url": best_offer.url,
            "last_notified_tier": tier,
            "last_notified_at": datetime.now(timezone.utc).isoformat(),
            "latest_prices": {
                o.source: {
                    "price": o.price,
                    "seller": o.seller,
                    "title": o.title,
                    "url": o.url,
                }
                for o in offers
            },
        }
        save_state(new_state)
        print(f"[KAYDEDİLDİ] {STATE_FILE.name} yeni dip fiyat ile güncellendi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
