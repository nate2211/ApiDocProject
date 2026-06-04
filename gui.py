from __future__ import annotations

import json
import os
import re
import shlex
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from PyQt5.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import blocks
import pipeline  # noqa: F401  # importing registers pipeline/block providers

APP_TITLE = "PromptChat APIDoc Builder"

DARK = """
QMainWindow, QWidget, QTabWidget { background-color: #252525; color: #dddddd; }
QPlainTextEdit, QLineEdit, QComboBox, QSpinBox { background-color: #1e1e1e; color: #eeeeee; border: 1px solid #555555; selection-background-color: #555555; }
QPushButton { background-color: #444444; color: #eeeeee; border: 1px solid #666666; padding: 6px 12px; border-radius: 4px; font-weight: bold; }
QPushButton:hover { background-color: #555555; }
QPushButton:disabled { background-color: #333333; color: #777777; }
QGroupBox { border: 1px solid #555555; margin-top: 10px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px 0 4px; }
QTabBar::tab { background: #3a3a3a; color: #dddddd; padding: 8px 14px; }
QTabBar::tab:selected { background: #555555; }
"""

DEFAULT_QUERIES = """# APIDoc queries. Direct mode ON uses exact/catalog URLs first.
# Direct mode OFF resolves each line with the search-query resolver first.
python: pathlib Path methods
python: asyncio Task cancellation
python-packages: requests Session request headers timeout API
python-packages: django model QuerySet API
csharp: C# async await Task documentation
dotnet: System.Net.Http.HttpClient SendAsync API
windows: Win32 CreateFile ReadFile WriteFile API
cpp: std::vector push_back reserve documentation
cpp: C++ templates concepts constexpr documentation
cpp: CMake target_link_libraries find_package documentation
web: MDN Fetch API WebSocket Canvas documentation
javascript: Promise async function Array map documentation
node: Node.js fs stream crypto worker_threads API
rust: std::vec::Vec Option Result ownership documentation
go: net/http context sync API documentation
java: java.util.HashMap java.net.http.HttpClient API
kotlin: coroutines Flow standard library API
swift: Swift async actor Foundation API
game: Unity GameObject MonoBehaviour Rigidbody API
game: Unreal Actor UObject Blueprint API
bannerlord: TaleWorlds.CampaignSystem MobileParty Hero CampaignBehaviorBase API
database: PostgreSQL SQLite Redis command API
devops: Dockerfile Kubernetes API GitHub Actions workflow syntax
cloud: AWS S3 Lambda API Azure REST GCP Compute API
ai: OpenAI API reference PyTorch TensorFlow OpenCV docs
linux: Linux socket epoll ioctl man pages
monero: get_block_template RPC documentation
monero: XMRig RandomX huge pages documentation
monero: P2Pool mini shares payout documentation
url: https://docs.python.org/3/library/pathlib.html
"""


class APIDocWorker(QObject):
    finished = pyqtSignal(bool, str, dict)
    status = pyqtSignal(str)

    def __init__(self, query_text: str, params: Dict[str, Any], stop_event: threading.Event):
        super().__init__()
        self.query_text = query_text
        self.params = params
        self.stop_event = stop_event

    @pyqtSlot()
    def run(self) -> None:
        try:
            ensure_dirs = getattr(blocks, "ensure_app_dirs", None)
            if callable(ensure_dirs):
                ensure_dirs()

            def progress(msg: str) -> None:
                if self.stop_event.is_set():
                    raise RuntimeError("cancelled")
                self.status.emit(str(msg))

            engine = blocks.APIDocEngine(params=self.params, progress=progress)
            markdown, bundle, meta = engine.run_all(self.query_text)
            if not isinstance(bundle, dict):
                bundle = {}
            if not isinstance(meta, dict):
                meta = {"raw_meta": meta}
            meta["query_count"] = len(bundle.get("queries", []))
            meta["doc_count"] = len(bundle.get("docs", []))
            self.finished.emit(True, str(markdown), meta)
        except Exception as e:
            tb = traceback.format_exc()
            self.finished.emit(False, f"{e}\n\n{tb}", {"error": repr(e), "traceback": tb})


class APIDocWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1360, 880)
        self.stop_event = threading.Event()
        self.thread: Optional[QThread] = None
        self.worker: Optional[APIDocWorker] = None
        self.build_ui()

    def build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        header = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.addItems(
            [
                "all",
                "python",
                "python-packages",
                "csharp",
                "dotnet",
                "cpp",
                "windows",
                "web",
                "javascript",
                "node",
                "rust",
                "go",
                "java",
                "kotlin",
                "swift",
                "game",
                "bannerlord",
                "database",
                "devops",
                "cloud",
                "ai",
                "linux",
                "monero",
            ]
        )
        self.out_path = QLineEdit(str(Path("out") / "apidocs.md"))

        browse_out = QPushButton("Browse Output")
        browse_out.clicked.connect(self.browse_output)
        self.run_btn = QPushButton("Run Direct APIDoc Request")
        self.run_btn.clicked.connect(self.run_apidoc)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)

        header.addWidget(QLabel("Profile:"))
        header.addWidget(self.profile)
        header.addWidget(QLabel("Markdown Output:"))
        header.addWidget(self.out_path, 1)
        header.addWidget(browse_out)
        header.addWidget(self.run_btn)
        header.addWidget(self.cancel_btn)
        root.addLayout(header)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        qgroup = QGroupBox("Query File / Query Text")
        qlayout = QVBoxLayout(qgroup)
        frow = QHBoxLayout()
        self.query_file = QLineEdit("")
        load_btn = QPushButton("Load .txt")
        load_btn.clicked.connect(self.load_query_file)
        save_btn = QPushButton("Save .txt")
        save_btn.clicked.connect(self.save_query_file)
        frow.addWidget(QLabel("Query file:"))
        frow.addWidget(self.query_file, 1)
        frow.addWidget(load_btn)
        frow.addWidget(save_btn)
        qlayout.addLayout(frow)

        self.query_text = QPlainTextEdit()
        self.query_text.setPlainText(DEFAULT_QUERIES)
        qlayout.addWidget(self.query_text)
        left_layout.addWidget(qgroup, 4)

        opts = QGroupBox("Direct Fetch Options")
        form = QFormLayout(opts)

        self.max_pages = QSpinBox()
        self.max_pages.setRange(1, 80)
        self.max_pages.setValue(5)

        self.max_direct = QSpinBox()
        self.max_direct.setRange(1, 200)
        self.max_direct.setValue(18)

        self.max_chars = QSpinBox()
        self.max_chars.setRange(500, 250000)
        self.max_chars.setSingleStep(1000)
        self.max_chars.setValue(18000)

        self.timeout = QSpinBox()
        self.timeout.setRange(2, 120)
        self.timeout.setValue(20)

        self.direct_mode = QCheckBox("Direct mode: resolve exact/catalog docs URLs first")
        self.direct_mode.setChecked(True)
        self.search_fallback = QCheckBox("Direct mode fallback: search only if direct URLs fail")
        self.search_fallback.setChecked(False)
        self.direct_fallback_after_search = QCheckBox("Search mode fallback: try direct catalog only if search finds nothing")
        self.direct_fallback_after_search.setChecked(False)
        self.crawl_direct = QCheckBox("Crawl links only inside the same resolved docs site")
        self.crawl_direct.setChecked(True)
        self.use_cache = QCheckBox("Use local HTML/Markdown cache")
        self.use_cache.setChecked(True)
        self.include_text = QCheckBox("Include extracted text in Markdown")
        self.include_text.setChecked(True)

        self.config_path = QLineEdit("")
        cfg_btn = QPushButton("Browse Config JSON")
        cfg_btn.clicked.connect(self.browse_config)
        cfg_row = QHBoxLayout()
        cfg_row.addWidget(self.config_path, 1)
        cfg_row.addWidget(cfg_btn)

        form.addRow("Max pages per query:", self.max_pages)
        form.addRow("Max direct URLs/query:", self.max_direct)
        form.addRow("Max chars per page:", self.max_chars)
        form.addRow("Timeout seconds:", self.timeout)
        form.addRow("", self.direct_mode)
        form.addRow("", self.search_fallback)
        form.addRow("", self.direct_fallback_after_search)
        form.addRow("", self.crawl_direct)
        form.addRow("", self.use_cache)
        form.addRow("", self.include_text)
        form.addRow("Optional config:", cfg_row)
        left_layout.addWidget(opts, 1)
        splitter.addWidget(left)

        right = QWidget()
        rlayout = QVBoxLayout(right)
        self.tabs = QTabWidget()
        self.preview = QPlainTextEdit()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.meta = QPlainTextEdit()
        self.meta.setReadOnly(True)
        self.tabs.addTab(self.preview, "Markdown Preview")
        self.tabs.addTab(self.log, "Run Log")
        self.tabs.addTab(self.meta, "Metadata")
        rlayout.addWidget(self.tabs)

        act = QHBoxLayout()
        open_btn = QPushButton("Open Output Folder")
        open_btn.clicked.connect(self.open_output_folder)
        cmd_btn = QPushButton("Show CLI Command")
        cmd_btn.clicked.connect(self.show_cli_command)
        cat_btn = QPushButton("Preview Link Catalog")
        cat_btn.clicked.connect(self.preview_catalog)
        act.addWidget(open_btn)
        act.addWidget(cmd_btn)
        act.addWidget(cat_btn)
        act.addStretch(1)
        rlayout.addLayout(act)

        splitter.addWidget(right)
        splitter.setSizes([580, 780])
        root.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.direct_mode.toggled.connect(self.sync_mode_ui)
        self.sync_mode_ui(self.direct_mode.isChecked())

    def params(self) -> Dict[str, Any]:
        direct_enabled = self.direct_mode.isChecked()
        p: Dict[str, Any] = {
            "profile": self.profile.currentText(),
            "out_path": self.out_path.text().strip(),
            "query_file": self.query_file.text().strip(),
            "max_pages_per_query": self.max_pages.value(),
            "max_direct_urls_per_query": self.max_direct.value(),
            "max_chars_per_page": self.max_chars.value(),
            "timeout": self.timeout.value(),
            "direct_mode": direct_enabled,
            "crawl_direct_pages": self.crawl_direct.isChecked(),
            "use_cache": self.use_cache.isChecked(),
            "include_text": self.include_text.isChecked(),
        }

        if direct_enabled:
            p["search_mode"] = "direct_first"
            p["search_fallback"] = self.search_fallback.isChecked()
            p["allow_search_in_safe_mode"] = self.search_fallback.isChecked()
            p["direct_fallback_when_search_fails"] = False
        else:
            # This is the important path: Direct mode OFF means APIDoc resolves
            # docs from the query search resolver first, even in crash-safe mode.
            p["search_mode"] = "search_first"
            p["search_fallback"] = True
            p["allow_search_in_safe_mode"] = True
            p["direct_fallback_when_search_fails"] = self.direct_fallback_after_search.isChecked()

        if self.config_path.text().strip():
            p["config_path"] = self.config_path.text().strip()
        return p

    def sync_mode_ui(self, direct_enabled: bool) -> None:
        if direct_enabled:
            self.run_btn.setText("Run Direct APIDoc Request")
            self.search_fallback.setEnabled(True)
            self.direct_fallback_after_search.setEnabled(False)
            self.statusBar().showMessage(
                "Ready. Direct mode ON: catalog/exact docs URLs resolve first; optional search fallback runs only on misses."
            )
        else:
            self.run_btn.setText("Run Search APIDoc Request")
            self.search_fallback.setEnabled(False)
            self.search_fallback.setChecked(True)
            self.direct_fallback_after_search.setEnabled(True)
            self.statusBar().showMessage(
                "Ready. Direct mode OFF: each APIDoc line resolves with the search-query resolver first."
            )

    def browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Markdown Output",
            self.out_path.text() or "apidocs_direct.md",
            "Markdown (*.md);;All Files (*)",
        )
        if path:
            if not path.lower().endswith(".md"):
                path += ".md"
            self.out_path.setText(path)

    def load_query_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Query Text File", "", "Text Files (*.txt);;Markdown (*.md);;All Files (*)"
        )
        if not path:
            return
        self.query_file.setText(path)
        try:
            self.query_text.setPlainText(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, APP_TITLE, f"Could not read query file:\n{e}")

    def save_query_file(self) -> None:
        start = self.query_file.text().strip() or "queries_direct.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Query Text File", start, "Text Files (*.txt);;Markdown (*.md);;All Files (*)"
        )
        if not path:
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(self.query_text.toPlainText(), encoding="utf-8")
            self.query_file.setText(path)
        except Exception as e:
            QMessageBox.warning(self, APP_TITLE, f"Could not save query file:\n{e}")

    def browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open APIDoc Config JSON", "", "JSON Files (*.json);;All Files (*)"
        )
        if path:
            self.config_path.setText(path)

    def append_log(self, msg: str) -> None:
        self.log.appendPlainText(str(msg))
        self.log.moveCursor(QTextCursor.End)
        self.statusBar().showMessage(str(msg))

    def run_apidoc(self) -> None:
        if self.thread is not None:
            return
        self.stop_event.clear()
        self.preview.clear()
        self.log.clear()
        self.meta.clear()

        self.thread = QThread(self)
        self.worker = APIDocWorker(self.query_text.toPlainText(), self.params(), self.stop_event)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.status.connect(self.append_log)
        self.worker.finished.connect(self.finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread_done)

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.append_log("[gui] starting APIDoc request in " + ("direct-first" if self.direct_mode.isChecked() else "search-first") + " mode")
        self.thread.start()

    def cancel(self) -> None:
        self.stop_event.set()
        self.append_log("[gui] cancel requested")

    @pyqtSlot(bool, str, dict)
    def finished(self, ok: bool, text: str, meta: dict) -> None:
        self.preview.setPlainText(text)
        self.meta.setPlainText(json.dumps(meta, indent=2, ensure_ascii=False, default=str))
        self.tabs.setCurrentWidget(self.preview if ok else self.meta)
        self.append_log("[gui] completed" if ok else "[gui] failed")

    def thread_done(self) -> None:
        self.thread = None
        self.worker = None
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def open_output_folder(self) -> None:
        out = Path(self.out_path.text().strip() or "out/apidocs.md")
        folder = out.parent if out.suffix else out
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')
        except Exception as e:
            QMessageBox.warning(self, APP_TITLE, f"Could not open folder:\n{e}")

    def show_cli_command(self) -> None:
        p = self.params()
        parts: List[str] = ["python", "main.py", "apidoc"]
        for k, v in p.items():
            if v in ("", None):
                continue
            parts.extend(["--extra", f"apidoc.{k}={v}"])

        if os.name == "nt":
            cmd = self._windows_join(parts)
        else:
            cmd = " ".join(shlex.quote(str(x)) for x in parts)

        self.log.appendPlainText("\n[CLI]\n" + cmd + "\n")
        self.tabs.setCurrentWidget(self.log)

    def preview_catalog(self) -> None:
        """
        Show the direct-link catalog without running a full APIDoc fetch.

        The previous GUI wired the Preview Link Catalog button to this method, but
        the method was missing, causing:
            AttributeError: 'APIDocWindow' object has no attribute 'preview_catalog'

        This implementation is intentionally tolerant. Different versions of your
        project have exposed the catalog as module constants, module functions,
        engine methods, engine attributes, or JSON config keys. This checks all of
        those locations and then falls back to direct url: lines in the query box.
        """
        try:
            sources: List[Tuple[str, Any]] = []

            config_catalog = self._catalog_from_config()
            if config_catalog is not None:
                sources.append(("config JSON", config_catalog))

            for name, value in self._catalog_candidates_from_blocks():
                sources.append((f"blocks.{name}", value))

            engine_catalog = self._catalog_from_engine()
            if engine_catalog is not None:
                sources.append(("APIDocEngine", engine_catalog))

            query_urls = self._direct_urls_from_query_text()
            if query_urls:
                sources.append(("query text url: lines", query_urls))

            if not sources:
                text = (
                    "# Link Catalog Preview\n\n"
                    "No direct-link catalog was discovered.\n\n"
                    "Checked config JSON, common blocks module catalog names, common catalog functions, "
                    "APIDocEngine catalog fields/methods, and `url:` lines in the query box.\n\n"
                    "To make this button show your built-in catalog, expose it from `blocks.py` as one of these names:\n\n"
                    "- `LINK_CATALOG`\n"
                    "- `DIRECT_LINK_CATALOG`\n"
                    "- `DOC_LINK_CATALOG`\n"
                    "- `API_DOC_CATALOG`\n"
                    "- `get_link_catalog()`\n"
                    "- `get_direct_link_catalog()`\n"
                )
            else:
                chunks = ["# Link Catalog Preview", ""]
                seen: set[str] = set()
                for source_name, catalog in sources:
                    formatted = self._format_catalog(catalog, source_name)
                    if formatted and formatted not in seen:
                        chunks.append(formatted)
                        chunks.append("")
                        seen.add(formatted)
                text = "\n".join(chunks).rstrip() + "\n"

            self.preview.setPlainText(text)
            self.meta.setPlainText(
                json.dumps(
                    {
                        "catalog_sources_checked": True,
                        "catalog_sources_found": [name for name, _ in sources],
                        "profile": self.profile.currentText(),
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )
            self.tabs.setCurrentWidget(self.preview)
            self.append_log(f"[gui] catalog preview ready ({len(sources)} source(s))")
        except Exception as e:
            tb = traceback.format_exc()
            self.preview.setPlainText(f"Catalog preview failed:\n{e}\n\n{tb}")
            self.meta.setPlainText(json.dumps({"error": repr(e), "traceback": tb}, indent=2))
            self.tabs.setCurrentWidget(self.preview)
            self.append_log("[gui] catalog preview failed")

    def _catalog_from_config(self) -> Optional[Any]:
        path_text = self.config_path.text().strip()
        if not path_text:
            return None
        path = Path(path_text)
        if not path.exists() or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return self._pick_catalog_from_mapping(data)

    def _catalog_candidates_from_blocks(self) -> Iterable[Tuple[str, Any]]:
        constant_names = [
            "LINK_CATALOG",
            "DIRECT_LINK_CATALOG",
            "DOC_LINK_CATALOG",
            "API_DOC_CATALOG",
            "DIRECT_DOC_CATALOG",
            "DEFAULT_LINK_CATALOG",
            "DEFAULT_DIRECT_CATALOG",
            "DIRECT_DOCS",
            "DOCS",
            "CATALOG",
            "CATALOGS",
        ]
        for name in constant_names:
            if hasattr(blocks, name):
                value = getattr(blocks, name)
                if value:
                    yield name, value

        function_names = [
            "get_link_catalog",
            "get_direct_link_catalog",
            "get_doc_link_catalog",
            "get_api_doc_catalog",
            "build_link_catalog",
            "build_direct_link_catalog",
            "load_link_catalog",
            "load_direct_catalog",
            "default_catalog",
        ]
        for name in function_names:
            fn = getattr(blocks, name, None)
            if not callable(fn):
                continue
            value = self._safe_call_catalog_function(fn)
            if value:
                yield f"{name}()", value

    def _catalog_from_engine(self) -> Optional[Any]:
        engine_cls = getattr(blocks, "APIDocEngine", None)
        if engine_cls is None:
            return None
        try:
            engine = engine_cls(params=self.params(), progress=lambda msg: None)
        except Exception:
            return None

        names = [
            "link_catalog",
            "direct_link_catalog",
            "doc_link_catalog",
            "api_doc_catalog",
            "direct_catalog",
            "catalog",
            "catalogs",
            "direct_docs",
            "docs",
        ]
        for name in names:
            if not hasattr(engine, name):
                continue
            value = getattr(engine, name)
            if callable(value):
                value = self._safe_call_catalog_function(value)
            if value:
                return value

        method_names = [
            "get_link_catalog",
            "get_direct_link_catalog",
            "build_link_catalog",
            "load_link_catalog",
            "preview_catalog",
        ]
        for name in method_names:
            fn = getattr(engine, name, None)
            if not callable(fn):
                continue
            value = self._safe_call_catalog_function(fn)
            if value:
                return value
        return None

    def _safe_call_catalog_function(self, fn: Callable[..., Any]) -> Optional[Any]:
        # Try the safest/no-arg form first, then common profile/params forms.
        attempts = [
            (),
            (self.profile.currentText(),),
            (self.params(),),
        ]
        for args in attempts:
            try:
                value = fn(*args)
            except TypeError:
                continue
            except Exception:
                continue
            if value:
                return value
        return None

    def _pick_catalog_from_mapping(self, data: Any) -> Optional[Any]:
        if not isinstance(data, dict):
            return data if data else None
        keys = [
            "link_catalog",
            "direct_link_catalog",
            "doc_link_catalog",
            "api_doc_catalog",
            "direct_catalog",
            "catalog",
            "catalogs",
            "docs",
            "direct_docs",
            "sources",
        ]
        for key in keys:
            value = data.get(key)
            if value:
                return value
        return data if data else None

    def _direct_urls_from_query_text(self) -> List[str]:
        urls: List[str] = []
        seen: set[str] = set()
        for raw in self.query_text.toPlainText().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"(?i)^url\s*:\s*(https?://\S+)", line)
            if not match:
                continue
            url = match.group(1).strip().rstrip(")],.;")
            if url and url not in seen:
                urls.append(url)
                seen.add(url)
        return urls

    def _format_catalog(self, catalog: Any, source_name: str) -> str:
        profile = self.profile.currentText().strip().lower()
        lines = [f"## Source: `{source_name}`", ""]

        if isinstance(catalog, dict):
            filtered = self._filter_catalog_dict(catalog, profile)
            for key in sorted(filtered.keys(), key=lambda x: str(x).lower()):
                value = filtered[key]
                lines.append(f"### {key}")
                self._append_catalog_value(lines, value)
                lines.append("")
            return "\n".join(lines).rstrip()

        if isinstance(catalog, (list, tuple, set)):
            values = list(catalog)
            if values and all(isinstance(x, dict) for x in values):
                for item in values:
                    title = item.get("name") or item.get("title") or item.get("profile") or item.get("key") or "entry"
                    lines.append(f"### {title}")
                    self._append_catalog_value(lines, item)
                    lines.append("")
            else:
                for item in values:
                    lines.append(f"- {item}")
            return "\n".join(lines).rstrip()

        if isinstance(catalog, str):
            return "\n".join(lines + [catalog]).rstrip()

        return "\n".join(lines + ["```json", json.dumps(catalog, indent=2, ensure_ascii=False, default=str), "```"]).rstrip()

    def _filter_catalog_dict(self, catalog: Dict[Any, Any], profile: str) -> Dict[Any, Any]:
        if profile in ("", "all"):
            return dict(catalog)

        # If the catalog is keyed by profile, show the selected profile plus any common/default bucket.
        lower_map = {str(k).lower(): k for k in catalog.keys()}
        wanted_keys = []
        for candidate in (profile, "common", "default", "all"):
            real_key = lower_map.get(candidate)
            if real_key is not None:
                wanted_keys.append(real_key)
        if wanted_keys:
            return {k: catalog[k] for k in wanted_keys}

        # Otherwise keep the full dictionary. It may be keyed by URL/domain rather than profile.
        return dict(catalog)

    def _append_catalog_value(self, lines: List[str], value: Any) -> None:
        if isinstance(value, dict):
            preferred_url_keys = ("url", "href", "link", "docs", "documentation", "api", "source")
            simple_pairs: List[str] = []
            nested_pairs: List[Tuple[str, Any]] = []
            for k, v in value.items():
                if isinstance(v, (dict, list, tuple, set)):
                    nested_pairs.append((str(k), v))
                elif str(k).lower() in preferred_url_keys and self._looks_like_url(str(v)):
                    simple_pairs.insert(0, f"- **{k}:** {v}")
                else:
                    simple_pairs.append(f"- **{k}:** {v}")
            lines.extend(simple_pairs or ["- {}"])
            for k, v in nested_pairs:
                lines.append(f"- **{k}:**")
                self._append_indented_value(lines, v, indent="  ")
            return

        if isinstance(value, (list, tuple, set)):
            values = list(value)
            if not values:
                lines.append("- []")
            for item in values:
                if isinstance(item, dict):
                    compact = self._compact_dict_item(item)
                    lines.append(f"- {compact}")
                else:
                    lines.append(f"- {item}")
            return

        lines.append(f"- {value}")

    def _append_indented_value(self, lines: List[str], value: Any, indent: str = "  ") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (dict, list, tuple, set)):
                    lines.append(f"{indent}- **{k}:**")
                    self._append_indented_value(lines, v, indent + "  ")
                else:
                    lines.append(f"{indent}- **{k}:** {v}")
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, dict):
                    lines.append(f"{indent}- {self._compact_dict_item(item)}")
                else:
                    lines.append(f"{indent}- {item}")
            return
        lines.append(f"{indent}- {value}")

    def _compact_dict_item(self, item: Dict[Any, Any]) -> str:
        name = item.get("name") or item.get("title") or item.get("label") or item.get("profile")
        url = item.get("url") or item.get("href") or item.get("link")
        if name and url:
            return f"**{name}:** {url}"
        if url:
            return str(url)
        return "`" + json.dumps(item, ensure_ascii=False, default=str) + "`"

    def _looks_like_url(self, text: str) -> bool:
        return text.startswith("http://") or text.startswith("https://")

    def _windows_join(self, parts: List[str]) -> str:
        def quote(part: str) -> str:
            s = str(part)
            if not s:
                return '""'
            if re.search(r"[\s\"&|<>^]", s):
                return '"' + s.replace('"', '\\"') + '"'
            return s

        return " ".join(quote(x) for x in parts)

    def closeEvent(self, event: Any) -> None:  # Qt close event
        if self.thread is not None:
            self.stop_event.set()
            self.thread.quit()
            self.thread.wait(1500)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyleSheet(DARK)
    w = APIDocWindow()
    w.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())

