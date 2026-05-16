"""drumKAT 3.8 SysEx Visual Editor"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QSplitter, QListWidget, QLabel, QLineEdit,
    QSpinBox, QComboBox, QGroupBox, QScrollArea, QSizePolicy,
    QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction

from drumkat38 import (
    Kit, load_kit, save_kit,
    SURFACES, MODE_NAMES, CONTROL_TYPE_NAMES, GATE_TABLE,
    note_name, NUM_SURFACES,
)


# ---------------------------------------------------------------------------
# Mode groupings
# ---------------------------------------------------------------------------

MODES_4_NOTES = {1, 2, 5, 11, 12, 14, 15}   # Multiple, Alternate, Vel Shift, Melodic*
MODES_8_NOTES = {9, 10, 13}                  # Alternate 8, Random 8, Melodic Random


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gate_labels() -> list[str]:
    labels = ['Latch']
    for i in range(1, 254):
        v = GATE_TABLE[i]
        labels.append(f'{v:.3f}s' if v is not None else f'?({i})')
    labels.append('No OFF')
    return labels


GATE_LABELS = _gate_labels()
VEL_MIN_VALUES = list(range(0, 121, 8))   # 0, 8, 16, ..., 120
VEL_MAX_VALUES = list(range(7, 128, 8))   # 7, 15, 23, ..., 127


# ---------------------------------------------------------------------------
# Surface parameter panel
# ---------------------------------------------------------------------------

class SurfacePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._surface = None
        self._loading = False
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_note_spin():
        spin = QSpinBox()
        spin.setRange(0, 127)
        spin.setFixedWidth(60)
        return spin

    @staticmethod
    def _make_note_label():
        lbl = QLabel('---')
        lbl.setMinimumWidth(44)
        return lbl

    @staticmethod
    def _note_widget(spin, label):
        """Wrap a note spinbox + name label in a single widget."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(spin)
        h.addWidget(label)
        h.addStretch()
        return w

    def _add_note_row(self, form: QFormLayout, label: str, mapped: bool):
        """Create one note row; return (spin, name_label, row_widget)."""
        spin = self._make_note_spin()
        lbl  = self._make_note_label()
        row  = self._note_widget(spin, lbl)
        if not mapped:
            spin.setEnabled(False)
            spin.setToolTip('Block position not yet mapped — provide a dump to confirm')
            lbl.setText('TBD')
        form.addRow(label, row)
        return spin, lbl, row

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.setSpacing(8)

        # ---- Main parameter form ----
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setVerticalSpacing(6)

        # Mode
        self.w_mode = QComboBox()
        self.w_mode.addItems(MODE_NAMES)
        self.w_mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow('Mode:', self.w_mode)

        # Channel
        self.w_channel = QSpinBox()
        self.w_channel.setRange(1, 16)
        self.w_channel.valueChanged.connect(self._on_channel_changed)
        form.addRow('MIDI Channel:', self.w_channel)

        # Curve
        self.w_curve = QSpinBox()
        self.w_curve.setRange(1, 16)
        self.w_curve.valueChanged.connect(self._on_curve_changed)
        form.addRow('Velocity Curve:', self.w_curve)

        # Note 1
        self.w_note  = self._make_note_spin()
        self.l_note  = self._make_note_label()
        self.w_note.valueChanged.connect(self._on_note_changed)
        form.addRow('Note 1:', self._note_widget(self.w_note, self.l_note))

        # Vel Min / Max
        self.w_vel_min = QComboBox()
        self.w_vel_min.addItems([str(v) for v in VEL_MIN_VALUES])
        self.w_vel_min.currentIndexChanged.connect(self._on_vel_min_changed)
        form.addRow('Vel Min:', self.w_vel_min)

        self.w_vel_max = QComboBox()
        self.w_vel_max.addItems([str(v) for v in VEL_MAX_VALUES])
        self.w_vel_max.currentIndexChanged.connect(self._on_vel_max_changed)
        form.addRow('Vel Max:', self.w_vel_max)

        # Gate
        self.w_gate = QComboBox()
        self.w_gate.addItems(GATE_LABELS)
        self.w_gate.setMaxVisibleItems(16)
        self.w_gate.currentIndexChanged.connect(self._on_gate_changed)
        form.addRow('Gate Time:', self.w_gate)

        # Control Type (Control mode only)
        self.w_control_type = QComboBox()
        self.w_control_type.addItems(CONTROL_TYPE_NAMES)
        self.w_control_type.currentIndexChanged.connect(self._on_control_type_changed)
        self.l_control_type = QLabel('Control Type:')
        form.addRow(self.l_control_type, self.w_control_type)

        # Link
        self.w_link = QComboBox()
        self.w_link.currentIndexChanged.connect(self._on_link_changed)
        form.addRow('Link To:', self.w_link)

        root.addLayout(form)

        # ---- Extra notes group ----
        self.notes_group = QGroupBox('Additional Notes')
        nf = QFormLayout(self.notes_group)
        nf.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        nf.setVerticalSpacing(6)

        self.w_note2, self.l_note2, _ = self._add_note_row(nf, 'Note 2:', True)
        self.w_note3, self.l_note3, _ = self._add_note_row(nf, 'Note 3:', True)
        self.w_note4, self.l_note4, _ = self._add_note_row(nf, 'Note 4:', True)
        self.w_note5, self.l_note5, self.row_note5 = self._add_note_row(nf, 'Note 5:', True)
        self.w_note6, self.l_note6, self.row_note6 = self._add_note_row(nf, 'Note 6:', True)
        self.w_note7, self.l_note7, self.row_note7 = self._add_note_row(nf, 'Note 7:', True)
        self.w_note8, self.l_note8, self.row_note8 = self._add_note_row(nf, 'Note 8:', True)

        self.w_note2.valueChanged.connect(self._on_note2_changed)
        self.w_note3.valueChanged.connect(self._on_note3_changed)
        self.w_note4.valueChanged.connect(self._on_note4_changed)
        self.w_note5.valueChanged.connect(self._on_note5_changed)
        self.w_note6.valueChanged.connect(self._on_note6_changed)
        self.w_note7.valueChanged.connect(self._on_note7_changed)
        self.w_note8.valueChanged.connect(self._on_note8_changed)

        root.addWidget(self.notes_group)
        self.notes_group.hide()

        self.setEnabled(False)

    # ------------------------------------------------------------------
    # Load surface into widgets
    # ------------------------------------------------------------------

    def load_surface(self, surface):
        self._surface = surface
        self._loading = True
        self.setEnabled(True)

        self.w_mode.setCurrentIndex(surface.mode)
        self.w_channel.setValue(surface.channel)
        self.w_curve.setValue(surface.curve)
        self._set_note(self.w_note, self.l_note, surface.note)
        self.w_vel_min.setCurrentIndex(VEL_MIN_VALUES.index(surface.vel_min))
        self.w_vel_max.setCurrentIndex(VEL_MAX_VALUES.index(surface.vel_max))
        self.w_gate.setCurrentIndex(surface.gate)

        # Link combo — rebuild excluding self
        self.w_link.blockSignals(True)
        self.w_link.clear()
        self.w_link.addItem('None', userData=None)
        for i, name in enumerate(SURFACES):
            if i != surface.index:
                self.w_link.addItem(name, userData=i)
        link = surface.link
        for j in range(self.w_link.count()):
            if self.w_link.itemData(j) == link:
                self.w_link.setCurrentIndex(j)
                break
        self.w_link.blockSignals(False)

        # Extra notes
        self._set_note(self.w_note2, self.l_note2, surface.note2)
        self._set_note(self.w_note3, self.l_note3, surface.note3)
        self._set_note(self.w_note4, self.l_note4, surface.note4)
        self._set_note(self.w_note5, self.l_note5, surface.note5)
        self._set_note(self.w_note6, self.l_note6, surface.note6)
        self._set_note(self.w_note7, self.l_note7, surface.note7)
        self._set_note(self.w_note8, self.l_note8, surface.note8)

        # Control type (only meaningful in Control mode)
        ct = surface.control_type
        self.w_control_type.setCurrentIndex(ct if 0 <= ct < len(CONTROL_TYPE_NAMES) else 0)

        self._refresh_mode_widgets()
        self._loading = False

    def clear(self):
        self._surface = None
        self.setEnabled(False)
        self.notes_group.hide()
        self.l_control_type.hide()
        self.w_control_type.hide()

    @staticmethod
    def _set_note(spin, label, midi_note):
        spin.setValue(midi_note)
        label.setText(note_name(midi_note))

    def _refresh_mode_widgets(self):
        if self._surface is None:
            self.notes_group.hide()
            self.l_control_type.hide()
            self.w_control_type.hide()
            return

        mode = self._surface.mode
        is_control = (mode == 16)

        # Control type row
        self.l_control_type.setVisible(is_control)
        self.w_control_type.setVisible(is_control)

        # Notes group
        if is_control:
            self.notes_group.hide()
        elif mode in MODES_8_NOTES:
            self.notes_group.setTitle('Additional Notes (8 total)')
            self.notes_group.show()
            for w in (self.row_note5, self.row_note6, self.row_note7, self.row_note8):
                w.show()
        elif mode in MODES_4_NOTES:
            self.notes_group.setTitle('Additional Notes (4 total)')
            self.notes_group.show()
            for w in (self.row_note5, self.row_note6, self.row_note7, self.row_note8):
                w.hide()
        else:
            self.notes_group.hide()

    # ------------------------------------------------------------------
    # Signal handlers — main params
    # ------------------------------------------------------------------

    def _on_mode_changed(self, idx):
        if self._loading or self._surface is None:
            return
        self._surface.mode = idx
        self._refresh_mode_widgets()

    def _on_channel_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.channel = val

    def _on_curve_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.curve = val

    def _on_note_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.note = val
        self.l_note.setText(note_name(val))

    def _on_vel_min_changed(self, idx):
        if self._loading or self._surface is None:
            return
        self._surface.vel_min = VEL_MIN_VALUES[idx]

    def _on_vel_max_changed(self, idx):
        if self._loading or self._surface is None:
            return
        self._surface.vel_max = VEL_MAX_VALUES[idx]

    def _on_gate_changed(self, idx):
        if self._loading or self._surface is None:
            return
        self._surface.gate = idx

    def _on_control_type_changed(self, idx):
        if self._loading or self._surface is None:
            return
        self._surface.control_type = idx

    def _on_link_changed(self, idx):
        if self._loading or self._surface is None:
            return
        self._surface.link = self.w_link.itemData(idx)

    # ------------------------------------------------------------------
    # Signal handlers — extra notes
    # ------------------------------------------------------------------

    def _on_note2_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.note2 = val
        self.l_note2.setText(note_name(val))

    def _on_note3_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.note3 = val
        self.l_note3.setText(note_name(val))

    def _on_note4_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.note4 = val
        self.l_note4.setText(note_name(val))

    def _on_note5_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.note5 = val
        self.l_note5.setText(note_name(val))

    def _on_note6_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.note6 = val
        self.l_note6.setText(note_name(val))

    def _on_note7_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.note7 = val
        self.l_note7.setText(note_name(val))

    def _on_note8_changed(self, val):
        if self._loading or self._surface is None:
            return
        self._surface.note8 = val
        self.l_note8.setText(note_name(val))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._kit: Kit | None = None
        self._path: str | None = None
        self._unsaved = False
        self.setWindowTitle('drumKAT 3.8 Editor')
        self.resize(740, 620)
        self._build_ui()
        self._build_menu()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Kit name row
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel('Kit Name:'))
        self.w_kit_name = QLineEdit()
        self.w_kit_name.setMaxLength(12)
        self.w_kit_name.setPlaceholderText('(no kit loaded)')
        self.w_kit_name.setEnabled(False)
        self.w_kit_name.textEdited.connect(self._on_name_edited)
        name_row.addWidget(self.w_kit_name)
        root.addLayout(name_row)

        # Splitter: surface list | scrollable parameter panel
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.surface_list = QListWidget()
        self.surface_list.setFixedWidth(145)
        self.surface_list.addItems(SURFACES)
        self.surface_list.currentRowChanged.connect(self._on_surface_selected)
        splitter.addWidget(self.surface_list)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.surface_panel = SurfacePanel()
        scroll.setWidget(self.surface_panel)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)
        self.statusBar().showMessage('No kit loaded')

    def _build_menu(self):
        mb = self.menuBar()
        file_menu = mb.addMenu('&File')

        open_act = QAction('&Open Kit…', self)
        open_act.setShortcut('Ctrl+O')
        open_act.triggered.connect(self.open_kit)
        file_menu.addAction(open_act)

        save_act = QAction('&Save Kit', self)
        save_act.setShortcut('Ctrl+S')
        save_act.triggered.connect(self.save_kit)
        file_menu.addAction(save_act)

        save_as_act = QAction('Save Kit &As…', self)
        save_as_act.setShortcut('Ctrl+Shift+S')
        save_as_act.triggered.connect(self.save_kit_as)
        file_menu.addAction(save_as_act)

    # ------------------------------------------------------------------
    # Kit I/O
    # ------------------------------------------------------------------

    def open_kit(self):
        if self._unsaved and not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, 'Open SysEx Kit', '', 'SysEx Files (*.syx);;All Files (*)')
        if not path:
            return
        try:
            kit = load_kit(path)
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Failed to load kit:\n{e}')
            return
        self._kit  = kit
        self._path = path
        self._unsaved = False
        self._populate_kit()

    def save_kit(self):
        if self._kit is None:
            return
        if self._path is None:
            self.save_kit_as()
            return
        self._write_kit(self._path)

    def save_kit_as(self):
        if self._kit is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save SysEx Kit', self._path or '',
            'SysEx Files (*.syx);;All Files (*)')
        if not path:
            return
        self._path = path
        self._write_kit(path)

    def _write_kit(self, path):
        try:
            save_kit(self._kit, path)
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Failed to save kit:\n{e}')
            return
        self._unsaved = False
        self._update_title()
        self.statusBar().showMessage(f'Saved: {path}')

    def _confirm_discard(self) -> bool:
        r = QMessageBox.question(
            self, 'Unsaved Changes',
            'You have unsaved changes. Discard them?',
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return r == QMessageBox.StandardButton.Discard

    # ------------------------------------------------------------------
    # UI update
    # ------------------------------------------------------------------

    def _populate_kit(self):
        self.w_kit_name.setEnabled(True)
        self.w_kit_name.setText(self._kit.name)
        if self.surface_list.currentRow() == 0:
            self._on_surface_selected(0)
        else:
            self.surface_list.setCurrentRow(0)
        self._update_title()
        self.statusBar().showMessage(f'Loaded: {self._path}')

    def _on_surface_selected(self, row):
        if self._kit is None or row < 0:
            self.surface_panel.clear()
            return
        self.surface_panel.load_surface(self._kit.surfaces[row])

    def _on_name_edited(self, text):
        if self._kit is not None:
            self._kit.name = text
            self._mark_unsaved()

    def _mark_unsaved(self):
        if not self._unsaved:
            self._unsaved = True
            self._update_title()

    def _update_title(self):
        name  = self._kit.name if self._kit else ''
        path  = self._path or 'Untitled'
        dirty = ' •' if self._unsaved else ''
        self.setWindowTitle(f'drumKAT 3.8 Editor — {name} [{path}]{dirty}')

    def closeEvent(self, event):
        if self._unsaved and not self._confirm_discard():
            event.ignore()
        else:
            event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    win = MainWindow()
    win.show()

    if len(sys.argv) > 1:
        win._kit  = load_kit(sys.argv[1])
        win._path = sys.argv[1]
        win._populate_kit()

    sys.exit(app.exec())
