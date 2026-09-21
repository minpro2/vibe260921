"""네이버 뉴스 수집기 - requests + BeautifulSoup4

두 가지 방식을 지원한다.

  A) 네이버 검색 API (--api, 권장)   공식 API 라서 robots.txt/약관 문제가 없다.
                                    제목 · 요약 · 발행 시각 · 기사 링크를 받는다. (기사 본문 전체는 제공되지 않는다)
  B) 검색 결과 페이지 수집 (기본)    검색 결과 HTML 에서 네이버 뉴스 기사를 찾아 본문 전체를 뽑는다.
                                    robots.txt 가 금지하므로 --ignore-robots 로 직접 책임질 때에만 동작한다.

사용법
    python news_crawler_gui.py                           # 화면(GUI)으로 사용 - PyQt6 필요
    python news_crawler.py --api                         # 검색어 '반도체' 최신 뉴스 5건 (API)
    python news_crawler.py --api --query 인공지능 --limit 20 --sort sim --out ai.xlsx   # 엑셀로 저장 (.csv/.json 도 가능)
    python news_crawler.py --ignore-robots               # (B) 기본 URL 의 기사 본문 5건
    python news_crawler.py --url "<다른 네이버 검색 URL>" --ignore-robots --out news.csv

설치
    pip install requests beautifulsoup4            # 명령줄
    pip install PyQt6                              # 화면(GUI)까지 쓸 때
    pip install openpyxl                           # 엑셀(.xlsx) 파일로 저장할 때

API 인증 정보 준비 (A 방식)
    1) https://developers.naver.com/apps 에서 애플리케이션을 등록하고 사용 API 에 '검색'을 선택한다.
    2) 발급된 Client ID / Client Secret 을 환경 변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 로 설정한다.
       - 이 파일 옆의 .env 파일에 적어도 된다 (.env.example 을 복사해서 사용, .env 는 커밋 금지).
       - VS Code 의 Python 확장도 작업 폴더의 .env 를 실행/디버그 때 자동으로 읽는다.
    호출 한도는 하루 25,000회이고, 이 스크립트는 실행 한 번에 API 를 1회 호출한다.

주의
    - B 방식: search.naver.com 과 n.news.naver.com 의 robots.txt 는 모든 자동 수집기를 금지한다(Disallow: /).
      그래서 먼저 robots.txt 를 확인하고 금지되어 있으면 멈춘다. 개인 학습용으로 소량만 받겠다고 판단해
      직접 책임질 때에만 --ignore-robots 를 붙인다. 차단(403/429) 응답이 오면 우회하지 않고 바로 멈춘다.
    - 기사 저작권은 언론사에 있다. 수집한 내용을 재배포하거나 서비스에 쓰면 안 된다.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from email.utils import parsedate_to_datetime
from urllib import robotparser
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- 설정 값
DEFAULT_URL = (
    "https://search.naver.com/search.naver?where=nexearch&sm=top_hty&fbm=0&ie=utf8"
    "&query=%EB%B0%98%EB%8F%84%EC%B2%B4&ackey=5img6uyc"
)
DEFAULT_LIMIT = 5         # 기본 수집 기사 수
MAX_LIMIT = 30            # (B) 한 번에 받을 수 있는 최대 기사 수 (검색 페이지에 나오는 양도 이 정도다)
DEFAULT_DELAY = 2.0       # (B) 요청 사이 대기 시간(초)
MIN_DELAY = 1.0           # (B) --delay 로 이보다 짧게 줄일 수 없다
TIMEOUT = 15              # 요청 하나당 최대 대기(초)
RETRIES = 2               # 연결 오류/서버 오류(5xx)일 때만 다시 시도하는 횟수
BLOCK_CODES = (403, 429)  # (B) 차단으로 보고 즉시 중단하는 응답 코드

USER_AGENT = "NewsCrawlerStudy/1.0 (personal study script; requests)"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BASE_DIR, "news_result.json")

# ---- (A) 검색 API
API_URL = "https://openapi.naver.com/v1/search/news.json"
API_MAX_LIMIT = 100       # 검색 API 가 한 번에 돌려주는 최대 건수(display)
ID_ENV, SECRET_ENV = "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"
API_FIELDS = ("title", "summary", "published", "domain", "link", "originallink")
API_HINTS = {
    401: "Client ID / Client Secret 이 올바른지 확인하세요.",
    403: "애플리케이션의 '사용 API' 에 '검색'이 추가되어 있는지 확인하세요.",
    429: "호출 한도(하루 25,000회)를 넘었거나 짧은 시간에 너무 많이 호출했습니다. 잠시 후 다시 시도하세요.",
}

# ---- (B) 검색 결과 페이지 수집
# 기사 링크 형태. 클래스 이름은 자주 바뀌므로(해시 값) 주소 형태로 찾는다.
ARTICLE_LINK = re.compile(r"^https://n\.news\.naver\.com/mnews/article/\d+/\d+$")
# 본문 영역 (일반 뉴스 -> 연예 -> 스포츠 순의 옛/다른 서식까지 시도)
BODY_SELECTORS = ("#dic_area", "#articeBody", "#newsEndContents", "#articleBodyContents")
# 본문 안에 섞여 있는 것 중 기사 글이 아닌 것 (사진과 사진 설명, 광고)
NOISE_SELECTORS = ("script", "style", "span.end_photo_org", ".img_desc", ".ad_body_rgt")
SUMMARY_SELECTOR = "strong.media_end_summary"    # 기사 맨 위 핵심 요약 (본문의 일부로 남긴다)
FIELDS = ("url", "title", "press", "published", "modified", "reporter", "content")

# ---- 엑셀(.xlsx) 저장
EXCEL_LABELS = {                       # 엑셀 첫 줄에 쓸 한글 열 이름 (JSON/CSV 는 영문 키를 그대로 쓴다)
    "url": "기사 주소", "title": "제목", "press": "언론사", "published": "작성 시각", "modified": "수정 시각",
    "reporter": "기자", "content": "본문", "summary": "요약", "domain": "출처", "link": "네이버 링크",
    "originallink": "원문 링크",
}
EXCEL_WIDTHS = {"title": 50, "summary": 60, "content": 90, "published": 20, "modified": 20, "press": 14,
                "domain": 22, "reporter": 14, "url": 42, "link": 42, "originallink": 42}
EXCEL_WRAP_FIELDS = ("title", "summary", "content")     # 줄바꿈해서 보여 줄 열
EXCEL_LINK_FIELDS = ("url", "link", "originallink")     # 눌러서 열 수 있는 링크 열
EXCEL_CELL_LIMIT = 32000                                # 엑셀 셀 하나에는 32,767자까지만 들어간다


# ---------------------------------------------------------------- 공통: 내려받기
def build_search_url(query):
    """검색어로 네이버 통합검색 URL 을 만든다."""
    return "https://search.naver.com/search.naver?" + urlencode({"where": "nexearch", "ie": "utf8", "query": query})


def nap(seconds, stop=None, step=0.1):
    """seconds 초 쉰다. stop() 이 True 가 되면 바로 깨어나 False 를 돌려준다 (중지 버튼이 곧바로 먹히도록)."""
    waited = 0.0
    while waited < seconds:
        if stop and stop():
            return False
        time.sleep(min(step, seconds - waited))
        waited += step
    return not (stop and stop())


def fetch(session, url, **kwargs):
    """응답 본문 문자열을 돌려준다. 연결 오류/5xx 는 잠깐 쉬었다 다시 시도하고, 4xx 는 바로 예외를 낸다.
    params, headers 같은 추가 인자는 session.get 으로 그대로 넘긴다."""
    last = None
    for attempt in range(RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp.text
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code < 500:
                raise
            last = e
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        if attempt < RETRIES:
            time.sleep(1.5 * (attempt + 1))
    raise last


def save(articles, path, fields=FIELDS):
    """확장자에 따라 저장한다. .xlsx 는 엑셀, .csv 는 CSV(엑셀에서 한글이 깨지지 않도록 utf-8-sig), 그 밖에는 JSON."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        save_xlsx(articles, path, fields)
    elif ext == ".xls":
        raise RuntimeError("옛 엑셀 형식(.xls)은 지원하지 않습니다. 확장자를 .xlsx 로 바꿔 저장하세요.")
    elif ext == ".csv":
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(articles)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)


def save_xlsx(articles, path, fields=FIELDS):
    """엑셀 파일(.xlsx)로 저장한다. 열 이름은 한글, 첫 줄 고정 + 필터, 긴 글은 줄바꿈, 주소는 눌러서 열 수 있다."""
    try:
        from openpyxl import Workbook
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        raise RuntimeError("엑셀(.xlsx) 저장에는 openpyxl 이 필요합니다: pip install openpyxl") from e

    wb = Workbook()
    ws = wb.active
    ws.title = "뉴스"

    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="DCE6F1")
    for col, field in enumerate(fields, 1):
        cell = ws.cell(row=1, column=col, value=EXCEL_LABELS.get(field, field))
        cell.font, cell.fill = header_font, header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(col)].width = EXCEL_WIDTHS.get(field, 20)

    for row, article in enumerate(articles, 2):
        for col, field in enumerate(fields, 1):
            text = ILLEGAL_CHARACTERS_RE.sub("", str(article.get(field, "")))     # 엑셀이 받지 못하는 제어 문자 제거
            if len(text) > EXCEL_CELL_LIMIT:
                text = text[:EXCEL_CELL_LIMIT] + "…(잘림)"
            cell = ws.cell(row=row, column=col, value=text)
            cell.data_type = "s"        # 웹에서 가져온 글이 '=' 로 시작해도 수식으로 실행되지 않게 항상 글자로 저장한다
            if field in EXCEL_LINK_FIELDS and text.startswith(("http://", "https://")) and len(text) <= 2000:
                cell.hyperlink = text
                cell.style = "Hyperlink"
            cell.alignment = Alignment(vertical="top", wrap_text=field in EXCEL_WRAP_FIELDS)

    ws.freeze_panes = "A2"              # 스크롤해도 열 이름이 보이도록 첫 줄 고정
    if articles:
        ws.auto_filter.ref = ws.dimensions
    wb.save(path)


def save_error_text(error):
    """저장 실패 원인을 사용자에게 보여 줄 문장으로 바꾼다."""
    if isinstance(error, PermissionError):
        return f"파일에 쓸 수 없습니다. 엑셀 등에서 같은 파일이 열려 있으면 닫고 다시 저장하세요. ({error})"
    return str(error)


def load_dotenv(path):
    """KEY=VALUE 형식의 .env 파일을 읽어 환경 변수에 채운다. 이미 설정된 값은 덮어쓰지 않는다."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key.strip() and value:
            os.environ.setdefault(key.strip(), value)


# ---------------------------------------------------------------- (A) 네이버 검색 API
def clean_text(fragment):
    """API 응답의 <b>검색어</b> 강조 태그와 &quot; 같은 HTML 엔티티를 없앤 글자만 남긴다."""
    return BeautifulSoup(fragment or "", "html.parser").get_text().strip()


def format_pubdate(value):
    """'Mon, 21 Sep 2026 09:17:00 +0900' -> '2026-09-21 09:17:00'. 해석할 수 없으면 원문을 그대로 쓴다."""
    try:
        return parsedate_to_datetime(value).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return value or ""


def api_error_message(error):
    """HTTPError 응답 본문에서 네이버가 알려 주는 errorMessage 를 꺼낸다."""
    resp = error.response
    if resp is None:
        return str(error)
    try:
        return resp.json().get("errorMessage") or resp.text[:200]
    except ValueError:
        return resp.text[:200]


def api_search(session, query, limit, sort, client_id, client_secret):
    """검색 API 를 한 번 호출해 기사 목록(dict)을 돌려준다."""
    text = fetch(
        session, API_URL,
        params={"query": query, "display": limit, "start": 1, "sort": sort},
        headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
    )
    results = []
    for item in json.loads(text).get("items", []):
        link, original = item.get("link", ""), item.get("originallink", "")
        results.append({
            "title": clean_text(item.get("title")),
            "summary": clean_text(item.get("description")),
            "published": format_pubdate(item.get("pubDate")),
            "domain": urlparse(original or link).netloc,
            "link": link,
            "originallink": original,
        })
    return results


def crawl_api(query, limit, sort, session=None, log=print, stop=None, on_article=None, credentials=None):
    """검색 API 로 뉴스를 받아 dict 목록으로 돌려준다. 시작할 수 없거나 실패하면 None.

    log(text): 진행 상황을 받는 함수(기본 print). stop(): True 를 돌려주면 중단한다.
    on_article(dict): 기사 하나가 준비될 때마다 부르는 함수 (GUI 가 표를 채울 때 쓴다).
    credentials: (Client ID, Client Secret). 생략하면 환경 변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 를 쓴다.
    """
    if credentials is None:
        credentials = (os.environ.get(ID_ENV), os.environ.get(SECRET_ENV))
    client_id, client_secret = credentials
    if not client_id or not client_secret:
        log("[중단] 네이버 검색 API 인증 정보가 없습니다.")
        log("       1) https://developers.naver.com/apps 에서 애플리케이션을 등록하고 사용 API 에 '검색'을 선택합니다.")
        log(f"       2) 발급된 값을 입력하거나(화면), 환경 변수 {ID_ENV}, {SECRET_ENV} 로 설정합니다.")
        log(f"          PowerShell: $env:{ID_ENV}=\"...\" ; $env:{SECRET_ENV}=\"...\"")
        log(f"          또는 {os.path.join(BASE_DIR, '.env')} 파일에 KEY=VALUE 로 적습니다 (.env.example 참고).")
        log("       ※ 비밀 값은 코드나 저장소에 넣지 마세요.")
        return None

    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    log(f"[API] 검색어: {query}  |  최대 {limit}건  |  {'최신순' if sort == 'date' else '정확도순'}")
    try:
        articles = api_search(session, query, limit, sort, client_id, client_secret)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        log(f"[오류] 네이버 API 가 요청을 거절했습니다 (HTTP {code}): {api_error_message(e)}")
        if code in API_HINTS:
            log(f"       {API_HINTS[code]}")
        return None
    except (requests.RequestException, ValueError) as e:
        log(f"[오류] API 요청/응답 처리에 실패했습니다: {e.__class__.__name__}: {e}")
        return None

    if stop and stop():
        log("[중단] 사용자가 중지했습니다.")
        return []
    for i, a in enumerate(articles, 1):
        log(f"[{i}/{len(articles)}] {a['published']} | {a['domain']} | {a['title']}")
        log(f"      {a['summary'][:90]}")
        if on_article:
            on_article(a)
    return articles


# ---------------------------------------------------------------- (B) 검색 결과 페이지 수집: robots.txt
def robots_allows(session, url):
    """robots.txt 가 이 주소의 수집을 허용하는지. 확인할 수 없으면 허용으로 보지 않는다."""
    p = urlparse(url)
    try:
        resp = session.get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=TIMEOUT)
    except requests.RequestException:
        return False
    if resp.status_code in (401, 403) or resp.status_code >= 500:
        return False
    if resp.status_code >= 400:
        return True               # robots.txt 가 없으면 제한이 없는 것으로 본다(표준 해석)
    rp = robotparser.RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp.can_fetch(USER_AGENT, url)


# ---------------------------------------------------------------- (B) 파싱
def extract_article_links(html, limit):
    """검색 결과 HTML 에서 네이버 뉴스 기사 주소를 순서대로(중복 없이) 최대 limit 개 모은다."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        p = urlparse(a["href"])
        clean = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))   # ?sid=101 같은 꼬리표는 뗀다
        if ARTICLE_LINK.match(clean) and clean not in links:
            links.append(clean)
    return links[:limit]


def _meta(soup, prop):
    tag = soup.select_one(f'meta[property="{prop}"]')
    return tag["content"].strip() if tag and tag.get("content") else ""


def parse_article(html, url):
    """기사 페이지 HTML 에서 필요한 값을 뽑아 dict 로 돌려준다. 본문을 못 찾으면 None."""
    soup = BeautifulSoup(html, "html.parser")

    body = next((el for el in (soup.select_one(s) for s in BODY_SELECTORS) if el is not None), None)
    if body is None:
        return None
    for selector in NOISE_SELECTORS:
        for tag in body.select(selector):
            tag.decompose()
    for br in body.find_all("br"):
        br.replace_with("\n")
    for block in body.select(SUMMARY_SELECTOR):
        block.insert_after("\n")       # 요약문 뒤에는 <br> 이 없어서 본문 첫 문장과 붙어 버린다
    lines = (line.strip() for line in body.get_text().splitlines())
    content = "\n".join(line for line in lines if line)

    title_tag = soup.select_one("#title_area")
    title = title_tag.get_text(" ", strip=True) if title_tag else _meta(soup, "og:title")

    author = _meta(soup, "og:article:author")            # 예: "뉴시스 | 네이버"
    press = author.split("|")[0].strip() if author else ""

    published = soup.select_one("span.media_end_head_info_datestamp_time")
    modified = soup.select_one("span._ARTICLE_MODIFY_DATE_TIME")

    reporter_tag = soup.select_one(".byline_s") or soup.select_one("em.media_end_head_journalist_name")
    reporter = ""
    if reporter_tag:
        # "김양수 기자(abc@example.com)" 에서 이메일 부분은 저장하지 않는다
        reporter = re.sub(r"\(.*?\)", "", reporter_tag.get_text(strip=True)).strip()

    return {
        "url": url,
        "title": title,
        "press": press,
        "published": published.get("data-date-time", "") if published else "",
        "modified": modified.get("data-modify-date-time", "") if modified else "",
        "reporter": reporter,
        "content": content,
    }


# ---------------------------------------------------------------- (B) 실행
def crawl(url, limit, delay, ignore_robots=False, session=None, log=print, stop=None, on_article=None):
    """검색 URL 에서 기사를 수집해 dict 목록으로 돌려준다. 시작할 수 없으면 None.

    log / stop / on_article 의 뜻은 crawl_api 와 같다.
    """
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"})

    # 1) robots.txt: 검색 페이지와 기사 페이지 양쪽을 모두 확인한다
    sample_article = "https://n.news.naver.com/mnews/article/000/0000000000"
    blocked = [u for u in (url, sample_article) if not robots_allows(session, u)]
    if blocked:
        hosts = ", ".join(sorted({urlparse(u).netloc for u in blocked}))
        log(f"[중단] robots.txt 가 자동 수집을 금지하고 있습니다: {hosts}")
        if not ignore_robots:
            log("       개인 학습용으로 소량만 받겠다고 판단해 직접 책임질 때에만 다시 실행하세요 (명령줄: --ignore-robots, 화면: '직접 책임' 체크).")
            log("       공식 방식을 쓰려면 네이버 검색 API 를 사용하세요 (명령줄: --api, 화면: API 모드). 제목·요약·링크를 받을 수 있습니다.")
            return None
        log("       직접 책임 옵션이 지정되어 계속합니다. 수집량과 대기 시간 제한은 그대로 적용됩니다.")

    query = parse_qs(urlparse(url).query).get("query", [""])[0]
    log(f"검색어: {query or '(알 수 없음)'}  |  최대 {limit}건  |  요청 간격 {delay:g}초")

    # 2) 검색 결과에서 기사 링크 모으기
    try:
        search_html = fetch(session, url)
    except requests.RequestException as e:
        log(f"[오류] 검색 페이지를 받지 못했습니다: {e}")
        return None
    links = extract_article_links(search_html, limit)
    if not links:
        log("[오류] 네이버 뉴스 기사 링크를 찾지 못했습니다. 페이지 구조가 바뀌었거나 차단 페이지일 수 있습니다.")
        return None
    log(f"기사 링크 {len(links)}개를 찾았습니다.\n")

    # 3) 기사 하나씩 받아서 파싱
    articles = []
    for i, link in enumerate(links, 1):
        if not nap(delay, stop):
            log("[중단] 사용자가 중지했습니다.")
            break
        try:
            article = parse_article(fetch(session, link), link)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code in BLOCK_CODES:
                log(f"[중단] 차단으로 보이는 응답({code})을 받았습니다. 우회하지 않고 여기서 멈춥니다.")
                break
            log(f"[{i}/{len(links)}] 건너뜀 (HTTP {code}): {link}")
            continue
        except requests.RequestException as e:
            log(f"[{i}/{len(links)}] 건너뜀 ({e.__class__.__name__}): {link}")
            continue
        if article is None:
            log(f"[{i}/{len(links)}] 건너뜀 (본문을 찾지 못함): {link}")
            continue
        articles.append(article)
        if on_article:
            on_article(article)
        preview = article["content"].replace("\n", " ")[:80]
        log(f"[{i}/{len(links)}] {article['press']} | {article['published']} | {article['title']}")
        log(f"      {preview}...  ({len(article['content']):,}자)")
    return articles


# ---------------------------------------------------------------- 명령줄
def main(argv=None):
    parser = argparse.ArgumentParser(description="네이버 뉴스 수집기 (requests + BeautifulSoup4)")
    parser.add_argument("--api", action="store_true", help="네이버 검색 API 로 수집한다 (권장, 인증 정보 필요)")
    parser.add_argument("--query", help="검색어. 생략하면 --url 의 검색어(기본 '반도체')를 쓴다 (--url 을 따로 주면 --url 이 우선)")
    parser.add_argument("--sort", choices=("date", "sim"), default="date", help="(--api 전용) date=최신순, sim=정확도순")
    parser.add_argument("--url", default=DEFAULT_URL, help="네이버 검색 결과 URL (기본: 검색어 '반도체')")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"수집할 기사 수 (기본 {DEFAULT_LIMIT}, 최대 API {API_MAX_LIMIT} / 페이지 수집 {MAX_LIMIT})")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"(페이지 수집 전용) 요청 사이 대기 초 (기본 {DEFAULT_DELAY:g}, 최소 {MIN_DELAY:g})")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="저장 파일 (.xlsx 엑셀 / .csv / .json, 기본: 스크립트 옆 news_result.json)")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="(페이지 수집 전용) robots.txt 가 금지해도 진행한다. 개인 학습용 소량 수집에 한해 직접 책임질 때만")
    args = parser.parse_args(argv)

    load_dotenv(os.path.join(BASE_DIR, ".env"))

    if args.api:
        query = args.query or parse_qs(urlparse(args.url).query).get("query", [""])[0]
        if not query:
            print("[오류] 검색어를 알 수 없습니다. --query 로 지정하세요.")
            return 2
        limit = max(1, min(args.limit, API_MAX_LIMIT))
        if limit != args.limit:
            print(f"(수집 기사 수는 1~{API_MAX_LIMIT}건으로 조정했습니다)")
        articles, fields = crawl_api(query, limit, args.sort), API_FIELDS
    else:
        limit = max(1, min(args.limit, MAX_LIMIT))
        delay = max(args.delay, MIN_DELAY)
        if (limit, delay) != (args.limit, args.delay):
            print(f"(수집 기사 수는 1~{MAX_LIMIT}건, 대기 시간은 {MIN_DELAY:g}초 이상으로 조정했습니다)")
        url = build_search_url(args.query) if args.query and args.url == DEFAULT_URL else args.url
        articles, fields = crawl(url, limit, delay, args.ignore_robots), FIELDS

    if articles is None:
        return 2
    if not articles:
        print("\n수집된 기사가 없습니다.")
        return 1
    try:
        save(articles, args.out, fields)
    except (OSError, RuntimeError) as e:
        print(f"\n[오류] 저장하지 못했습니다: {save_error_text(e)}")
        return 2
    print(f"\n{len(articles)}건 저장 완료: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
