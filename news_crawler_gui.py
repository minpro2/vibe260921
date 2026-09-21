"""네이버 뉴스 수집기 - PyQt6 화면(GUI) 버전

실행
    python news_crawler_gui.py

필요한 패키지
    pip install PyQt6 requests beautifulsoup4 openpyxl

사용법
    1) 수집 방식을 고른다.
       - 네이버 검색 API (권장): Client ID / Secret 을 입력한다. 제목 · 요약 · 발행 시각 · 링크를 받는다.
         (인증 정보는 news_crawler.py 옆의 .env 파일이나 환경 변수에 두면 자동으로 채워진다)
       - 검색 결과 페이지 수집: 기사 본문 전체를 받지만 robots.txt 가 자동 수집을 금지한다.
         '직접 책임' 을 체크한 경우에만 금지를 무시하고 진행한다.
    2) 검색어와 건수를 정하고 [수집 시작] 을 누른다. 결과는 표에 실시간으로 쌓이고,
       행을 선택하면 아래에 요약/본문이 보인다. 더블클릭하면 기사를 브라우저로 연다.
    3) [엑셀로 저장] 으로 .xlsx 파일에, [JSON/CSV 저장] 으로 JSON 또는 CSV 파일에 저장한다.
       (엑셀 저장에는 openpyxl 이 필요: pip install openpyxl)

수집 로직은 news_crawler.py 를 그대로 쓰고, 이 파일은 화면과 스레드만 맡는다.
수집은 별도 스레드에서 돌아가므로 진행 중에도 화면이 멈추지 않고 [중지] 가 바로 먹는다.
기사 저작권은 언론사에 있다. 수집한 내용을 재배포하거나 서비스에 쓰면 안 된다.
"""

import html
import os
import sys
import threading
from urllib.parse import urlparse

from PyQt6.QtCore import QObject, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QRadioButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

import news_crawler as nc

DEFAULT_QUERY = "반도체"
TABLE_HEADERS = ("#", "작성 시각", "출처", "제목")
LOG_MAX_LINES = 1000
MAX_DELAY = 10.0
SAVE_FILTERS = {"xlsx": "Excel 파일 (*.xlsx)", "csv": "CSV 파일 (*.csv)", "json": "JSON 파일 (*.json)"}


def safe_link(url):
    """브라우저로 열어도 되는 주소(http/https)만 돌려준다. 그 밖의 형태는 빈 문자열."""
    return url if url and urlparse(url).scheme in ("http", "https") else ""


def article_link(article):
    return safe_link(article.get("link") or article.get("url") or article.get("originallink") or "")


def detail_html(article):
    """표에서 고른 기사를 오른쪽 아래 창에 보여 줄 HTML. 기사 글은 전부 escape 해서 넣는다."""
    title = html.escape(article.get("title", ""))
    parts = [article.get("published"), article.get("domain") or article.get("press"), article.get("reporter")]
    meta = " · ".join(html.escape(p) for p in parts if p)
    body_text = article.get("content") or article.get("summary") or ""
    label = "본문" if article.get("content") else "요약"
    body = "<br>".join(html.escape(line) for line in body_text.split("\n"))
    link = article_link(article)
    link_html = f'<p><a href="{html.escape(link, quote=True)}">기사 원문 열기</a></p>' if link else ""
    return f"<h3>{title}</h3><p style='color:gray'>{meta}</p>{link_html}<p><b>{label}</b></p><p>{body}</p>"


class Worker(QObject):
    """수집을 별도 스레드에서 실행한다. 진행 상황은 시그널로만 화면에 전달한다."""

    log = pyqtSignal(str)
    article = pyqtSignal(dict)
    done = pyqtSignal(object)          # 기사 목록(list) 또는 시작/요청에 실패했으면 None

    def __init__(self, job, session_factory=None):
        super().__init__()
        self.job = job
        self.session_factory = session_factory
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    @property
    def stopped(self):
        return self._stop.is_set()

    def run(self):
        job, result = self.job, None
        try:
            session = self.session_factory() if self.session_factory else None
            common = dict(session=session, log=self.log.emit, stop=self._stop.is_set, on_article=self.article.emit)
            if job["mode"] == "api":
                result = nc.crawl_api(
                    job["query"], job["limit"], job["sort"],
                    credentials=(job["client_id"], job["client_secret"]), **common)
            else:
                result = nc.crawl(job["url"], job["limit"], job["delay"], job["ignore_robots"], **common)
        except Exception as e:          # 예상하지 못한 오류도 화면에 알리고, 화면은 정상 상태로 되돌린다
            self.log.emit(f"[오류] 예기치 않은 오류: {e.__class__.__name__}: {e}")
        self.done.emit(result)


class MainWindow(QMainWindow):
    def __init__(self, session_factory=None):
        super().__init__()
        self.session_factory = session_factory      # 테스트에서 가짜 세션을 끼워 넣을 때만 쓴다
        self._thread = None
        self._worker = None
        self._closing = False
        self.articles = []
        self.fields = nc.API_FIELDS

        self.setWindowTitle("네이버 뉴스 수집기")
        self.resize(1040, 800)
        self._build_ui()
        self._load_credentials()
        self._on_mode_changed()
        self._set_running(False)

    # ------------------------------------------------------------ 화면 구성
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        # -- 수집 방식
        self.mode_box = QGroupBox("수집 방식")
        mode_layout = QVBoxLayout(self.mode_box)
        radios = QHBoxLayout()
        self.rb_api = QRadioButton("네이버 검색 API (권장) — 제목 · 요약 · 링크")
        self.rb_page = QRadioButton("검색 결과 페이지 수집 — 기사 본문 전체 (robots.txt 금지)")
        self.rb_api.setChecked(True)
        radios.addWidget(self.rb_api)
        radios.addWidget(self.rb_page)
        radios.addStretch(1)
        mode_layout.addLayout(radios)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_api_page())
        self.stack.addWidget(self._build_page_page())
        mode_layout.addWidget(self.stack)
        root.addWidget(self.mode_box)

        # -- 검색어 / 건수
        row = QHBoxLayout()
        row.addWidget(QLabel("검색어"))
        self.ed_query = QLineEdit(DEFAULT_QUERY)
        self.ed_query.setPlaceholderText("검색어를 입력하세요")
        self.ed_query.setClearButtonEnabled(True)
        row.addWidget(self.ed_query, 1)
        row.addWidget(QLabel("건수"))
        self.sp_limit = QSpinBox()
        self.sp_limit.setRange(1, nc.API_MAX_LIMIT)
        self.sp_limit.setValue(nc.DEFAULT_LIMIT)
        self.sp_limit.setSuffix(" 건")
        row.addWidget(self.sp_limit)
        root.addLayout(row)

        # -- 버튼 / 진행 표시
        buttons = QHBoxLayout()
        self.btn_start = QPushButton("수집 시작")
        self.btn_start.setDefault(True)
        self.btn_stop = QPushButton("중지")
        self.btn_excel = QPushButton("엑셀로 저장…")
        self.btn_save = QPushButton("JSON/CSV 저장…")
        self.btn_clear = QPushButton("결과 지우기")
        for b in (self.btn_start, self.btn_stop, self.btn_excel, self.btn_save, self.btn_clear):
            buttons.addWidget(b)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        root.addWidget(self.progress)
        self.lbl_status = QLabel("검색어를 입력하고 [수집 시작] 을 누르세요.")
        root.addWidget(self.lbl_status)

        # -- 결과 표 / 상세 / 로그
        self.table = QTableWidget(0, len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for col in (0, 1, 2):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.detail.setPlaceholderText("표에서 기사를 선택하면 요약/본문이 여기에 표시됩니다. 더블클릭하면 브라우저로 엽니다.")

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_MAX_LINES)
        self.log.setPlaceholderText("진행 상황과 오류 메시지가 여기에 표시됩니다.")

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        splitter.addWidget(self.log)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 2)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        # -- 동작 연결
        self.rb_api.toggled.connect(self._on_mode_changed)
        self.cb_show.toggled.connect(self._toggle_secret)
        self.ed_query.returnPressed.connect(self.start)
        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_excel.clicked.connect(self.save_excel)
        self.btn_save.clicked.connect(self.save_results)
        self.btn_clear.clicked.connect(self._clear_results)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.table.cellDoubleClicked.connect(self._open_row_link)

    def _build_api_page(self):
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 4, 0, 0)
        self.ed_id = QLineEdit()
        self.ed_id.setPlaceholderText("Client ID")
        self.ed_secret = QLineEdit()
        self.ed_secret.setPlaceholderText("Client Secret")
        self.ed_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.cb_show = QCheckBox("표시")
        secret_row = QHBoxLayout()
        secret_row.addWidget(self.ed_secret, 1)
        secret_row.addWidget(self.cb_show)
        self.cmb_sort = QComboBox()
        self.cmb_sort.addItem("최신순", "date")
        self.cmb_sort.addItem("정확도순", "sim")
        help_link = QLabel('<a href="https://developers.naver.com/apps">인증 정보 발급하기 (애플리케이션 등록 → 사용 API: 검색)</a>')
        help_link.setOpenExternalLinks(True)
        form.addRow("Client ID", self.ed_id)
        form.addRow("Client Secret", secret_row)
        form.addRow("정렬", self.cmb_sort)
        form.addRow("", help_link)
        return page

    def _build_page_page(self):
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 4, 0, 0)
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("비우면 위의 검색어로 검색 URL 을 자동으로 만듭니다")
        self.sp_delay = QDoubleSpinBox()
        self.sp_delay.setRange(nc.MIN_DELAY, MAX_DELAY)
        self.sp_delay.setSingleStep(0.5)
        self.sp_delay.setValue(nc.DEFAULT_DELAY)
        self.sp_delay.setSuffix(" 초")
        warn = QLabel(
            "⚠ search.naver.com 과 n.news.naver.com 의 robots.txt 는 모든 자동 수집기를 금지합니다. "
            "아래에 체크하지 않으면 금지된 경우 아무것도 받지 않고 멈춥니다. 차단 응답이 오면 우회하지 않고 즉시 멈춥니다.")
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #d9822b;")
        self.cb_agree = QCheckBox("robots.txt 가 금지함을 이해했으며, 개인 학습용 소량 수집으로 직접 책임지고 진행합니다")
        form.addRow("검색 URL", self.ed_url)
        form.addRow("요청 간격", self.sp_delay)
        form.addRow(warn)
        form.addRow(self.cb_agree)
        return page

    # ------------------------------------------------------------ 초기값 / 모드
    def _load_credentials(self):
        nc.load_dotenv(os.path.join(nc.BASE_DIR, ".env"))
        self.ed_id.setText(os.environ.get(nc.ID_ENV, ""))
        self.ed_secret.setText(os.environ.get(nc.SECRET_ENV, ""))

    def _toggle_secret(self, shown):
        self.ed_secret.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)

    def _on_mode_changed(self, *_):
        api = self.rb_api.isChecked()
        self.stack.setCurrentIndex(0 if api else 1)
        self.sp_limit.setMaximum(nc.API_MAX_LIMIT if api else nc.MAX_LIMIT)   # 값이 최대보다 크면 자동으로 줄어든다

    # ------------------------------------------------------------ 실행 / 중지
    def _build_job(self):
        query = self.ed_query.text().strip()
        if self.rb_api.isChecked():
            if not query:
                return self._invalid("검색어를 입력하세요.")
            return {"mode": "api", "query": query, "limit": self.sp_limit.value(),
                    "sort": self.cmb_sort.currentData(),
                    "client_id": self.ed_id.text().strip(), "client_secret": self.ed_secret.text().strip()}
        url = self.ed_url.text().strip() or (nc.build_search_url(query) if query else "")
        if not url:
            return self._invalid("검색어 또는 검색 URL 을 입력하세요.")
        return {"mode": "page", "url": url, "limit": self.sp_limit.value(), "delay": self.sp_delay.value(),
                "ignore_robots": self.cb_agree.isChecked()}

    def _invalid(self, message):
        self.lbl_status.setText(message)
        self.ed_query.setFocus()
        return None

    def start(self):
        if self._thread is not None:
            return
        job = self._build_job()
        if job is None:
            return
        self._clear_results()
        self.fields = nc.API_FIELDS if job["mode"] == "api" else nc.FIELDS

        self._worker = Worker(job, self.session_factory)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.append_log)
        self._worker.article.connect(self.add_article)
        self._worker.done.connect(self._on_done)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)     # 워커는 자기 스레드가 끝날 때 정리된다
        self._thread.finished.connect(self._on_thread_finished)

        self._set_running(True)
        self.lbl_status.setText("수집 중…")
        self._thread.start()

    def stop(self):
        try:
            if self._worker is not None and not self._worker.stopped:
                self._worker.stop()
                self.btn_stop.setEnabled(False)
                self.lbl_status.setText("중지하는 중…")
        except RuntimeError:
            pass                        # 그 사이 워커가 이미 정리됐다: 멈출 것이 없다

    def _on_done(self, result):
        count = len(self.articles)
        if self._worker is not None and self._worker.stopped:            # 이 시점의 워커는 아직 살아 있다
            text = f"중지됨: {count}건 수집"
        elif result is None:
            text = "수집하지 못했습니다. 아래 로그를 확인하세요."
        elif count == 0:
            text = "수집된 기사가 없습니다."
        else:
            text = f"수집 완료: {count}건"
        self.lbl_status.setText(text)

    def _on_thread_finished(self):
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = self._thread = None
        self._set_running(False)

    def _set_running(self, running):
        self.mode_box.setEnabled(not running)
        self.ed_query.setEnabled(not running)
        self.sp_limit.setEnabled(not running)
        self.btn_start.setEnabled(not running)
        self.btn_clear.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self._update_save_buttons()
        if running:
            self.progress.setRange(0, 0)               # 진행 중 표시(끝을 알 수 없는 막대)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)

    # ------------------------------------------------------------ 결과 표시
    def append_log(self, text):
        self.log.appendPlainText(text)

    def add_article(self, article):
        self.articles.append(article)
        row = self.table.rowCount()
        self.table.insertRow(row)
        source = article.get("domain") or article.get("press") or ""
        values = (str(row + 1), article.get("published", ""), source, article.get("title", ""))
        for col, text in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem(text))
        if row == 0:
            self.table.selectRow(0)
        self._update_save_buttons()
        self.lbl_status.setText(f"수집 중… {len(self.articles)}건")

    def _show_detail(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if rows and 0 <= rows[0].row() < len(self.articles):
            self.detail.setHtml(detail_html(self.articles[rows[0].row()]))
        else:
            self.detail.clear()

    def _open_row_link(self, row, _col):
        if 0 <= row < len(self.articles):
            link = article_link(self.articles[row])
            if link:
                QDesktopServices.openUrl(QUrl(link))

    def _clear_results(self):
        self.articles = []
        self.table.setRowCount(0)
        self.detail.clear()
        self._update_save_buttons()

    def _update_save_buttons(self):
        has_results = bool(self.articles)
        self.btn_excel.setEnabled(has_results)
        self.btn_save.setEnabled(has_results)

    # ------------------------------------------------------------ 저장
    def save_excel(self):
        self._save_dialog(("xlsx",))

    def save_results(self):
        self._save_dialog(("json", "csv"))

    def _save_dialog(self, exts):
        """저장 대화상자를 띄우고, 확장자를 쓰지 않았으면 고른 형식에 맞게 붙여서 저장한다."""
        if not self.articles:
            return
        path, selected = QFileDialog.getSaveFileName(
            self, "결과 저장", os.path.join(nc.BASE_DIR, f"news_result.{exts[0]}"),
            ";;".join(SAVE_FILTERS[e] for e in exts))
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += "." + next((e for e in exts if SAVE_FILTERS[e] == selected), exts[0])
        self.save_to(path)

    def save_to(self, path):
        try:
            nc.save(self.articles, path, self.fields)
        except (OSError, RuntimeError) as e:
            QMessageBox.warning(self, "저장 실패", f"파일을 저장하지 못했습니다.\n{nc.save_error_text(e)}")
            return False
        self.lbl_status.setText(f"저장 완료: {path}")
        self.append_log(f"[저장] {len(self.articles)}건 → {path}")
        return True

    # ------------------------------------------------------------ 종료
    def closeEvent(self, event):
        if self._thread is None or not self._thread.isRunning():
            event.accept()
            return
        # 수집 스레드가 끝나기 전에 창을 닫으면 Qt 가 강제 종료된다. 중지를 요청하고 끝나면 프로그램을 마친다.
        if not self._closing:
            self._closing = True
            self.stop()
            self._thread.finished.connect(QApplication.quit)
        self.hide()
        event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
