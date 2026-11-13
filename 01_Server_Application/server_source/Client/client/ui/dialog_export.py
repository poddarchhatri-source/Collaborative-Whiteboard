"""Export/import screen for PNG, PDF and portable JSON boards."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from client.services.exporter import ExportError, export_json, export_pdf, export_png, import_json
from client.ui.icons import icon, pixmap


class ExportImportDialog(QDialog):
    def __init__(self, view, model, manager, settings, parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.model = model
        self.manager = manager
        self.settings = settings
        self.setWindowTitle("Export & import")
        self.setModal(True)
        self.resize(680, 620)
        self._import_payload: dict[str, Any] | None = None
        self._import_path = ""
        role = (getattr(manager, "self_participant", {}) or {}).get("role", "viewer")
        self._role = role

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)
        header = QHBoxLayout()
        header_icon = QLabel()
        header_icon.setPixmap(pixmap("export", "#f4f4f5", 24))
        header.addWidget(header_icon)
        title = QLabel("Export & import")
        title.setProperty("role", "h2")
        header.addWidget(title)
        header.addStretch(1)
        outer.addLayout(header)
        subtitle = QLabel(
            "Export the current canvas at full fidelity, or import a SynCanvas JSON board. "
            "Imports are validated by the server and synchronized to every participant."
        )
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        # Export card -----------------------------------------------------
        export_card = QFrame()
        export_card.setProperty("role", "card")
        export_layout = QVBoxLayout(export_card)
        export_layout.setContentsMargins(16, 14, 16, 16)
        export_layout.setSpacing(10)
        export_title = QLabel("Export board")
        export_title.setProperty("role", "h2")
        export_layout.addWidget(export_title)
        row = QHBoxLayout()
        row.addWidget(QLabel("Format"))
        self.format = QComboBox()
        self.format.addItem("PNG image", "png")
        self.format.addItem("PDF document", "pdf")
        self.format.addItem("JSON board (portable)", "json")
        row.addWidget(self.format, 1)
        row.addWidget(QLabel("Background"))
        self.background = QComboBox()
        self.background.addItem("Board dark", "dark")
        self.background.addItem("Print white", "light")
        self.background.addItem("Transparent", "transparent")
        row.addWidget(self.background, 1)
        export_layout.addLayout(row)
        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Image scale"))
        self.scale = QComboBox()
        self.scale.addItem("1× (screen)", 1.0)
        self.scale.addItem("2× (high resolution)", 2.0)
        self.scale.addItem("3× (print)", 3.0)
        scale_row.addWidget(self.scale, 1)
        scale_row.addStretch(2)
        export_layout.addLayout(scale_row)
        path_row = QHBoxLayout()
        self.path = QLineEdit(self._default_path())
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(self.browse_button)
        export_layout.addLayout(path_row)
        self.format.currentIndexChanged.connect(self._format_changed)
        action_row = QHBoxLayout()
        self.export_button = QPushButton("Export now")
        self.export_button.setObjectName("primaryButton")
        self.export_button.setIcon(icon("export", "#09090b", 16))
        self.export_button.clicked.connect(self._export)
        action_row.addWidget(self.export_button)
        action_row.addStretch(1)
        self.open_folder = QPushButton("Open folder")
        self.open_folder.setObjectName("ghostButton")
        self.open_folder.clicked.connect(self._open_folder)
        action_row.addWidget(self.open_folder)
        export_layout.addLayout(action_row)
        self.export_status = QLabel("")
        self.export_status.setProperty("role", "faint")
        self.export_status.setWordWrap(True)
        export_layout.addWidget(self.export_status)
        outer.addWidget(export_card)

        # Import card -----------------------------------------------------
        import_card = QFrame()
        import_card.setProperty("role", "card")
        import_layout = QVBoxLayout(import_card)
        import_layout.setContentsMargins(16, 14, 16, 16)
        import_layout.setSpacing(10)
        import_title = QLabel("Import a saved board")
        import_title.setProperty("role", "h2")
        import_layout.addWidget(import_title)
        self.import_info = QLabel("Choose a SynCanvas JSON file to inspect it before replacing the current board.")
        self.import_info.setProperty("role", "muted")
        self.import_info.setWordWrap(True)
        import_layout.addWidget(self.import_info)
        import_row = QHBoxLayout()
        self.import_path = QLineEdit()
        self.import_path.setPlaceholderText("Path to board.json")
        choose = QPushButton("Choose file…")
        choose.clicked.connect(self._choose_import)
        import_row.addWidget(self.import_path, 1)
        import_row.addWidget(choose)
        import_layout.addLayout(import_row)
        import_action = QHBoxLayout()
        self.import_button = QPushButton("Import board")
        self.import_button.setObjectName("primaryButton")
        self.import_button.setIcon(icon("import", "#09090b", 16))
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._import)
        import_action.addWidget(self.import_button)
        import_action.addStretch(1)
        if self._role != "host":
            role_note = QLabel("Only the host can import/replace the board")
            role_note.setProperty("role", "faint")
            import_action.addWidget(role_note)
            self.import_button.setEnabled(False)
            choose.setEnabled(False)
        import_layout.addLayout(import_action)
        self.import_status = QLabel("")
        self.import_status.setProperty("role", "faint")
        self.import_status.setWordWrap(True)
        import_layout.addWidget(self.import_status)
        outer.addWidget(import_card)
        outer.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Close")
        outer.addWidget(buttons)

    # ------------------------------------------------------------------
    def _default_path(self, extension: str | None = None) -> str:
        ext = extension or self.format.currentData() if hasattr(self, "format") else "png"
        from client.config import default_export_dir

        directory = Path(self.settings.settings.export_directory or default_export_dir())
        code = getattr(self.manager, "session_code", "") or "board"
        safe_code = "".join(ch for ch in code if ch.isalnum()) or "board"
        stamp = time.strftime("%Y%m%d-%H%M")
        return str(directory / f"syncanvas-{safe_code}-{stamp}.{ext}")

    def _format_changed(self) -> None:
        fmt = self.format.currentData()
        if fmt in ("pdf", "json"):
            self.scale.setEnabled(False)
        else:
            self.scale.setEnabled(True)
        self.background.setEnabled(fmt != "json")
        current = Path(self.path.text())
        if current.suffix.lower() in (".png", ".pdf", ".json"):
            self.path.setText(str(current.with_suffix(f".{fmt}")))
        else:
            self.path.setText(self._default_path(fmt))

    def _browse(self) -> None:
        fmt = self.format.currentData()
        filters = {
            "png": "PNG image (*.png)",
            "pdf": "PDF document (*.pdf)",
            "json": "SynCanvas board (*.json)",
        }
        path, _ = QFileDialog.getSaveFileName(self, "Export board", self.path.text(), filters.get(fmt, "All files"))
        if path:
            self.path.setText(path)

    def _export(self) -> None:
        path = self.path.text().strip()
        if not path:
            QMessageBox.warning(self, "Export", "Choose an output path first.")
            return
        fmt = self.format.currentData()
        background = self.background.currentData()
        try:
            self.export_button.setEnabled(False)
            self.export_status.setText("Rendering…")
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
            if fmt == "png":
                output = export_png(
                    self.view.scene(),
                    path,
                    background=background,
                    scale=float(self.scale.currentData()),
                )
            elif fmt == "pdf":
                output = export_pdf(
                    self.view.scene(),
                    path,
                    background="light" if background == "light" else "dark",
                    title=f"SynCanvas · {self.manager.session_name}",
                )
            else:
                output = export_json(
                    path,
                    self.model.snapshot(),
                    meta={
                        "session_code": self.manager.session_code,
                        "session_name": self.manager.session_name,
                        "exported_by": (self.manager.self_participant or {}).get("name", ""),
                    },
                )
            self.path.setText(str(output))
            self.export_status.setText(f"Exported to {output}")
            QMessageBox.information(self, "Export complete", f"Board exported successfully:\n{output}")
        except (ExportError, OSError, ValueError) as exc:
            self.export_status.setText(f"Export failed: {exc}")
            QMessageBox.critical(self, "Export failed", str(exc))
        finally:
            self.export_button.setEnabled(True)

    def _open_folder(self) -> None:
        path = Path(self.path.text())
        directory = path.parent if path.parent.exists() else Path.cwd() / "exports"
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _choose_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open board JSON", str(Path.cwd()), "SynCanvas board (*.json);;JSON (*.json)")
        if not path:
            return
        self.import_path.setText(path)
        try:
            payload = import_json(path)
        except ExportError as exc:
            self._import_payload = None
            self.import_button.setEnabled(False)
            self.import_status.setText(str(exc))
            return
        self._import_payload = payload
        self._import_path = path
        objects = payload["objects"]
        meta = payload.get("meta", {})
        source = meta.get("session_name") or payload.get("application") or "SynCanvas board"
        self.import_info.setText(
            f"Ready to import: {len(objects)} object(s) from “{source}”. "
            "The current board will be snapshotted as a safety version before import."
        )
        self.import_status.setText(f"Validated {path}")
        self.import_button.setEnabled(self._role == "host")

    def _import(self) -> None:
        if not self._import_payload or self._role != "host":
            return
        objects = self._import_payload.get("objects", [])
        answer = QMessageBox.question(
            self,
            "Replace current board?",
            f"Import {len(objects)} object(s) and replace the current canvas for everyone?\n\n"
            "A safety snapshot will be saved first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.manager.send("BOARD_IMPORT", {"objects": objects})
        self.import_status.setText("Import sent to the server…")
        self.import_button.setEnabled(False)
