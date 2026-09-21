"""네이버 증권 '실시간 랭킹'(거래대금 상위) 수집 - Selenium(브라우저) + BeautifulSoup4

왜 Selenium 인가
    이 주소(https://stock.naver.com/market/stock/kr/stocklist/priceTop)는 화면을 자바스크립트로 그린다.
    requests 로 받은 HTML 에는 표가 없어서 BeautifulSoup4 만으로는 읽을 수 없다.
    그래서 브라우저(Edge)를 화면 없이 띄워 페이지를 다 그리게 한 뒤, 그 HTML(driver.page_source)을
    BeautifulSoup4 로 읽는다.

수집 흐름
    1) robots.txt 확인 (네이버 증권은 자동 수집을 금지 -> 직접 책임 옵션이 없으면 여기서 멈춘다)
    2) 브라우저로 페이지 열기 -> [코스피/코스닥] 탭 선택 -> [항목 더보기] 를 눌러 목표 개수까지 늘리기
    3) 그려진 표를 BeautifulSoup4 로 읽어 종목 기록(dict)으로 만든다

주의
    - 이 목록은 KOSPI200 구성종목이 아니라 '거래대금 상위 랭킹'이다. (코스피 탭에서 200개 안팎을 가져온다)
    - stock.naver.com 의 robots.txt 는 모든 자동 수집기를 금지한다. 개인 학습용 소량 수집에 한해 직접 책임질 때만 쓴다.
      페이지는 한 번만 열고, 요청 간격을 두며, 차단으로 보이면 멈춘다.
    - 페이지 구조가 바뀌면 파서(parse_rendered)를 손봐야 한다.
    - 처음 실행할 때 Selenium 이 브라우저 드라이버를 내려받을 수 있어 인터넷이 필요하다.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

import kospi200_data as kd
import news_crawler as nc

URL = "https://stock.naver.com/market/stock/kr/stocklist/priceTop"
MARKETS = {"kospi": "코스피", "kosdaq": "코스닥", "all": "전체"}
DEFAULT_COUNT = 200
MAX_COUNT = 300
PAGE_TIMEOUT = 25            # 페이지가 그려질 때까지 최대 대기(초)
STEP_TIMEOUT = 12            # 탭 전환 / 더보기 한 번의 최대 대기(초)
POLL = 0.4
MORE_CLICK_DELAY = 1.0       # '항목 더보기' 사이 대기(초)
MAX_MORE_CLICKS = 4          # 100개씩 4번이면 최대 500개. 그 이상은 누르지 않는다
USER_AGENT = "Kospi200Study/1.0 (personal study script; requests)"

HEADER_KEYS = {"종목명": "name", "현재가": "close", "전일대비": "change", "거래량": "volume",
               "거래대금": "value", "고가": "high", "저가": "low", "시가총액": "marcap"}
UNIT_FACTORS = {"백만": 10**6, "천": 10**3, "만": 10**4, "억": 10**8, "조": 10**12, "주": 1, "원": 1}
AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(조|억|만)")
NUMBER_UNIT_RE = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*(백만|천|만|억|조|주|원)?\s*$")
CHANGE_RE = re.compile(r"(상승|하락|보합|상한가|하한가)?\s*([\d,]*(?:\.\d+)?)\s*\(\s*([+\-−]?[\d,]*(?:\.\d+)?)\s*%\s*\)")
LOGO_CODE_RE = re.compile(r"Stock([0-9A-Z]{6})")


class NaverPageError(Exception):
    """받아 온 화면에서 표를 읽을 수 없을 때."""


# ---------------------------------------------------------------- 값 해석
def parse_korean_amount(text):
    """'1,598조 9,572억' -> 원 단위 정수(1_598_957_200_000_000). 단위가 없으면 숫자 그대로. 해석할 수 없으면 None."""
    text = (text or "").strip()
    parts = AMOUNT_RE.findall(text)
    if parts:
        return int(round(sum(float(n.replace(",", "")) * UNIT_FACTORS[u] for n, u in parts)))
    return kd.parse_number(text)


def parse_number_unit(text, default_unit_factor=1):
    """'238,539 백만' -> 238_539_000_000, '13,337,015 주' -> 13337015. 단위가 없으면 default_unit_factor 를 곱한다."""
    m = NUMBER_UNIT_RE.match(text or "")
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    factor = UNIT_FACTORS[m.group(2)] if m.group(2) else default_unit_factor
    value = number * factor
    return int(value) if float(value).is_integer() else value


def _cell_text(td, keep_a11y=False):
    """셀의 글자. 화면 낭독용 문구(.a11y)는 뺀다. 숫자가 조각나 있어도 이어 붙인다('1,87' + '6,000')."""
    if not keep_a11y:
        for hidden in td.select(".a11y"):
            hidden.decompose()
    return td.get_text("", strip=True)


def parse_change(text):
    """'상승 19,000 (+1.02%)' -> (19000, 1.02). '하락 510 (-2.78%)' -> (-510, -2.78). 보합 -> (0, 0)."""
    m = CHANGE_RE.search(text or "")
    if not m:
        return None, None
    word, amount, rate = m.group(1), kd.parse_number(m.group(2)), kd.parse_number(m.group(3).replace("−", "-"))
    if amount is None:
        return None, rate
    negative = word in ("하락", "하한가") or (word is None and rate is not None and rate < 0)
    if word == "보합":
        amount = 0
    return (-amount if negative else amount), rate


def _parse_name_cell(td):
    rank_tag = td.select_one("span.index")
    rank = kd.parse_number(rank_tag.get_text()) if rank_tag else None
    code = ""
    img = td.find("img", src=True)
    if img:
        m = LOGO_CODE_RE.search(img["src"])
        code = m.group(1) if m else ""
    # 이름 앞에 붙는 장식 요소를 걷어낸다: 관심종목 버튼, 순위, 아이콘, 낭독용 문구, 로고, 그리고 로고가 없을 때
    # 대신 들어가는 '첫 글자 자리표시'(aria-hidden="true" 로 표시된 장식 요소)
    for junk in td.select("button, .index, svg, .a11y, img, [aria-hidden='true']"):
        junk.decompose()
    return rank, code, td.get_text(" ", strip=True)


# ---------------------------------------------------------------- 그려진 화면(HTML) -> 종목 기록
def parse_rendered(html):
    """브라우저가 다 그린 페이지의 HTML 에서 순위 표를 읽어 dict 목록으로 돌려준다."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise NaverPageError("페이지에서 표(<table>)를 찾지 못했습니다. 화면이 아직 다 그려지지 않았거나 구조가 바뀌었습니다.")
    rows = table.find_all("tr")
    header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])] if rows else []
    index = {HEADER_KEYS[h]: i for i, h in enumerate(header) if h in HEADER_KEYS}
    if "name" not in index or "close" not in index:
        raise NaverPageError(f"표의 머리글이 예상과 다릅니다: {header}")

    records = []
    for tr in rows[1:]:
        tds = tr.find_all("td", recursive=False)
        if len(tds) <= max(index.values()):
            continue
        rank, code, name = _parse_name_cell(tds[index["name"]])
        rec = {"rank": rank, "code": code, "name": name}
        rec["close"] = kd.parse_number(_cell_text(tds[index["close"]]))
        if "change" in index:
            rec["change"], rec["rate"] = parse_change(tds[index["change"]].get_text(" ", strip=True))
        for key in ("volume", "high", "low"):
            if key in index:
                rec[key] = parse_number_unit(_cell_text(tds[index[key]]))
        if "value" in index:
            rec["value"] = parse_number_unit(_cell_text(tds[index["value"]]), default_unit_factor=10**6)   # 화면은 백만원 단위
        if "marcap" in index:
            rec["marcap"] = parse_korean_amount(_cell_text(tds[index["marcap"]]))
        if rec["name"] and rec["close"] is not None:
            records.append(rec)
    if not records:
        raise NaverPageError("표는 찾았지만 종목 행을 읽지 못했습니다.")
    return records


def build_result(records, market_key, notes=None):
    label = MARKETS.get(market_key, "")
    if market_key != "all":
        for r in records:
            r["market"] = label
    present = {k for r in records for k, v in r.items() if v not in (None, "")}
    columns = [k for k in kd.COLUMNS if k in present]
    all_notes = ["이 목록은 KOSPI200 구성종목이 아니라 네이버의 '거래대금 상위' 실시간 랭킹입니다."]
    if market_key == "all":
        all_notes.append("시장(코스피/코스닥) 구분 없이 전체 랭킹입니다.")
    no_code = sum(1 for r in records if not r.get("code"))
    if no_code:
        all_notes.append(f"{no_code}개 종목은 화면에 로고가 없어 종목코드를 알 수 없습니다 (종목코드 칸이 비어 있습니다).")
    all_notes += notes or []
    fmt = f"네이버 증권 실시간 랭킹(거래대금 상위) · {label} · Selenium + BeautifulSoup4"
    return kd.LoadResult(records, columns, 0, fmt, all_notes, "네이버 증권")


# ---------------------------------------------------------------- 브라우저 제어
def default_driver_factory():
    """화면 없는(headless) Edge 를, 안 되면 Chrome 을 띄운다."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.edge.options import Options as EdgeOptions

    errors = []
    for options_cls, driver_cls in ((EdgeOptions, webdriver.Edge), (ChromeOptions, webdriver.Chrome)):
        opts = options_cls()
        for arg in ("--headless=new", "--disable-gpu", "--no-sandbox", "--window-size=1400,1000", "--lang=ko-KR"):
            opts.add_argument(arg)
        try:
            return driver_cls(options=opts)
        except Exception as e:                        # 브라우저/드라이버가 없을 때
            errors.append(f"{driver_cls.__name__}: {e.__class__.__name__}")
    raise RuntimeError("Edge 또는 Chrome 브라우저를 시작하지 못했습니다 (" + ", ".join(errors) + ").")


MORE_BUTTON_SELECTOR = "button"
MARKET_BUTTON_SELECTOR = "button[role='radio']"
ROW_COUNT_JS = "return document.querySelectorAll('table tr td:first-child').length;"
CLICK_BY_TEXT_JS = """
const els = [...document.querySelectorAll(arguments[0])].filter(e => e.textContent.trim() === arguments[1]);
if (!els.length) return false;
els[0].click();
return true;
"""
MARKET_ACTIVE_JS = """
const b = [...document.querySelectorAll("button[role='radio']")].find(e => e.textContent.trim() === arguments[0]);
return !!b && /active/.test(b.className);
"""


def _row_count(driver):
    return int(driver.execute_script(ROW_COUNT_JS) or 0)


def _wait_for(condition, timeout, stop):
    """condition() 이 참이 될 때까지 기다린다. 시간이 지나면 False, 중지를 눌렀으면 None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop():
            return None
        if condition():
            return True
        time.sleep(POLL)
    return False


def _wait_stable(driver, timeout, stop):
    """행 수가 두 번 연속 같고 0 보다 크면 다 그려졌다고 본다."""
    state = {"last": -1}

    def stable():
        n = _row_count(driver)
        done = n > 0 and n == state["last"]
        state["last"] = n
        return done
    return _wait_for(stable, timeout, stop)


def collect(count=DEFAULT_COUNT, market="kospi", agree=False, log=print, stop=None,
            driver_factory=None, session=None):
    """네이버 실시간 랭킹을 수집해 kd.LoadResult 로 돌려준다. 시작하지 못하거나 실패하면 None."""
    stop = stop or (lambda: False)
    count = max(1, min(int(count), MAX_COUNT))
    market = market if market in MARKETS else "kospi"

    # 1) robots.txt
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    if not nc.robots_allows(session, URL):
        log("[중단] robots.txt 가 자동 수집을 금지하고 있습니다: stock.naver.com")
        if not agree:
            log("       개인 학습용으로 소량만 받겠다고 판단해 직접 책임질 때에만 '직접 책임' 을 체크하고 다시 실행하세요.")
            log("       대안: KRX 등에서 받은 파일을 [파일 열기] 로 불러오면 사이트에 접속하지 않고 사용할 수 있습니다.")
            return None
        log("       '직접 책임' 이 체크되어 계속합니다. 페이지는 한 번만 열고 요청 간격을 둡니다.")

    # 2) 브라우저
    notes = []
    try:
        driver = (driver_factory or default_driver_factory)()
    except Exception as e:
        log(f"[오류] 브라우저를 시작하지 못했습니다: {e}")
        log("       Edge 또는 Chrome 이 설치되어 있고, 처음 실행이면 인터넷에 연결되어 있는지 확인하세요.")
        return None

    try:
        log(f"페이지를 여는 중… ({URL})")
        driver.get(URL)
        ready = _wait_for(lambda: _row_count(driver) > 0, PAGE_TIMEOUT, stop)
        if ready is None:
            log("[중단] 사용자가 중지했습니다.")
            return None
        if not ready:
            log("[오류] 시간 안에 순위 표가 나타나지 않았습니다. 인터넷 연결이나 차단 여부를 확인하세요.")
            return None
        log(f"표가 나타났습니다 ({_row_count(driver)}행).")

        if market != "all":
            label = MARKETS[market]
            if driver.execute_script(CLICK_BY_TEXT_JS, MARKET_BUTTON_SELECTOR, label):
                _wait_for(lambda: bool(driver.execute_script(MARKET_ACTIVE_JS, label)), STEP_TIMEOUT, stop)
                _wait_stable(driver, STEP_TIMEOUT, stop)
                log(f"'{label}' 탭을 선택했습니다 ({_row_count(driver)}행).")
            else:
                log(f"[알림] '{label}' 탭을 찾지 못해 전체 랭킹으로 진행합니다.")
                notes.append(f"'{label}' 탭을 찾지 못해 전체 랭킹을 받았습니다.")
                market = "all"

        clicks = 0
        while _row_count(driver) < count and clicks < MAX_MORE_CLICKS and not stop():
            before = _row_count(driver)
            time.sleep(MORE_CLICK_DELAY)
            # 글자가 같은 요소가 바깥 div > button > span 으로 겹쳐 있다. 실제 동작은 <button> 에만 걸려 있으므로 button 만 누른다.
            if not driver.execute_script(CLICK_BY_TEXT_JS, MORE_BUTTON_SELECTOR, "항목 더보기"):
                log("'항목 더보기' 버튼이 더 이상 없습니다.")
                break
            clicks += 1
            _wait_for(lambda: _row_count(driver) > before, STEP_TIMEOUT, stop)
            log(f"'항목 더보기' {clicks}번 → {_row_count(driver)}행")
        html = driver.page_source
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    # 3) 파싱
    try:
        records = parse_rendered(html)
    except NaverPageError as e:
        log(f"[오류] {e}")
        return None
    if len(records) < count:
        notes.append(f"요청한 {count}개보다 적은 {len(records)}개만 받았습니다.")
    records = records[:count]
    if stop():
        log(f"[중단] 사용자가 중지했습니다. 지금까지 읽은 {len(records)}개를 사용합니다.")
    log(f"수집 완료: {len(records)}종목")
    return build_result(records, market, notes)
