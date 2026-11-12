"""drumKAT 3.8 SysEx Visual Editor"""

import sys
import rtmidi
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFormLayout, QSplitter, QListWidget, QListWidgetItem, QLabel,
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QScrollArea,
    QFileDialog, QMessageBox, QFrame, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor

from drumkat38 import (
    Kit, KitBank, Sound, load, save,
    SURFACES, MODE_NAMES, CONTROL_TYPE_NAMES, MIDI_PORT_NAMES,
    MODES_8_NOTES, MODES_WITH_PORT,
    MODE_NOTE_SHIFT, MODE_GATE_SHIFT, MODE_VELOCITY_SHIFT, MODE_CONTROL,
    NOTE_OFF, GATE_NO_OFF, TEMPO_MIN, TEMPO_MAX,
    gate_label, note_name, tempo_from_stored, tempo_to_stored,
)



# ---------------------------------------------------------------------------
# MIDI engine
# ---------------------------------------------------------------------------

class MidiEngine:
    """Thin wrapper around rtmidi for sending test notes."""

    TEST_VELOCITY = 100
    NOTE_DURATION = 500   # ms

    def __init__(self):
        self._out  = rtmidi.MidiOut()
        self._port = None          # currently open port index

    def ports(self) -> list[str]:
        return self._out.get_ports()

    def _all_notes_off(self):
        """Send All-Notes-Off (CC 123) on all 16 channels to silence any stuck notes."""
        if self._out.is_port_open():
            for ch in range(16):
                self._out.send_message([0xB0 | ch, 123, 0])

    def is_open(self) -> bool:
        return self._out.is_port_open()

    def open(self, idx: int):
        if self._port == idx and self.is_open():
            return
        self.close()
        if 0 <= idx < len(self.ports()):
            self._out.open_port(idx)
            self._port = idx

    def close(self):
        self._all_notes_off()
        if self.is_open():
            self._out.close_port()
        self._port = None

    def play(self, channel: int, note: int, callback_note_off=None):
        """Send Note On; schedule Note Off after NOTE_DURATION ms."""
        if not self._out.is_port_open():
            return
        ch = (channel - 1) & 0x0F
        self._out.send_message([0x90 | ch, note & 0x7F, self.TEST_VELOCITY])
        QTimer.singleShot(self.NOTE_DURATION, lambda: self._note_off(ch, note, callback_note_off))

    def _note_off(self, ch: int, note: int, callback=None):
        if self._out.is_port_open():
            self._out.send_message([0x80 | ch, note & 0x7F, 0])
        if callback:
            callback()



# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

THEME = """
QMainWindow, QWidget {
    background-color: #1e1e1e;
    color: #d4d4d4;
    font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

QMenuBar {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border-bottom: 1px solid #3a3a3a;
}
QMenuBar::item:selected { background-color: #094771; }
QMenu {
    background-color: #2d2d2d;
    border: 1px solid #454545;
}
QMenu::item:selected { background-color: #094771; }

QSplitter::handle { background-color: #3a3a3a; width: 1px; }

QListWidget {
    background-color: #252526;
    border: none;
    border-right: 1px solid #3a3a3a;
    outline: none;
    font-size: 13px;
}
QListWidget::item {
    padding: 6px 10px;
    border-bottom: 1px solid #2a2a2a;
}
QListWidget::item:selected {
    background-color: #094771;
    color: #ffffff;
}
QListWidget::item:hover:!selected {
    background-color: #2a2d2e;
}

QScrollArea { border: none; background-color: #1e1e1e; }

QLineEdit {
    background-color: #3c3c3c;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 4px 8px;
    color: #d4d4d4;
    selection-background-color: #094771;
}
QLineEdit:focus { border-color: #0e639c; }
QLineEdit:disabled { background-color: #2d2d2d; color: #6a6a6a; }

QAbstractSpinBox, QComboBox {
    background-color: #3c3c3c;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 3px 6px;
    color: #d4d4d4;
    min-height: 22px;
}
QAbstractSpinBox:focus, QComboBox:focus { border-color: #0e639c; }
QAbstractSpinBox:disabled, QComboBox:disabled {
    background-color: #2d2d2d;
    color: #555;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    background-color: #4a4a4a;
    border: none;
    width: 16px;
}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
    background-color: #0e639c;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #888;
    width: 0; height: 0;
}
QComboBox QAbstractItemView {
    background-color: #2d2d2d;
    border: 1px solid #555;
    selection-background-color: #094771;
    outline: none;
}

QGroupBox {
    border: 1px solid #3a3a3a;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    font-weight: bold;
    color: #9d9d9d;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

QPushButton#testBtn {
    background-color: #2a4a2a;
    color: #4ec94e;
    border: 1px solid #3a6a3a;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    min-width: 28px;
    max-height: 22px;
}
QPushButton#testBtn:hover  { background-color: #3a6a3a; }
QPushButton#testBtn:pressed { background-color: #1a3a1a; }
QPushButton#testBtn:disabled { color: #444; border-color: #333; background-color: #222; }
QPushButton#testBtn[playing="true"] {
    background-color: #1a3a1a;
    color: #2a8a2a;
}

QStatusBar {
    background-color: #007acc;
    color: #ffffff;
    font-size: 12px;
}
QStatusBar::item { border: none; }

QLabel { color: #9d9d9d; }
QLabel#heading { color: #d4d4d4; font-weight: bold; }

QFrame#divider {
    background-color: #3a3a3a;
    max-height: 1px;
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GATE_LABELS = [gate_label(i) for i in range(GATE_NO_OFF + 1)]
HAT_OPEN_LABELS = ['Open', 'Closed', 'Closed 2', 'Sound 4']

# Surface list colors
PAD_COLOR     = QColor('#1a3a4a')   # dark teal tint for pads
TRIGGER_COLOR = QColor('#3a2a1a')   # dark amber tint for triggers


def _spin(lo: int, hi: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(lo, hi)
    spin.setFixedWidth(54)
    return spin


def _combo(items, width: int | None = None) -> QComboBox:
    combo = QComboBox()
    combo.addItems(list(items))
    combo.setMaxVisibleItems(16)
    if width:
        combo.setFixedWidth(width)
    return combo


class TempoSpin(QDoubleSpinBox):
    """Tempo editor whose arrows step through the tempos the unit can store."""

    def __init__(self):
        super().__init__()
        self.setRange(TEMPO_MIN, TEMPO_MAX)
        self.setDecimals(1)
        self.setSuffix(' BPM')
        self.setKeyboardTracking(False)     # don't snap while the user is typing

    def stepBy(self, steps: int):
        self.setValue(tempo_from_stored(tempo_to_stored(self.value()) - steps))


# ---------------------------------------------------------------------------
# Surface parameter panel
# ---------------------------------------------------------------------------

# Column name -> header text, in grid order.
COLUMNS = {
    'label': '', 'channel': 'Ch', 'note': 'Note', 'note_name': '', 'test': '',
    'vel_min': 'Vel min', 'vel_max': 'Vel max', 'curve': 'Curve', 'gate': 'Gate',
    'port': 'MIDI out', 'dyn_min': 'Hit from', 'dyn_max': 'Hit to',
}
NOTE_COLUMNS = {'label', 'channel', 'note', 'note_name', 'test'}
SOUND_COLUMNS = NOTE_COLUMNS | {'vel_min', 'vel_max', 'curve', 'gate'}


class VoiceRow:
    """Editors for one sound (or one extra note of an 8-note mode) on a grid row."""

    def __init__(self, panel: 'SurfacePanel', grid: QGridLayout, row: int):
        self._panel = panel
        self._slot = None
        note_name_label = QLabel()
        note_name_label.setMinimumWidth(40)
        note_name_label.setStyleSheet('color: #4ec9b0; font-weight: bold;')
        test = QPushButton('▶')
        test.setObjectName('testBtn')
        test.setFixedWidth(28)
        test.clicked.connect(self._play)
        self.w = {
            'label': QLabel(), 'channel': _spin(1, 16), 'note': _spin(0, NOTE_OFF),
            'note_name': note_name_label, 'test': test,
            'vel_min': _combo((str(v) for v in range(0, 121, 8)), 60),
            'vel_max': _combo((str(v) for v in range(7, 128, 8)), 60),
            'curve': _spin(1, 16), 'gate': _combo(GATE_LABELS, 86),
            'port': _combo(MIDI_PORT_NAMES, 70),
            'dyn_min': _spin(0, 127), 'dyn_max': _spin(0, 127),
        }
        for col, name in enumerate(COLUMNS):
            grid.addWidget(self.w[name], row, col)

        for name in ('channel', 'curve', 'dyn_min', 'dyn_max'):
            self.w[name].valueChanged.connect(lambda v, n=name: self._set(n, v))
        for name in ('gate', 'port'):
            self.w[name].currentIndexChanged.connect(lambda i, n=name: self._set(n, i))
        self.w['vel_min'].currentIndexChanged.connect(lambda i: self._set('vel_min', i * 8))
        self.w['vel_max'].currentIndexChanged.connect(lambda i: self._set('vel_max', i * 8 + 7))
        self.w['note'].valueChanged.connect(self._note_changed)

    def _set(self, attr: str, value: int):
        if self._slot is not None and not self._panel.loading:
            setattr(self._slot, attr, value)
            self._panel.changed.emit()

    def _note_changed(self, value: int):
        self.w['note_name'].setText(note_name(value))
        self._set('note', value)

    def _play(self):
        midi, slot, btn = self._panel.midi, self._slot, self.w['test']
        if slot is None or slot.note == NOTE_OFF or not midi.is_open():
            return
        btn.setEnabled(False)
        midi.play(slot.channel, slot.note, lambda: btn.setEnabled(True))

    def show(self, slot, label: str, columns: set[str]):
        """Bind this row to `slot` and show only `columns`. Caller sets panel.loading."""
        self._slot = slot
        w = self.w
        w['label'].setText(label)
        w['channel'].setValue(slot.channel)
        w['note'].setValue(slot.note)
        w['note_name'].setText(note_name(slot.note))
        if isinstance(slot, Sound):
            w['vel_min'].setCurrentIndex(slot.vel_min // 8)
            w['vel_max'].setCurrentIndex(slot.vel_max // 8)
            w['curve'].setValue(slot.curve)
            w['gate'].setCurrentIndex(slot.gate)
            w['port'].setCurrentIndex(slot.port)
            w['dyn_min'].setValue(slot.dyn_min)
            w['dyn_max'].setValue(slot.dyn_max)
        for name, widget in w.items():
            widget.setVisible(name in columns)

    def hide(self):
        self._slot = None
        for widget in self.w.values():
            widget.hide()


class SurfacePanel(QWidget):
    changed = pyqtSignal()      # emitted after any edit to the surface

    def __init__(self, midi: MidiEngine, parent=None):
        super().__init__(parent)
        self.midi = midi
        self.loading = False
        self._surface = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.l_surface_name = QLabel('')
        font = self.l_surface_name.font()
        font.setPointSize(15)
        font.setBold(True)
        self.l_surface_name.setFont(font)
        self.l_surface_name.setStyleSheet('color: #d4d4d4;')
        root.addWidget(self.l_surface_name)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet('background-color: #3a3a3a; max-height: 1px; border: none;')
        root.addWidget(divider)

        self._form = form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(12)

        self.w_mode = _combo(MODE_NAMES, 200)
        self.w_mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow('Mode:', self.w_mode)

        self.w_control_type = _combo(CONTROL_TYPE_NAMES, 200)
        self.w_control_type.currentIndexChanged.connect(lambda i: self._set('control_type', i))
        form.addRow('Control Type:', self.w_control_type)

        self.w_shift_note = _spin(0, 127)
        self.w_shift_note.valueChanged.connect(lambda v: self._set('shift_end', v))
        form.addRow('Shift up to note:', self.w_shift_note)

        self.w_shift_gate = _combo(GATE_LABELS, 92)
        self.w_shift_gate.currentIndexChanged.connect(lambda i: self._set('shift_end', i))
        form.addRow('Shift up to gate:', self.w_shift_gate)

        self.w_link = QComboBox()
        self.w_link.setFixedWidth(200)
        self.w_link.currentIndexChanged.connect(
            lambda i: self._set('link', self.w_link.itemData(i)))
        form.addRow('Link To:', self.w_link)
        root.addLayout(form)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self._headers = {}
        for col, (name, text) in enumerate(COLUMNS.items()):
            self._headers[name] = QLabel(text)
            grid.addWidget(self._headers[name], 0, col)
        self._rows = [VoiceRow(self, grid, r + 1) for r in range(8)]
        grid.setColumnStretch(len(COLUMNS), 1)
        root.addLayout(grid)
        self.clear()

    def _set(self, attr: str, value):
        if self._surface is not None and not self.loading:
            setattr(self._surface, attr, value)
            self.changed.emit()

    def _on_mode_changed(self, idx: int):
        if self._surface is not None and not self.loading:
            self._surface.mode = idx
            self.changed.emit()
            self.load_surface(self._surface)

    def clear(self):
        self._surface = None
        self.l_surface_name.setText('')
        self.setEnabled(False)

    def load_surface(self, surface):
        self._surface = surface
        self.loading = True
        try:
            self._load(surface)
        finally:
            self.loading = False
        self.setEnabled(True)

    def _load(self, s):
        mode = s.mode
        self.l_surface_name.setText(s.name)
        self.w_mode.setCurrentIndex(mode if mode < len(MODE_NAMES) else -1)

        ct = s.control_type
        self.w_control_type.setCurrentIndex(ct if ct < len(CONTROL_TYPE_NAMES) else -1)
        self.w_shift_note.setValue(min(s.shift_end, 127))
        self.w_shift_gate.setCurrentIndex(min(s.shift_end, GATE_NO_OFF))
        self._form.setRowVisible(self.w_control_type, mode == MODE_CONTROL)
        self._form.setRowVisible(self.w_shift_note, mode == MODE_NOTE_SHIFT)
        self._form.setRowVisible(self.w_shift_gate, mode == MODE_GATE_SHIFT)

        self.w_link.clear()
        self.w_link.addItem('None', userData=None)
        for i, name in enumerate(SURFACES):
            if i != s.index:
                self.w_link.addItem(name, userData=i)
        self.w_link.setCurrentIndex(max(self.w_link.findData(s.link), 0))

        # Sound rows: every mode has sound 1; the multi-note modes add rows.
        sound_cols = set(SOUND_COLUMNS)
        if mode in MODES_WITH_PORT:
            sound_cols.add('port')
        if mode == MODE_VELOCITY_SHIFT:
            sound_cols |= {'dyn_min', 'dyn_max'}
        slots = s.slots
        if mode in MODES_8_NOTES:
            labels = [f'Note {n}' for n in range(1, 9)]
        elif mode == 6:
            labels = HAT_OPEN_LABELS
        else:
            labels = [f'Sound {n}' for n in range(1, 5)] if len(slots) > 1 else ['']
        for row, slot, label in zip(self._rows, slots, labels):
            row.show(slot, label, sound_cols if isinstance(slot, Sound) else NOTE_COLUMNS)
        for row in self._rows[len(slots):]:
            row.hide()
        for name, header in self._headers.items():
            header.setVisible(name in sound_cols)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._kit: Kit | None = None
        self._bank: KitBank | None = None
        self._path: str | None = None
        self._unsaved = False
        self._midi = MidiEngine()
        self.setWindowTitle('drumKAT 3.8 Editor')
        self.resize(1100, 660)
        self._build_ui()
        self._build_menu()
        self._refresh_midi_ports()   # auto-open first port if available

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar — kit name + MIDI port only
        top_bar = QWidget()
        top_bar.setFixedHeight(36)
        top_bar.setStyleSheet('background-color: #2d2d2d; border-bottom: 1px solid #3a3a3a;')
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 4, 12, 4)
        top_layout.setSpacing(10)

        lbl_name = QLabel('Name:')
        lbl_name.setStyleSheet('color: #9d9d9d;')
        self.w_kit_name = QLineEdit()
        self.w_kit_name.setMaxLength(12)
        self.w_kit_name.setPlaceholderText('(no kit loaded)')
        self.w_kit_name.setEnabled(False)
        self.w_kit_name.setFixedWidth(130)
        self.w_kit_name.textEdited.connect(self._on_name_edited)
        top_layout.addWidget(lbl_name)
        top_layout.addWidget(self.w_kit_name)

        lbl_tempo = QLabel('Tempo:')
        lbl_tempo.setStyleSheet('color: #9d9d9d;')
        self.w_tempo = TempoSpin()
        self.w_tempo.setFixedWidth(100)
        self.w_tempo.setEnabled(False)
        self.w_tempo.valueChanged.connect(self._on_tempo_changed)
        top_layout.addWidget(lbl_tempo)
        top_layout.addWidget(self.w_tempo)
        top_layout.addStretch()

        lbl_midi = QLabel('MIDI Out:')
        lbl_midi.setStyleSheet('color: #9d9d9d;')
        self.w_midi_port = QComboBox()
        self.w_midi_port.setMinimumWidth(160)
        self.w_midi_port.setToolTip('MIDI output port for test notes')
        self.w_midi_port.currentIndexChanged.connect(self._on_midi_port_changed)
        top_layout.addWidget(lbl_midi)
        top_layout.addWidget(self.w_midi_port)

        root.addWidget(top_bar)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        # Left panel: kit selector (bank mode) + surface list
        left_panel = QWidget()
        left_panel.setFixedWidth(148)
        left_panel.setStyleSheet('background-color: #252526;')
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # Kit selector — shown only when a bank is loaded
        self.kit_selector_row = QWidget()
        self.kit_selector_row.setStyleSheet('background-color: #1e1e1e; border-bottom: 1px solid #3a3a3a;')
        ks = QVBoxLayout(self.kit_selector_row)
        ks.setContentsMargins(6, 6, 6, 6)
        ks.setSpacing(3)
        lbl_kit = QLabel('KIT')
        lbl_kit.setStyleSheet('color: #666; font-size: 10px; letter-spacing: 1px;')
        self.w_kit_selector = QComboBox()
        self.w_kit_selector.setMaxVisibleItems(10)
        self.w_kit_selector.currentIndexChanged.connect(self._on_kit_selected)
        ks.addWidget(lbl_kit)
        ks.addWidget(self.w_kit_selector)
        self.kit_selector_row.hide()
        left_layout.addWidget(self.kit_selector_row)

        self.surface_list = QListWidget()
        self.surface_list.currentRowChanged.connect(self._on_surface_selected)
        self._populate_surface_list()
        left_layout.addWidget(self.surface_list)

        splitter.addWidget(left_panel)

        # Right: scrollable parameter panel
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea { background-color: #1e1e1e; border: none; }')
        self.surface_panel = SurfacePanel(self._midi)
        self.surface_panel.changed.connect(self._mark_unsaved)
        scroll.setWidget(self.surface_panel)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)
        self.statusBar().showMessage('No kit loaded — File > Open to begin')

    def _populate_surface_list(self):
        self.surface_list.clear()
        self.surface_list.setStyleSheet('QListWidget { border: none; }')
        for i, name in enumerate(SURFACES):
            item = QListWidgetItem(name)
            item.setBackground(PAD_COLOR if i < 10 else TRIGGER_COLOR)
            self.surface_list.addItem(item)

    def _build_menu(self):
        mb = self.menuBar()
        file_menu = mb.addMenu('&File')

        open_act = QAction('&Open…', self)
        open_act.setShortcut('Ctrl+O')
        open_act.triggered.connect(self.open_kit)
        file_menu.addAction(open_act)

        file_menu.addSeparator()

        save_act = QAction('&Save', self)
        save_act.setShortcut('Ctrl+S')
        save_act.triggered.connect(self.save_kit)
        file_menu.addAction(save_act)

        save_as_act = QAction('Save &As…', self)
        save_as_act.setShortcut('Ctrl+Shift+S')
        save_as_act.triggered.connect(self.save_kit_as)
        file_menu.addAction(save_as_act)

    # ------------------------------------------------------------------
    # MIDI
    # ------------------------------------------------------------------

    def _refresh_midi_ports(self):
        self.w_midi_port.blockSignals(True)
        self.w_midi_port.clear()
        ports = self._midi.ports()
        if ports:
            self.w_midi_port.addItem('None', userData=-1)
            for i, name in enumerate(ports):
                self.w_midi_port.addItem(name, userData=i)
            self.w_midi_port.setCurrentIndex(1)   # auto-select first real port
            self._midi.open(0)
        else:
            self.w_midi_port.addItem('No MIDI ports found', userData=-1)
        self.w_midi_port.blockSignals(False)

    def _on_midi_port_changed(self, combo_idx):
        idx = self.w_midi_port.itemData(combo_idx)
        if idx is None or idx < 0:
            self._midi.close()
        else:
            self._midi.open(idx)

    # ------------------------------------------------------------------
    # Kit I/O
    # ------------------------------------------------------------------

    def open_kit(self):
        if self._unsaved and not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, 'Open SysEx File', '', 'SysEx Files (*.syx);;All Files (*)')
        if path:
            self.load_path(path)

    def load_path(self, path: str):
        try:
            loaded = load(path)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, 'Error', f'Failed to load file:\n{e}')
            return
        self._bank = loaded if isinstance(loaded, KitBank) else None
        self._kit = loaded.kits[0] if self._bank else loaded
        self._path = path
        self._unsaved = False
        self._populate_kit()

    def _on_kit_selected(self, idx):
        if self._bank is None or idx < 0:
            return
        self._kit = self._bank.kits[idx]
        self.w_kit_name.setText(self._kit.name)
        self._show_tempo()
        row = self.surface_list.currentRow()
        self._on_surface_selected(row if row >= 0 else 0)

    def save_kit(self):
        if self._kit is None: return
        if self._path is None: self.save_kit_as(); return
        self._write_kit(self._path)

    def save_kit_as(self):
        if self._kit is None: return
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save SysEx', self._path or '',
            'SysEx Files (*.syx);;All Files (*)')
        if path:
            self._write_kit(path)

    def _write_kit(self, path):
        try:
            save(self._bank or self._kit, path)
        except OSError as e:
            QMessageBox.critical(self, 'Error', f'Failed to save:\n{e}')
            return
        self._path = path
        self._unsaved = False
        self._update_title()
        self.statusBar().showMessage(f'Saved: {path}')

    def _confirm_discard(self) -> bool:
        r = QMessageBox.question(
            self, 'Unsaved Changes', 'Discard unsaved changes?',
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return r == QMessageBox.StandardButton.Discard

    # ------------------------------------------------------------------
    # UI update
    # ------------------------------------------------------------------

    def _populate_kit(self):
        if self._bank is not None:
            self.w_kit_selector.blockSignals(True)
            self.w_kit_selector.clear()
            for i, k in enumerate(self._bank.kits):
                self.w_kit_selector.addItem(f'{i+1:2d}.  {k.name}')
            self.w_kit_selector.setCurrentIndex(0)
            self.w_kit_selector.blockSignals(False)
            self.kit_selector_row.show()
        else:
            self.kit_selector_row.hide()

        self.w_kit_name.setEnabled(True)
        self.w_kit_name.setText(self._kit.name)
        self.w_tempo.setEnabled(True)
        self._show_tempo()
        if self.surface_list.currentRow() == 0:
            self._on_surface_selected(0)
        else:
            self.surface_list.setCurrentRow(0)
        self._update_title()
        src = 'All Kits' if self._bank else 'Kit'
        self.statusBar().showMessage(f'Loaded {src}: {self._path}')

    def _on_surface_selected(self, row):
        if self._kit is None or row < 0:
            self.surface_panel.clear()
            return
        self.surface_panel.load_surface(self._kit.surfaces[row])

    def _on_name_edited(self, text):
        if self._kit is None: return
        self._kit.name = text
        if self._bank is not None:
            idx = self.w_kit_selector.currentIndex()
            self.w_kit_selector.blockSignals(True)
            self.w_kit_selector.setItemText(idx, f'{idx+1:2d}.  {text}')
            self.w_kit_selector.blockSignals(False)
        self._mark_unsaved()

    def _on_tempo_changed(self, value):
        if self._kit is None: return
        self._kit.tempo = value
        self._show_tempo()      # snap to the tempo the unit will actually store
        self._mark_unsaved()

    def _show_tempo(self):
        self.w_tempo.blockSignals(True)
        self.w_tempo.setValue(self._kit.tempo)
        self.w_tempo.blockSignals(False)

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
            self._midi.close()
            event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(THEME)
    win = MainWindow()
    win.show()

    if len(sys.argv) > 1:
        win.load_path(sys.argv[1])

    sys.exit(app.exec())
