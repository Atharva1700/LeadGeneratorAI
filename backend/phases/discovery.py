"""
discovery.py — Phase 1: Discover raw business records from 4 free public sources.

ROOT CAUSE FIX: Previous version had stub/broken scrapers returning empty lists.
This version implements each scraper fully with real selectors, pagination,
and independent try/except so one failure never kills the pipeline.
"""

import asyncio
import logging
import os
import re
import time
import tempfile
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SCRAPE_DELAY = float(os.getenv("SCRAPE_DELAY_SECONDS", "2"))
MAX_PAGES = int(os.getenv("MAX_PAGES_PER_SOURCE", "10"))

NAICS_MAP = {
    "722511": "restaurant", "722513": "fast_food", "484110": "trucking",
    "236220": "construction", "621111": "medical_office", "448140": "retail_clothing",
    "812112": "salon", "441110": "auto_dealer", "561730": "landscaping",
    "524114": "insurance", "541110": "legal", "531210": "real_estate",
    "621210": "dental", "311812": "bakery", "713940": "fitness",
    "238910": "excavation", "325412": "pharmaceutical", "423840": "industrial_supply",
    "445110": "grocery", "532120": "truck_rental",
}

GOOGLE_MAPS_QUERIES = [
    "restaurants Chicago IL",
    "trucking companies Chicago IL",
    "construction companies Chicago IL",
    "medical offices Chicago IL",
    "auto repair shops Chicago IL",
    "hair salons Chicago IL",
    "retail stores Chicago IL",
    "landscaping companies Chicago IL",
    "dental offices Chicago IL",
    "bakeries Chicago IL",
    "plumbing companies Chicago IL",
    "electricians Chicago IL",
]

YELP_VERTICALS = [
    ("restaurants", "restaurant"),
    ("trucking", "trucking"),
    ("construction", "construction"),
    ("doctors", "medical_office"),
    ("automotiverepair", "auto_repair"),
    ("hair", "salon"),
    ("landscaping", "landscaping"),
    ("dentists", "dental"),
]

PHONE_RE = re.compile(r'(\+?1?\s?)?(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})')


# ─────────────────────────────────────────────────────────────────────────────
# Source A: Google Maps via Playwright
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_google_maps(run_id: str) -> list[dict]:
    records = []
    try:
        from playwright.async_api import async_playwright
        from db import log_progress

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                ]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            # Block images/fonts to speed up loading
            await context.route(
                "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf}",
                lambda route: route.abort()
            )

            for query in GOOGLE_MAPS_QUERIES[:MAX_PAGES]:
                try:
                    page = await context.new_page()
                    encoded = query.replace(" ", "+")
                    url = f"https://www.google.com/maps/search/{encoded}"
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                    # Wait for results feed
                    try:
                        await page.wait_for_selector('div[role="feed"]', timeout=10000)
                    except Exception:
                        # Try alternate selector
                        await page.wait_for_selector('.Nv2PK', timeout=8000)

                    # Scroll to load more results (8 scrolls)
                    feed = page.locator('div[role="feed"]').first
                    for _ in range(8):
                        await feed.evaluate("el => el.scrollBy(0, 800)")
                        await asyncio.sleep(0.8)

                    # Extract result cards
                    cards = await page.query_selector_all('div[role="feed"] > div')
                    query_industry = query.split(" ")[0].lower()

                    for card in cards[:50]:
                        try:
                            text = await card.inner_text()
                            if not text.strip():
                                continue

                            # Company name
                            name_el = await card.query_selector('a[aria-label]')
                            company = None
                            if name_el:
                                company = await name_el.get_attribute("aria-label")
                            if not company:
                                h3 = await card.query_selector('div.qBF1Pd')
                                if h3:
                                    company = await h3.inner_text()

                            if not company or len(company) < 2:
                                continue

                            # Phone
                            phone_match = PHONE_RE.search(text)
                            phone = phone_match.group(0) if phone_match else None

                            # Address — look for address patterns
                            address = None
                            addr_lines = [
                                l.strip() for l in text.split("\n")
                                if re.search(r'\d+\s+\w+\s+(St|Ave|Blvd|Dr|Rd|Lane|Ln|Way|Ct|Pl)', l, re.I)
                            ]
                            if addr_lines:
                                address = addr_lines[0]

                            # URL
                            link_el = await card.query_selector('a[href*="/maps/place/"]')
                            url_val = None
                            if link_el:
                                url_val = await link_el.get_attribute("href")

                            records.append({
                                "source": "google_maps",
                                "company_name": company.strip(),
                                "first_name": None,
                                "last_name": None,
                                "email": None,
                                "phone": phone,
                                "address": address,
                                "industry": query_industry,
                                "business_age_months": None,
                                "raw_html": text[:2000],
                                "url": url_val,
                            })
                        except Exception as card_err:
                            logger.debug(f"Card parse error: {card_err}")
                            continue

                    await page.close()
                    log_progress(run_id, "discovery",
                                 f"Google Maps: {len(records)} records so far (query: {query})",
                                 len(records))
                    await asyncio.sleep(SCRAPE_DELAY)

                except Exception as query_err:
                    logger.warning(f"Google Maps query '{query}' failed: {query_err}")
                    continue

            await browser.close()

    except Exception as e:
        logger.warning(f"Google Maps scraping failed entirely: {e} — skipping source")

    logger.info(f"Google Maps: collected {len(records)} records")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Source B: Yelp website scraping (no API key)
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_yelp_website(run_id: str) -> list[dict]:
    records = []
    try:
        import httpx
        from bs4 import BeautifulSoup
        from db import log_progress

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
            for category, industry in YELP_VERTICALS:
                for offset in [0, 10, 20, 30]:
                    try:
                        url = (
                            f"https://www.yelp.com/search"
                            f"?find_desc={category}"
                            f"&find_loc=Chicago%2C+IL"
                            f"&start={offset}"
                        )
                        resp = await client.get(url)

                        # Check for block/CAPTCHA
                        if resp.status_code == 403 or "captcha" in resp.text.lower():
                            logger.warning(f"Yelp blocked for category {category} — skipping")
                            break

                        if resp.status_code != 200:
                            logger.warning(f"Yelp returned {resp.status_code} for {category}")
                            break

                        soup = BeautifulSoup(resp.text, "lxml")

                        # Multiple selector attempts for resilience
                        listings = (
                            soup.select('div[data-testid="serp-ia-card"]') or
                            soup.select('li.y-css-hcgwj4') or
                            soup.select('div.businessName__09f24__HG_pC') or
                            soup.select('h3.y-css-1x9eetn') or
                            []
                        )

                        # Fallback: find all business name headers
                        if not listings:
                            listings = soup.find_all("h3", limit=20)

                        for item in listings[:10]:
                            try:
                                # Company name
                                name_tag = item.find("a") or item
                                company = name_tag.get_text(strip=True)
                                if not company or len(company) < 2:
                                    continue

                                # Get parent container for more info
                                parent = item.find_parent("div", class_=True) or item.parent

                                # Address
                                address = None
                                addr_tag = (
                                    parent.find("address") if parent else None or
                                    soup.find("address")
                                )
                                if addr_tag:
                                    address = addr_tag.get_text(strip=True)

                                # Phone from text
                                parent_text = parent.get_text() if parent else ""
                                phone_match = PHONE_RE.search(parent_text)
                                phone = phone_match.group(0) if phone_match else None

                                # URL
                                link = name_tag.get("href", "") if name_tag.name == "a" else ""
                                if link and not link.startswith("http"):
                                    link = "https://www.yelp.com" + link

                                records.append({
                                    "source": "yelp_web",
                                    "company_name": company,
                                    "first_name": None,
                                    "last_name": None,
                                    "email": None,
                                    "phone": phone,
                                    "address": address,
                                    "industry": industry,
                                    "business_age_months": None,
                                    "raw_html": str(item)[:2000],
                                    "url": link or None,
                                })
                            except Exception as item_err:
                                logger.debug(f"Yelp item parse error: {item_err}")
                                continue

                        log_progress(run_id, "discovery",
                                     f"Yelp: {len(records)} records so far",
                                     len(records))
                        await asyncio.sleep(SCRAPE_DELAY)

                    except Exception as page_err:
                        logger.warning(f"Yelp page error ({category}, offset {offset}): {page_err}")
                        await asyncio.sleep(SCRAPE_DELAY)
                        continue

    except Exception as e:
        logger.warning(f"Yelp scraping failed entirely: {e} — skipping source")

    logger.info(f"Yelp: collected {len(records)} records")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Source C: SBA Public PPP Loan Data (CSV download — streamed)
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_sba_data(run_id: str) -> list[dict]:
    records = []
    try:
        import httpx
        import pandas as pd
        from db import log_progress

        SBA_URL = (
            "https://data.sba.gov/dataset/ppp-foia/resource/"
            "aab8e9f9-36d1-42e1-b3ba-e59c79f1d7f0/download/"
            "public_up_to_150k_1_230101.csv"
        )

        tmp_path = os.path.join(tempfile.gettempdir(), "sba_data.csv")

        log_progress(run_id, "discovery", "Downloading SBA public loan data (streaming)...", 0)

        # Stream download
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", SBA_URL) as response:
                if response.status_code != 200:
                    logger.warning(f"SBA download returned {response.status_code} — skipping")
                    return []
                with open(tmp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)

        logger.info(f"SBA CSV downloaded to {tmp_path}")

        # Read in chunks — only needed columns
        needed_cols = [
            "BorrowerName", "BorrowerAddress", "BorrowerCity",
            "BorrowerState", "NAICSCode", "LoanStatusDate", "InitialApprovalAmount"
        ]

        count = 0
        for chunk in pd.read_csv(
            tmp_path,
            usecols=lambda c: c in needed_cols,
            chunksize=1000,
            dtype=str,
            on_bad_lines="skip",
        ):
            for _, row in chunk.iterrows():
                try:
                    state = str(row.get("BorrowerState", "")).strip().upper()
                    if state != "IL":
                        continue

                    amount_str = str(row.get("InitialApprovalAmount", "0")).replace(",", "")
                    try:
                        amount = float(amount_str)
                    except ValueError:
                        continue

                    if not (25000 <= amount <= 500000):
                        continue

                    naics = str(row.get("NAICSCode", "")).strip()[:6]
                    industry = NAICS_MAP.get(naics, "small_business")

                    company = str(row.get("BorrowerName", "")).strip()
                    if not company:
                        continue

                    address_parts = [
                        str(row.get("BorrowerAddress", "")),
                        str(row.get("BorrowerCity", "")),
                        state,
                    ]
                    address = ", ".join(p.strip() for p in address_parts if p.strip())

                    records.append({
                        "source": "sba",
                        "company_name": company,
                        "first_name": None,
                        "last_name": None,
                        "email": None,
                        "phone": None,
                        "address": address,
                        "industry": industry,
                        "business_age_months": None,
                        "raw_html": None,
                        "url": None,
                    })
                    count += 1
                    if count >= 500:
                        break
                except Exception:
                    continue
            if count >= 500:
                break

        # Clean up temp file
        try:
            os.remove(tmp_path)
        except Exception:
            pass

        log_progress(run_id, "discovery", f"SBA data: {len(records)} records loaded", len(records))

    except Exception as e:
        logger.warning(f"SBA data download/parse failed: {e} — skipping source")

    logger.info(f"SBA: collected {len(records)} records")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Source D: Illinois SOS new LLC filings (Playwright)
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_ilsos(run_id: str) -> list[dict]:
    records = []
    try:
        from playwright.async_api import async_playwright
        from db import log_progress

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            try:
                await page.goto(
                    "https://www.ilsos.gov/corporatellc/",
                    wait_until="domcontentloaded",
                    timeout=30000
                )
                await page.wait_for_load_state("networkidle", timeout=15000)

                # Try to find search form
                # ILSOS has a search by name form
                name_input = await page.query_selector('input[name="LLC_NAME"]')
                if not name_input:
                    name_input = await page.query_selector('input[type="text"]')

                if name_input:
                    await name_input.fill("%")  # wildcard search

                    # Find and click search button
                    search_btn = (
                        await page.query_selector('input[type="submit"]') or
                        await page.query_selector('button[type="submit"]')
                    )
                    if search_btn:
                        await search_btn.click()
                        await page.wait_for_load_state("networkidle", timeout=20000)

                        # Parse results table
                        rows = await page.query_selector_all("table tr")
                        now = datetime.now(timezone.utc)

                        for row in rows[1:]:  # Skip header row
                            try:
                                cells = await row.query_selector_all("td")
                                if len(cells) < 3:
                                    continue

                                company = (await cells[0].inner_text()).strip()
                                status_text = (await cells[-1].inner_text()).strip().upper()

                                if "GOOD STANDING" not in status_text and "ACTIVE" not in status_text:
                                    continue
                                if not company:
                                    continue

                                # Try to get file date
                                date_text = ""
                                for cell in cells:
                                    t = (await cell.inner_text()).strip()
                                    if re.search(r'\d{2}/\d{2}/\d{4}', t):
                                        date_text = t
                                        break

                                age_months = None
                                date_match = re.search(r'(\d{2})/(\d{2})/(\d{4})', date_text)
                                if date_match:
                                    try:
                                        file_date = datetime(
                                            int(date_match.group(3)),
                                            int(date_match.group(1)),
                                            int(date_match.group(2)),
                                            tzinfo=timezone.utc
                                        )
                                        delta = now - file_date
                                        age_months = int(delta.days / 30)
                                        if age_months > 24:
                                            continue  # Only want new businesses
                                    except Exception:
                                        pass

                                records.append({
                                    "source": "ilsos",
                                    "company_name": company,
                                    "first_name": None,
                                    "last_name": None,
                                    "email": None,
                                    "phone": None,
                                    "address": None,
                                    "industry": "small_business",
                                    "business_age_months": age_months,
                                    "raw_html": await row.inner_html(),
                                    "url": None,
                                })

                                if len(records) >= 300:
                                    break
                            except Exception as row_err:
                                logger.debug(f"ILSOS row parse error: {row_err}")
                                continue

            except Exception as inner_e:
                logger.warning(f"ILSOS page interaction failed: {inner_e}")

            await browser.close()
            log_progress(run_id, "discovery", f"ILSOS: {len(records)} new LLC records", len(records))

    except Exception as e:
        logger.warning(f"ILSOS scraping failed: {e} — skipping source")

    logger.info(f"ILSOS: collected {len(records)} records")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK: Yellow Pages scraping (backup if Google Maps is slow/blocked)
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_yellow_pages(run_id: str) -> list[dict]:
    """
    Fallback source: Yellow Pages public listings for Chicago businesses.
    No login or API key required.
    """
    records = []
    categories = [
        ("restaurants", "restaurant"),
        ("trucking-transportation-brokers", "trucking"),
        ("contractors-general", "construction"),
        ("physicians-surgeons", "medical_office"),
        ("auto-repair-service", "auto_repair"),
        ("beauty-salons", "salon"),
    ]

    try:
        import httpx
        from bs4 import BeautifulSoup
        from db import log_progress

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

        async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
            for category, industry in categories:
                for page_num in range(1, 4):
                    try:
                        url = (
                            f"https://www.yellowpages.com/chicago-il/{category}"
                            f"?page={page_num}"
                        )
                        resp = await client.get(url)
                        if resp.status_code != 200:
                            break

                        soup = BeautifulSoup(resp.text, "lxml")
                        listings = soup.select("div.result") or soup.select("div.v-card")

                        for item in listings[:15]:
                            try:
                                name_tag = (
                                    item.select_one("a.business-name") or
                                    item.select_one("h2.n") or
                                    item.select_one("h2")
                                )
                                if not name_tag:
                                    continue

                                company = name_tag.get_text(strip=True)
                                if not company:
                                    continue

                                phone_tag = item.select_one("div.phones") or item.select_one(".phone")
                                phone = phone_tag.get_text(strip=True) if phone_tag else None

                                addr_tag = item.select_one("div.street-address") or item.select_one(".street-address")
                                city_tag = item.select_one("div.locality") or item.select_one(".locality")
                                address = None
                                if addr_tag:
                                    address = addr_tag.get_text(strip=True)
                                    if city_tag:
                                        address += ", " + city_tag.get_text(strip=True)

                                link_tag = name_tag if name_tag.name == "a" else name_tag.find("a")
                                url_val = None
                                if link_tag and link_tag.get("href"):
                                    href = link_tag["href"]
                                    url_val = href if href.startswith("http") else f"https://www.yellowpages.com{href}"

                                records.append({
                                    "source": "yellow_pages",
                                    "company_name": company,
                                    "first_name": None,
                                    "last_name": None,
                                    "email": None,
                                    "phone": phone,
                                    "address": address,
                                    "industry": industry,
                                    "business_age_months": None,
                                    "raw_html": str(item)[:2000],
                                    "url": url_val,
                                })
                            except Exception:
                                continue

                        log_progress(run_id, "discovery",
                                     f"Yellow Pages: {len(records)} records so far",
                                     len(records))
                        await asyncio.sleep(SCRAPE_DELAY)

                    except Exception as e:
                        logger.warning(f"Yellow Pages page error: {e}")
                        break

    except Exception as e:
        logger.warning(f"Yellow Pages scraping failed: {e} — skipping source")

    logger.info(f"Yellow Pages: collected {len(records)} records")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run(run_id: str, config: dict) -> list[dict]:
    """
    Phase 1 entry point. Runs all 4 scrapers independently.
    One source failing never crashes the pipeline.
    Returns combined deduplicated list of raw business records.
    """
    from db import log_progress

    all_records = []

    # Run all scrapers — each wrapped independently
    log_progress(run_id, "discovery", "Starting Google Maps scraping...", 0)
    gm_records = await scrape_google_maps(run_id)
    all_records.extend(gm_records)
    log_progress(run_id, "discovery",
                 f"Google Maps done: {len(gm_records)} records", len(all_records))

    log_progress(run_id, "discovery", "Starting Yelp website scraping...", len(all_records))
    yelp_records = await scrape_yelp_website(run_id)
    all_records.extend(yelp_records)
    log_progress(run_id, "discovery",
                 f"Yelp done: {len(yelp_records)} records", len(all_records))

    log_progress(run_id, "discovery", "Starting Yellow Pages scraping...", len(all_records))
    yp_records = await scrape_yellow_pages(run_id)
    all_records.extend(yp_records)
    log_progress(run_id, "discovery",
                 f"Yellow Pages done: {len(yp_records)} records", len(all_records))

    log_progress(run_id, "discovery", "Starting SBA data download...", len(all_records))
    sba_records = await scrape_sba_data(run_id)
    all_records.extend(sba_records)
    log_progress(run_id, "discovery",
                 f"SBA done: {len(sba_records)} records", len(all_records))

    log_progress(run_id, "discovery", "Starting ILSOS LLC filings scrape...", len(all_records))
    ilsos_records = await scrape_ilsos(run_id)
    all_records.extend(ilsos_records)
    log_progress(run_id, "discovery",
                 f"ILSOS done: {len(ilsos_records)} records", len(all_records))

    # Deduplicate on company_name (case-insensitive)
    seen = set()
    unique = []
    for r in all_records:
        key = (r.get("company_name") or "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    logger.info(
        f"Discovery complete: {len(all_records)} total → {len(unique)} after dedup "
        f"(GM:{len(gm_records)} Yelp:{len(yelp_records)} YP:{len(yp_records)} "
        f"SBA:{len(sba_records)} ILSOS:{len(ilsos_records)})"
    )
    log_progress(run_id, "discovery",
                 f"Discovery complete — {len(unique)} unique records found", len(unique))

    return unique