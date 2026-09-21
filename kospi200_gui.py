"""코스피200 뷰어 - PyQt6 화면(GUI)

실행
    python kospi200_gui.py

필요한 패키지
    pip install PyQt6 beautifulsoup4 selenium openpyxl requests

데이터를 가져오는 두 가지 방법
    A) 네이버에서 직접 수집: 네이버 증권 '실시간 랭킹'(거래대금 상위) 페이지를 브라우저(Edge, 화면 없이)로 열어
       코스피 탭에서 목표 개수(기본 200개)까지 불러온 뒤, BeautifulSoup4 로 표를 읽는다.
       robots.txt 가 자동 수집을 금지하므로 '직접 책임' 을 체크해야만 진행한다. (naver_stock.py)
    B) 파일 열기: 한국거래소(KRX)에서 내려받은 KOSPI200 CSV/Excel 을 열거나 창에 끌어다 놓는다.
       사이트에 접속하지 않는다. ([KRX에서 받는 방법] 버튼에 순서가 있다) (kospi200_data.py)

화면 사용
    열 머리글을 눌러 정렬하고, 검색칸/필터로 종목을 좁힌다.
    [엑셀로 저장] / [CSV 저장] 은 지금 화면에 보이는 그대로(정렬·필터 반영) 저장한다.
    수집은 별도 스레드에서 돌아가므로 진행 중에도 화면이 멈추지 않고 [중지] 가 바로 먹는다.
"""

import os
import sys
import threading

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

import kospi200_data as kd
import naver_stock as ns

UP_COLOR, DOWN_COLOR = QColor("#d92d20"), QColor("#3b82f6")      # 상승 빨강, 하락 파랑 (한국식)
UP_HTML, DOWN_HTML = "#d92d20", "#3b82f6"
DIRECTION_FILTERS = (("전체", "all"), ("상승", "up"), ("하락", "down"), ("보합", "flat"))
OPEN_FILTER = "코스피200 파일 (*.csv *.txt *.xlsx *.xls *.html *.htm);;모든 파일 (*)"
SAVE_FILTERS = {"xlsx": "Excel 파일 (*.xlsx)", "csv": "CSV 파일 (*.csv)"}
LOG_MAX_LINES = 500
EMPTY_TEXT = ("위의 [네이버에서 수집] 으로 순위를 직접 가져오거나,\n"
              "KRX 에서 내려받은 KOSPI200 파일(CSV / Excel)을 [파일 열기] 로 열거나 이 창에 끌어다 놓으세요.\n\n"
              "KRX 파일을 받는 방법은 [KRX에서 받는 방법] 을 눌러 확인하세요.")
HELP_HTML = """
<h3>KOSPI200 데이터 받는 방법 (KRX 정보데이터시스템)</h3>
<ol>
<li><a href="https://data.krx.co.kr">data.krx.co.kr</a> 에 접속합니다.</li>
<li>메뉴에서 <b>통계 → 지수 → 주가지수</b> 쪽의 <b>구성종목(지수구성종목)</b> 화면으로 이동합니다.
    <br><span style="color:gray">메뉴 이름은 사이트 개편으로 달라질 수 있습니다.</span></li>
<li>지수를 <b>코스피 200</b> 으로 고르고 조회합니다.</li>
<li>표 오른쪽 위의 <b>CSV</b> 또는 <b>Excel</b> 다운로드 버튼으로 파일을 받습니다.</li>
<li>받은 파일을 이 앱의 <b>[파일 열기]</b> 로 열거나 창에 끌어다 놓습니다.</li>
</ol>
<p><b>읽는 열</b>: 종목코드, 종목명, 종가(현재가), 대비, 등락률, 시가, 고가, 저가, 거래량, 거래대금, 시가총액, 상장주식수 등.
열 이름이 달라 읽지 못하면 화면에 파일에서 찾은 열 이름을 보여 줍니다.</p>
<p style="color:gray">파일 열기는 KRX·네이버 등 어떤 사이트에도 접속하지 않고 내려받은 파일만 읽습니다.
KRX 데이터의 이용 조건은 사이트의 안내를 따르세요.</p>
"""


class NumItem(QTableWidgetItem):
    """숫자 열 정렬용: 화면에 보이는 글자('1,234', '+1.20%')가 아니라 실제 숫자로 비교한다."""

    def __init__(self, text, value):
        super().__init__(text)
        self.value = value

    def __lt__(self, other):
        a, b = self.value, getattr(other, "value", None)
        if a is None or b is None:
            return a is None and b is not None          # 값이 없는 칸은 맨 앞(오름차순)/맨 뒤(내림차순)
        return a < b


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KRX에서 KOSPI200 파일 받는 방법")
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(HELP_HTML)
        layout.addWidget(browser)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)


class CollectWorker(QObject):
    """네이버 수집을 별도 스레드에서 실행한다. 진행 상황은 시그널로만 화면에 전달한다."""

    log = pyqtSignal(str)
    done = pyqtSignal(object)           # kd.LoadResult 또는 None(시작/수집 실패)

    def __init__(self, params, collector=None):
        super().__init__()
        self.params = params
        self.collector = collector or ns.collect
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    @property
    def stopped(self):
        return self._stop.is_set()

    def run(self):
        result = None
        try:
            result = self.collector(log=self.log.emit, stop=self._stop.is_set, **self.params)
        except Exception as e:          # 예상하지 못한 오류도 화면에 알리고, 화면은 정상 상태로 되돌린다
            self.log.emit(f"[오류] 예기치 않은 오류: {e.__class__.__name__}: {e}")
        self.done.emit(result)


class MainWindow(QMainWindow):
    def __init__(self, collector=None):
        super().__init__()
        self.collector = collector          # 테스트에서 가짜 수집기를 끼워 넣을 때만 쓴다
        self.result = None
        self.path = None                    # 파일에서 불러왔을 때만 값이 있다
        self._thread = None
        self._worker = None
        self._closing = False
        self.setWindowTitle("코스피200 뷰어")
        self.resize(1120, 860)
        self.setAcceptDrops(True)
        self._build_ui()

    # ------------------------------------------------------------ 화면 구성
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        # -- 네이버에서 직접 수집
        self.collect_box = QGroupBox("네이버에서 직접 수집 (Selenium + BeautifulSoup4)")
        cl = QVBoxLayout(self.collect_box)
        row = QHBoxLayout()
        self.cmb_market = QComboBox()
        for key, label in ns.MARKETS.items():
            self.cmb_market.addItem(label, key)
        self.sp_count = QSpinBox()
        self.sp_count.setRange(10, ns.MAX_COUNT)
        self.sp_count.setValue(ns.DEFAULT_COUNT)
        self.sp_count.setSuffix(" 개")
        self.btn_collect = QPushButton("네이버에서 수집")
        self.btn_collect.setDefault(True)
        self.btn_stop = QPushButton("중지")
        row.addWidget(QLabel("시장"))
        row.addWidget(self.cmb_market)
        row.addWidget(QLabel("개수"))
        row.addWidget(self.sp_count)
        row.addWidget(self.btn_collect)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        cl.addLayout(row)
        self.cb_agree = QCheckBox("stock.naver.com 의 robots.txt 가 자동 수집을 금지함을 이해했으며, 개인 학습용 소량 수집으로 직접 책임지고 진행합니다")
        cl.addWidget(self.cb_agree)
        note = QLabel("수집 대상은 KOSPI200 구성종목이 아니라 네이버의 '거래대금 상위' 실시간 랭킹입니다. "
                      "페이지는 한 번만 열고, 브라우저(Edge/Chrome)가 필요합니다.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        cl.addWidget(note)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        cl.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_MAX_LINES)
        self.log.setFixedHeight(84)
        self.log.setPlaceholderText("수집 진행 상황이 여기에 표시됩니다.")
        cl.addWidget(self.log)
        root.addWidget(self.collect_box)

        # -- 파일 / 저장
        top = QHBoxLayout()
        self.btn_open = QPushButton("파일 열기…")
        self.btn_reload = QPushButton("다시 불러오기")
        self.btn_help = QPushButton("KRX에서 받는 방법")
        self.btn_excel = QPushButton("엑셀로 저장…")
        self.btn_csv = QPushButton("CSV 저장…")
        for b in (self.btn_open, self.btn_reload, self.btn_help):
            top.addWidget(b)
        top.addStretch(1)
        top.addWidget(self.btn_excel)
        top.addWidget(self.btn_csv)
        root.addLayout(top)

        self.lbl_file = QLabel("아직 데이터가 없습니다.")
        root.addWidget(self.lbl_file)
        self.lbl_notes = QLabel()
        self.lbl_notes.setWordWrap(True)
        self.lbl_notes.setStyleSheet("color: #d9822b;")
        self.lbl_notes.hide()
        root.addWidget(self.lbl_notes)

        filters = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("종목명 또는 종목코드로 검색")
        self.ed_search.setClearButtonEnabled(True)
        self.cmb_filter = QComboBox()
        for label, key in DIRECTION_FILTERS:
            self.cmb_filter.addItem(label, key)
        self.lbl_shown = QLabel("")
        filters.addWidget(self.ed_search, 1)
        filters.addWidget(QLabel("등락"))
        filters.addWidget(self.cmb_filter)
        filters.addWidget(self.lbl_shown)
        root.addLayout(filters)

        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setHighlightSections(False)

        self.empty = QLabel(EMPTY_TEXT)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("color: gray; font-size: 15px;")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.table)
        root.addWidget(self.stack, 1)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_summary.setWordWrap(True)
        root.addWidget(self.lbl_summary)

        self.btn_collect.clicked.connect(self.start_collect)
        self.btn_stop.clicked.connect(self.stop_collect)
        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_reload.clicked.connect(self.reload)
        self.btn_help.clicked.connect(self.show_help)
        self.btn_excel.clicked.connect(lambda: self._save_dialog("xlsx"))
        self.btn_csv.clicked.connect(lambda: self._save_dialog("csv"))
        self.ed_search.textChanged.connect(self._apply_filter)
        self.cmb_filter.currentIndexChanged.connect(self._apply_filter)
        self._update_buttons()

    def _running(self):
        return self._thread is not None

    def _update_buttons(self):
        running, loaded = self._running(), self.result is not None
        self.collect_box.setEnabled(True)
        for w in (self.cmb_market, self.sp_count, self.cb_agree, self.btn_collect, self.btn_open):
            w.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_reload.setEnabled(loaded and self.path is not None and not running)
        self.btn_excel.setEnabled(loaded and not running)
        self.btn_csv.setEnabled(loaded and not running)
        if running:
            self.progress.setRange(0, 0)               # 진행 중 표시(끝을 알 수 없는 막대)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)

    def show_help(self):
        HelpDialog(self).exec()

    def append_log(self, text):
        self.log.appendPlainText(text)

    # ------------------------------------------------------------ 네이버에서 수집 (스레드)
    def start_collect(self):
        if self._running():
            return
        params = {"count": self.sp_count.value(), "market": self.cmb_market.currentData(),
                  "agree": self.cb_agree.isChecked()}
        self.log.clear()
        self._worker = CollectWorker(params, self.collector)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.append_log)
        self._worker.done.connect(self._on_collect_done)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)      # 워커는 자기 스레드가 끝날 때 정리된다
        self._thread.finished.connect(self._on_thread_finished)
        self.lbl_file.setText("네이버에서 수집 중…")
        self._update_buttons()
        self._thread.start()

    def stop_collect(self):
        try:
            if self._worker is not None and not self._worker.stopped:
                self._worker.stop()
                self.btn_stop.setEnabled(False)
                self.lbl_file.setText("중지하는 중…")
        except RuntimeError:
            pass                        # 그 사이 워커가 이미 정리됐다: 멈출 것이 없다

    def _on_collect_done(self, result):
        stopped = self._worker is not None and self._worker.stopped     # 이 시점의 워커는 아직 살아 있다
        if result is not None and result.records:
            self._show_result(result, path=None)
            if stopped:
                self.lbl_file.setText(self.lbl_file.text() + "  (중지됨)")
        elif stopped:
            self.lbl_file.setText("중지됨")
        else:
            self.lbl_file.setText("수집하지 못했습니다. 위 로그를 확인하세요." if self.result is None
                                  else self.lbl_file.text())

    def _on_thread_finished(self):
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = self._thread = None
        self._update_buttons()

    # ------------------------------------------------------------ 파일 불러오기
    def open_dialog(self):
        start = os.path.dirname(self.path) if self.path else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "코스피200 파일 열기", start, OPEN_FILTER)
        if path:
            self.load_file(path)

    def reload(self):
        if self.path:
            self.load_file(self.path)

    def load_file(self, path):
        """성공하면 True. 실패하면 이유를 알려 주고, 이미 열려 있던 데이터는 그대로 둔다."""
        if self._running():
            return False
        try:
            result = kd.load_file(path)
        except kd.KospiFileError as e:
            self.lbl_file.setText(f"불러오지 못했습니다: {os.path.basename(path)}")
            QMessageBox.warning(self, "불러오지 못했습니다", str(e))
            return False
        self._show_result(result, path)
        return True

    def _show_result(self, result, path):
        self.result, self.path = result, path
        self._populate()
        self._apply_filter()
        self._update_summary()
        self.lbl_file.setText(f"{result.source}  ·  {result.fmt}  ·  {len(result.records)}종목")
        self.lbl_notes.setText("\n".join("• " + n for n in result.notes))
        self.lbl_notes.setVisible(bool(result.notes))
        self.stack.setCurrentIndex(1)
        self._update_buttons()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.load_file(url.toLocalFile())
                break

    # ------------------------------------------------------------ 표
    def _make_item(self, key, value, direction):
        text = kd.format_cell(key, value)
        if key in kd.TEXT_KEYS:
            item = QTableWidgetItem(text)
            if key in ("code", "market"):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return item
        item = NumItem(text, value)
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if key in ("change", "rate") and direction in ("up", "down"):
            item.setForeground(UP_COLOR if direction == "up" else DOWN_COLOR)
        return item

    def _populate(self):
        cols, records = self.result.columns, self.result.records
        t = self.table
        t.setSortingEnabled(False)                       # 채우는 동안 정렬되면 행이 뒤섞인다
        t.clearContents()
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels([kd.LABELS[k] for k in cols])
        t.setRowCount(len(records))
        for row, rec in enumerate(records):
            direction = kd.direction(rec)
            for c, key in enumerate(cols):
                item = self._make_item(key, rec.get(key), direction)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)      # 정렬로 행이 움직여도 원래 기록을 찾는 표식
                t.setItem(row, c, item)
        header = t.horizontalHeader()
        for c in range(len(cols)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(cols.index("name"), QHeaderView.ResizeMode.Stretch)
        t.setSortingEnabled(True)
        if "rank" in cols:                               # 순위가 있으면 순위 순
            t.sortByColumn(cols.index("rank"), Qt.SortOrder.AscendingOrder)
        elif "marcap" in cols:                           # 없으면 시가총액 큰 순
            t.sortByColumn(cols.index("marcap"), Qt.SortOrder.DescendingOrder)
        else:
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    def _record_at(self, row):
        return self.result.records[self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)]

    def _apply_filter(self, *_):
        if self.result is None:
            return
        needle = self.ed_search.text().strip().lower()
        wanted = self.cmb_filter.currentData()
        shown = 0
        for row in range(self.table.rowCount()):
            rec = self._record_at(row)
            ok = (not needle or needle in rec.get("name", "").lower() or needle in rec.get("code", "").lower())
            if ok and wanted != "all":
                ok = kd.direction(rec) == wanted
            self.table.setRowHidden(row, not ok)
            shown += ok
        self.lbl_shown.setText(f"표시 {shown} / 전체 {len(self.result.records)}")

    def visible_records(self):
        """지금 화면에 보이는 순서(정렬 반영)대로, 필터로 숨겨진 행은 빼고."""
        return [self._record_at(r) for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]

    # ------------------------------------------------------------ 요약
    def _update_summary(self):
        s = kd.summarize(self.result.records)
        parts = [f"종목 {s['total']}개",
                 f"<span style='color:{UP_HTML}'>상승 {s['up']}</span>",
                 f"<span style='color:{DOWN_HTML}'>하락 {s['down']}</span>",
                 f"보합 {s['flat']}"]
        if s["avg_rate"] is not None:
            parts.append(f"평균 등락률 {kd.fmt_rate(round(s['avg_rate'], 2))}")
        if s["marcap_sum"] is not None:
            parts.append(f"시가총액 합계 {kd.fmt_number(s['marcap_sum'])} (데이터의 단위 그대로)")
        lines = ["  ·  ".join(parts)]
        movers = []
        if s["top_up"]:
            movers.append(f"<span style='color:{UP_HTML}'>상승률 1위 {s['top_up']['name']} {kd.fmt_rate(s['top_up']['rate'])}</span>")
        if s["top_down"]:
            movers.append(f"<span style='color:{DOWN_HTML}'>하락률 1위 {s['top_down']['name']} {kd.fmt_rate(s['top_down']['rate'])}</span>")
        if movers:
            lines.append("  ·  ".join(movers))
        self.lbl_summary.setText("<br>".join(lines))

    # ------------------------------------------------------------ 저장
    def _save_dialog(self, kind):
        if self.result is None:
            return
        base = os.path.dirname(self.path) if self.path else os.path.dirname(os.path.abspath(__file__))
        path, _ = QFileDialog.getSaveFileName(self, "저장", os.path.join(base, f"kospi200_view.{kind}"), SAVE_FILTERS[kind])
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += "." + kind
        self.export_to(path)

    def export_to(self, path):
        records = self.visible_records()
        if not records:
            QMessageBox.information(self, "저장할 데이터 없음", "지금 화면에 보이는 종목이 없습니다. 검색/필터를 확인하세요.")
            return False
        try:
            kd.export(records, self.result.columns, path)
        except (OSError, RuntimeError) as e:
            QMessageBox.warning(self, "저장 실패", f"파일을 저장하지 못했습니다.\n{kd.save_error_text(e)}")
            return False
        self.lbl_file.setText(f"저장 완료: {path}  ({len(records)}종목, 화면에 보이는 그대로)")
        return True

    # ------------------------------------------------------------ 종료
    def closeEvent(self, event):
        if self._thread is None or not self._thread.isRunning():
            event.accept()
            return
        # 수집 스레드가 끝나기 전에 창을 닫으면 Qt 가 강제 종료된다. 중지를 요청하고 끝나면 프로그램을 마친다.
        if not self._closing:
            self._closing = True
            self.stop_collect()
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
