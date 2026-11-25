"""
7/24 Çoklu Ürün Fiyat Takip ve Bildirim Scripti
Takip edilen ürünler:
  1. Huawei FreeBuds Pro 5 (Tüm renkler - Akakçe & Vatan Bilgisayar)
  2. GameSir Super Nova Kablolu/Kablosuz Oyun Kolu - Mavi (Akakçe - Sadece popüler siteler)
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
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
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


# --- Popüler / Güvenilir Mağaza Listesi ---
POPULAR_STORES = [
    "amazon",
    "trendyol",
    "hepsiburada",
    "n11",
    "teknosa",
    "mediamarkt",
    "media markt",
    "vatan",
    "pazarama",
    "idefix",
    "itopya",
    "incehesap",
    "sinerji",
    "gaming.gen",
    "gaminggen",
    "wraith",
    "gizala",
    "connectgame",
    "connect game",
    "d&r",
    "pasaj",
]

COMMON_EXCLUDED_KEYWORDS = [
    "ambalaj hasarlı",
    "ambalaj hasarli",
    "teşhir",
    "teshir",
    "ikinci el",
    "2. el",
    "kılıf",
    "kilif",
]

STATE_FILE = Path(__file__).resolve().parent / "last_price.json"


@dataclass
class ProductConfig:
    product_id: str
    name: str
    good_deal_threshold: float
    urgent_threshold: float
    min_valid_price: float
    subject_good_deal: str
    subject_urgent: str
    akakce_urls: list[str] = field(default_factory=list)
    akakce_search_url: Optional[str] = None
    akakce_href_token: Optional[str] = None
    akakce_name_token: Optional[str] = None
    vatan_urls: list[str] = field(default_factory=list)
    vatan_search_url: Optional[str] = None
    extra_excluded_keywords: list[str] = field(default_factory=list)
    popular_stores_only: bool = False


PRODUCTS: list[ProductConfig] = [
    ProductConfig(
        product_id="freebuds_pro_5",
        name="Huawei FreeBuds Pro 5",
        good_deal_threshold=7500.0,
        urgent_threshold=7000.0,
        min_valid_price=3000.0,
        subject_good_deal="[İYİ İNDİRİM] FreeBuds Pro 5",
        subject_urgent="[ACİL - 7000 TL ALTI!] FreeBuds Pro 5",
        akakce_search_url="https://www.akakce.com/arama/?q=huawei+freebuds+pro+5",
        akakce_href_token="freebuds-pro-5",
        akakce_name_token="freebuds pro 5",
        akakce_urls=[
            "https://www.akakce.com/bluetooth-kulaklik/en-ucuz-huawei-freebuds-pro-5-anc-tws-beyaz-kulak-ici-fiyati,1430910638.html",
        ],
        vatan_search_url="https://www.vatanbilgisayar.com/arama/huawei%20freebuds%20pro%205/",
        vatan_urls=[
            "https://www.vatanbilgisayar.com/huawei-freebuds-pro-5-bluetooth-kablosuz-kulak-ici-kulaklik-gri.html",
        ],
        extra_excluded_keywords=[
            "tek kulaklık",
            "tek kulaklik",
            "şarj kutusu",
            "sarj kutusu",
        ],
        popular_stores_only=False,
    ),
    ProductConfig(
        product_id="gamesir_super_nova_mavi",
        name="GameSir Super Nova (Mavi)",
        good_deal_threshold=2500.0,
        urgent_threshold=2000.0,
        min_valid_price=1000.0,
        subject_good_deal="[İYİ İNDİRİM - 2500 TL VE ALTI!] GameSir Super Nova (Mavi)",
        subject_urgent="[ACİL - 2000 TL VE ALTI!] GameSir Super Nova (Mavi)",
        akakce_urls=[
            "https://www.akakce.com/oyun-kolu/en-ucuz-gamesir-super-nova-kablolu-kablosuz-fiyati,988280688.html",
        ],
        extra_excluded_keywords=[
            "pembe",
            "pink",
            "stand",
            "kapak",
            "faceplate",
            "kablo",
            "şarj istasyonu",
            "sarj istasyonu",
        ],
        popular_stores_only=True,
    ),
    ProductConfig(
        product_id="airpods_pro_3",
        name="Apple AirPods Pro 3",
        good_deal_threshold=11500.0,
        urgent_threshold=10500.0,
        min_valid_price=6000.0,
        subject_good_deal="[İYİ İNDİRİM - 11.500 TL VE ALTI!] Apple AirPods Pro 3",
        subject_urgent="[ACİL - 10.500 TL VE ALTI!] Apple AirPods Pro 3",
        akakce_search_url="https://www.akakce.com/arama/?q=airpods+pro+3",
        akakce_href_token="airpods-pro-3",
        akakce_name_token="airpods pro 3",
        vatan_search_url="https://www.vatanbilgisayar.com/arama/airpods%20pro%203/",
        extra_excluded_keywords=[
            "tek kulaklık",
            "tek kulaklik",
            "tek sağ",
            "tek sol",
            "sağ kulaklık",
            "sol kulaklık",
            "şarj kutusu",
            "sarj kutusu",
            "kulaklık ucu",
            "yedek",
            "2. nesil",
            "2.nesil",
        ],
        popular_stores_only=True,
    ),
]


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


def is_excluded(text: str, extra_keywords: Optional[list[str]] = None) -> Optional[str]:
    """Metinde yasaklı kelime varsa eşleşen kelimeyi döndürür."""
    norm = normalize_turkish(text)
    keywords = COMMON_EXCLUDED_KEYWORDS + (extra_keywords or [])
    for kw in keywords:
        if normalize_turkish(kw) in norm:
            return kw
    return None


def is_popular_store(seller: str) -> bool:
    """Satıcı/mağaza adının popüler ve güvenilir siteler listesinde olup olmadığını kontrol eder."""
    norm = normalize_turkish(seller)
    return any(store in norm for store in POPULAR_STORES)


def parse_tr_price(raw: str) -> Optional[float]:
    """
    Türkçe fiyat metnini float'a çevirir.
    Örnekler: '8.499,00 TL' -> 8499.0, '3.029,37 TL' -> 3029.37, '8.489' -> 8489.0
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


def _matches_product_search(product: ProductConfig, text: str, href: str) -> bool:
    combined = normalize_turkish(f"{text} {href.replace('-', ' ')}")
    if product.akakce_name_token and normalize_turkish(product.akakce_name_token) not in combined:
        return False
    return is_excluded(combined, product.extra_excluded_keywords) is None


def discover_akakce_product_urls(
    search_html: str,
    product: ProductConfig,
    max_urls: int = 8,
) -> list[str]:
    """Akakçe arama sayfasından ilgili ürün/renk sayfalarını bulur."""
    soup = BeautifulSoup(search_html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if product.akakce_href_token and product.akakce_href_token not in href.lower():
            continue
        if not re.search(r",\d+\.html$", href):
            continue
        text = a.get_text(" ", strip=True) or a.get("title", "")
        if not _matches_product_search(product, text, href):
            continue
        full_url = urllib.parse.urljoin("https://www.akakce.com", href.split("#")[0])
        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)
            if len(urls) >= max_urls:
                break

    return urls


def discover_vatan_product_urls(
    search_html: str,
    product: ProductConfig,
    max_urls: int = 8,
) -> list[str]:
    """Vatan Bilgisayar arama sayfasından ilgili ürün/renk sayfalarını bulur."""
    soup = BeautifulSoup(search_html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or not href.lower().endswith(".html"):
            continue
        if product.akakce_href_token and product.akakce_href_token not in href.lower():
            continue
        text = a.get_text(" ", strip=True) or a.get("title", "")
        if not _matches_product_search(product, text, href):
            continue
        full_url = urllib.parse.urljoin("https://www.vatanbilgisayar.com", href.split("#")[0])
        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)
            if len(urls) >= max_urls:
                break

    return urls


def parse_akakce(
    page_html: str,
    url: str,
    product: ProductConfig,
) -> tuple[Optional[Offer], int, int]:
    """
    Akakçe satıcı listesini (#PL li) tarar, yasaklı ilanları ve (etkinse)
    bilinmedik siteleri eler, en ucuz geçerli teklifi döndürür.
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

        if price < product.min_valid_price:
            filtered_count += 1
            print(
                f"  [FİLTRE - Min Fiyat] {price:.2f} TL < {product.min_valid_price:.0f} TL "
                f"(muhtemelen aksesuar/hatalı ilan)"
            )
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
        matched_kw = is_excluded(check_text, product.extra_excluded_keywords)
        if matched_kw:
            filtered_count += 1
            print(f"  [FİLTRE - Akakçe] '{matched_kw}' nedeniyle elendi: {title} ({price:.2f} TL)")
            continue

        if product.popular_stores_only and not is_popular_store(seller):
            filtered_count += 1
            print(f"  [FİLTRE - Bilinmedik Site] '{seller}' elendi: {title} ({price:.2f} TL)")
            continue

        valid_offers.append(
            Offer(
                source="Akakçe",
                seller=seller,
                title=title or product.name,
                price=price,
                shipping=shipping or "Belirtilmemiş",
                url=url,
            )
        )

    if not valid_offers:
        return None, 0, filtered_count

    best = min(valid_offers, key=lambda o: o.price)
    return best, len(valid_offers), filtered_count


def parse_vatan(
    page_html: str,
    url: str,
    product: ProductConfig,
) -> Optional[Offer]:
    """Vatan Bilgisayar ürün sayfasından JSON-LD (veya DOM fallback) ile fiyat okur."""
    soup = BeautifulSoup(page_html, "html.parser")

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

        matched_kw = is_excluded(name, product.extra_excluded_keywords)
        if matched_kw:
            print(f"  [FİLTRE - Vatan] '{matched_kw}' nedeniyle elendi: {name}")
            return None

        price = parse_tr_price(str(offers.get("price", "")))
        if price is not None and price >= product.min_valid_price:
            return Offer(
                source="Vatan Bilgisayar",
                seller="Vatan Bilgisayar",
                title=name or product.name,
                price=price,
                shipping="Ücretsiz kargo",
                url=url,
            )

    h1 = soup.select_one("h1")
    title = h1.get_text(" ", strip=True) if h1 else product.name
    matched_kw = is_excluded(title, product.extra_excluded_keywords)
    if matched_kw:
        print(f"  [FİLTRE - Vatan] '{matched_kw}' nedeniyle elendi: {title}")
        return None

    price_el = soup.select_one(".product-list__price, .product-detail__price")
    if price_el:
        price = parse_tr_price(price_el.get_text(" ", strip=True))
        if price is not None and price >= product.min_valid_price:
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
        return {"products": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"products": {}}

    # Eski tek-ürün formatından yeni çoklu-ürün formatına geçiş uyumluluğu
    if "products" not in data:
        migrated = {"products": {}}
        if data.get("last_notified_price") is not None:
            migrated["products"]["freebuds_pro_5"] = data
        return migrated
    return data


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def determine_alert(product: ProductConfig, price: float) -> Optional[tuple[str, str]]:
    """Fiyat eşik altındaysa (tier, subject) döndürür."""
    if price <= product.urgent_threshold:
        return f"urgent_{int(product.urgent_threshold)}", product.subject_urgent
    if price <= product.good_deal_threshold:
        return f"good_{int(product.good_deal_threshold)}", product.subject_good_deal
    return None


def send_email_alert(
    product: ProductConfig,
    subject: str,
    best: Offer,
    all_offers: list[Offer],
    prev_price: Optional[float],
    is_test: bool = False,
) -> bool:
    gmail_user = os.environ.get("GMAIL_USER", "").strip()
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    gmail_to = os.environ.get("GMAIL_TO", "").strip() or gmail_user

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    prev_text = f"{prev_price:,.2f} TL" if prev_price is not None else "Yok (ilk kez eşik altı)"

    header_line = (
        f"Bu bir TEST e-postasıdır ({product.name}). Takip sistemi sorunsuz çalışıyor! "
        "(last_price.json güncellenmedi.)"
        if is_test
        else f"{product.name} için hedef fiyat uyarısı!"
    )

    lines = [
        header_line,
        "",
        f"Ürün                  : {product.name}",
        f"Hedef Eşik            : {product.good_deal_threshold:,.0f} TL ve altı (Acil: {product.urgent_threshold:,.0f} TL)",
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

    # 1) GitHub Issue bildirimi (GitHub otomatik mail atar)
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
                "User-Agent": "price-tracker-bot",
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

    # 2) Opsiyonel Gmail SMTP bildirimi
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


def check_product(
    product: ProductConfig,
    state: dict,
    test_mail_mode: bool,
) -> tuple[bool, bool]:
    """
    Tek bir ürünü tarar.
    Döndürür: (en_az_bir_teklif_bulundu_mu, state_guncellendi_mi)
    """
    print("=" * 60)
    print(f"{product.name} Fiyat Kontrolü Başlatılıyor...")
    if product.popular_stores_only:
        print("[FİLTRE] Yalnızca popüler ve güvenilir siteler kabul edilecek.")
    print("=" * 60)

    offers: list[Offer] = []

    # 1. Akakçe
    akakce_urls: list[str] = []
    if product.akakce_search_url:
        try:
            search_html = fetch_html(product.akakce_search_url)
            akakce_urls = discover_akakce_product_urls(search_html, product)
            print(f"[Akakçe Arama] {len(akakce_urls)} ürün/renk sayfası bulundu.")
        except Exception as exc:
            print(f"[UYARI - Akakçe Arama] {exc}", file=sys.stderr)

    for url in product.akakce_urls:
        if url not in akakce_urls:
            akakce_urls.append(url)

    for url in akakce_urls:
        try:
            akakce_html = fetch_html(url)
            akakce_offer, valid_cnt, filtered_cnt = parse_akakce(akakce_html, url, product)
            if akakce_offer:
                offers.append(akakce_offer)
                print(
                    f"[Akakçe] {akakce_offer.title}: {akakce_offer.price:,.2f} TL "
                    f"| Satıcı: {akakce_offer.seller} | Kargo: {akakce_offer.shipping} "
                    f"(Geçerli: {valid_cnt}, Elenen: {filtered_cnt})"
                )
            else:
                print(f"[Akakçe] Geçerli ilan bulunamadı ({url}, Elenen: {filtered_cnt}).")
        except Exception as exc:
            print(f"[HATA - Akakçe] ({url}) {exc}", file=sys.stderr)

    # 2. Vatan Bilgisayar (tanımlıysa)
    vatan_urls: list[str] = []
    if product.vatan_search_url:
        try:
            vatan_search_html = fetch_html(product.vatan_search_url)
            vatan_urls = discover_vatan_product_urls(vatan_search_html, product)
            print(f"[Vatan Arama] {len(vatan_urls)} ürün/renk sayfası bulundu.")
        except Exception as exc:
            print(f"[UYARI - Vatan Arama] {exc}", file=sys.stderr)

    for url in product.vatan_urls:
        if url not in vatan_urls:
            vatan_urls.append(url)

    for url in vatan_urls:
        try:
            vatan_html = fetch_html(url)
            vatan_offer = parse_vatan(vatan_html, url, product)
            if vatan_offer:
                offers.append(vatan_offer)
                print(
                    f"[Vatan]  Güncel fiyat: {vatan_offer.price:,.2f} TL "
                    f"| Ürün: {vatan_offer.title}"
                )
            else:
                print(f"[Vatan]  Geçerli veya stokta ürün bulunamadı ({url}).")
        except Exception as exc:
            print(f"[HATA - Vatan] ({url}) {exc}", file=sys.stderr)

    if not offers:
        print(f"[UYARI] {product.name} için geçerli fiyat çekilemedi.", file=sys.stderr)
        return False, False

    best_offer = min(offers, key=lambda o: o.price)
    print("-" * 60)
    print(
        f"[{product.name}] EN UCUZ: {best_offer.price:,.2f} TL "
        f"({best_offer.source} - {best_offer.seller})"
    )

    product_state = state.setdefault("products", {}).get(product.product_id, {})
    last_notified_price = product_state.get("last_notified_price")

    if test_mail_mode:
        test_subject = f"[TEST] {product.name} Takip Sistemi Çalışıyor"
        print(f"[TEST MAİLİ] Gönderiliyor -> {test_subject} ({best_offer.price:,.2f} TL)")
        send_email_alert(
            product,
            test_subject,
            best_offer,
            offers,
            last_notified_price,
            is_test=True,
        )
        return True, False

    alert = determine_alert(product, best_offer.price)
    if alert is None:
        print(
            f"[BİLGİ] {product.name} fiyatı ({best_offer.price:,.2f} TL) alarm eşiğinin "
            f"({product.good_deal_threshold:,.0f} TL) üzerinde. Mail gönderilmeyecek."
        )
        return True, False

    tier, subject = alert
    should_notify = (
        last_notified_price is None or best_offer.price < float(last_notified_price)
    )

    if not should_notify:
        print(
            f"[SPAM KORUMASI] {product.name} eşik altında ({best_offer.price:,.2f} TL) ancak "
            f"son bildirilen dipten ({float(last_notified_price):,.2f} TL) daha düşük değil."
        )
        return True, False

    print(f"[ALARM] Tetiklendi -> {subject} ({best_offer.price:,.2f} TL)")
    sent = send_email_alert(product, subject, best_offer, offers, last_notified_price)
    if sent:
        state["products"][product.product_id] = {
            "name": product.name,
            "last_notified_price": best_offer.price,
            "last_notified_source": best_offer.source,
            "last_notified_seller": best_offer.seller,
            "last_notified_url": best_offer.url,
            "last_notified_tier": tier,
            "last_notified_at": datetime.now(timezone.utc).isoformat(),
            "latest_prices": {
                f"{o.source} | {o.title}": {
                    "price": o.price,
                    "seller": o.seller,
                    "title": o.title,
                    "url": o.url,
                }
                for o in offers
            },
        }
        return True, True
    return True, False


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="7/24 Çoklu Ürün Fiyat Takip ve Bildirim Scripti."
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

    if test_mail_mode:
        print("[MOD] Test maili modu aktif (last_price.json değiştirilmeyecek).")

    state = load_state()
    any_found = False
    state_changed = False

    for product in PRODUCTS:
        found, changed = check_product(product, state, test_mail_mode)
        any_found = any_found or found
        state_changed = state_changed or changed

    if state_changed and not test_mail_mode:
        save_state(state)
        print(f"[KAYDEDİLDİ] {STATE_FILE.name} güncellendi.")

    if not any_found:
        print("[HATA] Hiçbir ürün için geçerli fiyat çekilemedi.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
