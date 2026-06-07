"""drumKAT 3.8 SysEx Visual Editor"""

import sys
import rtmidi
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QSplitter, QListWidget, QListWidgetItem, QLabel,
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QGroupBox, QScrollArea,
    QFileDialog, QMessageBox, QFrame, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QFont

from drumkat38 import (
    Kit, KitBank, load_kit, save_kit, load_kit_bank, save_kit_bank,
    parse_sysex_header, DUMP_TYPE_KIT, DUMP_TYPE_ALL_KITS,
    SURFACES, MODE_NAMES, CONTROL_TYPE_NAMES, GATE_TABLE,
    note_name, NUM_SURFACES, NUM_KITS,
    MIDI_PORT_NAMES, MODES_WITH_PORT,
    TEMPO_MIN, TEMPO_MAX,
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

    def open(self, idx: int):
        if self._port == idx:
            return
        if self._out.is_port_open():
            self._all_notes_off()
            self._out.close_port()
        ports = self.ports()
        if 0 <= idx < len(ports):
            self._out.open_port(idx)
            self._port = idx

    def close(self):
        self._all_notes_off()
        if self._out.is_port_open():
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
# Mode groupings
# ---------------------------------------------------------------------------

MODES_4_NOTES = {1, 2, 5, 11, 12, 14, 15}
MODES_8_NOTES = {9, 10, 13}


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

QSpinBox, QComboBox {
    background-color: #3c3c3c;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 3px 6px;
    color: #d4d4d4;
    min-height: 22px;
}
QSpinBox:focus, QComboBox:focus { border-color: #0e639c; }
QSpinBox:disabled, QComboBox:disabled {
    background-color: #2d2d2d;
    color: #555;
}
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #4a4a4a;
    border: none;
    width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
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

def _gate_labels() -> list[str]:
    labels = ['Latch']
    for i in range(1, 254):
        v = GATE_TABLE[i]
        labels.append(f'{v:.3f}s' if v is not None else f'?({i})')
    labels.append('No OFF')
    return labels


GATE_LABELS = _gate_labels()
VEL_MIN_VALUES = list(range(0, 121, 8))
VEL_MAX_VALUES = list(range(7, 128, 8))

# Surface list colors
PAD_COLOR     = QColor('#1a3a4a')   # dark teal tint for pads
TRIGGER_COLOR = QColor('#3a2a1a')   # dark amber tint for triggers


# ---------------------------------------------------------------------------
# Surface parameter panel
# ---------------------------------------------------------------------------

class SurfacePanel(QWidget):
    def __init__(self, midi: 'MidiEngine', parent=None):
        super().__init__(parent)
        self._surface = None
        self._loading = False
        self._midi    = midi
        self._build_ui()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_note_spin():
        spin = QSpinBox()
        spin.setRange(0, 127)
        spin.setFixedWidth(64)
        return spin

    def _make_test_btn(self, note_getter):
        """Create a ▶ button that plays the note returned by note_getter()."""
        btn = QPushButton('▶')
        btn.setObjectName('testBtn')
        btn.setFixedWidth(28)

        def on_click():
            if self._surface is None or not self._midi.ports():
                return
            btn.setProperty('playing', 'true')
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.setEnabled(False)
            self._midi.play(
                self._surface.channel,
                note_getter(),
                callback_note_off=lambda: self._reset_btn(btn),
            )

        btn.clicked.connect(on_click)
        return btn

    def _reset_btn(self, btn):
        btn.setProperty('playing', 'false')
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        btn.setEnabled(True)

    def _note_widget(self, spin, label, note_getter=None):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(spin)
        h.addWidget(label)
        if note_getter is not None:
            h.addWidget(self._make_test_btn(note_getter))
        h.addStretch()
        return w

    def _add_note_row(self, form, row_label, mapped=True):
        spin  = self._make_note_spin()
        label = QLabel('---')
        label.setMinimumWidth(48)
        label.setObjectName('noteLabel')
        if not mapped:
            spin.setEnabled(False)
            spin.setToolTip('Block position not yet mapped')
            label.setText('TBD')
        form.addRow(row_label, self._note_widget(spin, label))
        return spin, label

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Surface name heading
        self.l_surface_name = QLabel('')
        self.l_surface_name.setObjectName('heading')
        font = self.l_surface_name.font()
        font.setPointSize(15)
        font.setBold(True)
        self.l_surface_name.setFont(font)
        self.l_surface_name.setStyleSheet('color: #d4d4d4;')
        root.addWidget(self.l_surface_name)

        divider = QFrame()
        divider.setObjectName('divider')
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet('background-color: #3a3a3a; max-height: 1px; border: none;')
        root.addWidget(divider)

        # ---- Main form ----
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(12)

        self.w_mode = QComboBox()
        self.w_mode.addItems(MODE_NAMES)
        self.w_mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow('Mode:', self.w_mode)

        self.w_channel = QSpinBox()
        self.w_channel.setRange(1, 16)
        self.w_channel.setFixedWidth(64)
        self.w_channel.valueChanged.connect(self._on_channel_changed)
        form.addRow('MIDI Channel:', self.w_channel)

        self.w_curve = QSpinBox()
        self.w_curve.setRange(1, 16)
        self.w_curve.setFixedWidth(64)
        self.w_curve.valueChanged.connect(self._on_curve_changed)
        form.addRow('Velocity Curve:', self.w_curve)

        # Note 1 with prominent name label
        self.w_note = self._make_note_spin()
        self.l_note = QLabel('---')
        self.l_note.setMinimumWidth(48)
        self.l_note.setStyleSheet('color: #4ec9b0; font-weight: bold; font-size: 14px;')
        self.w_note.valueChanged.connect(self._on_note_changed)
        form.addRow('Note 1:', self._note_widget(self.w_note, self.l_note,
                                                  lambda: self.w_note.value()))

        # Vel Min + Max on same row
        vel_widget = QWidget()
        vel_layout = QHBoxLayout(vel_widget)
        vel_layout.setContentsMargins(0, 0, 0, 0)
        vel_layout.setSpacing(8)
        self.w_vel_min = QComboBox()
        self.w_vel_min.addItems([str(v) for v in VEL_MIN_VALUES])
        self.w_vel_min.setFixedWidth(72)
        self.w_vel_min.currentIndexChanged.connect(self._on_vel_min_changed)
        lbl_to = QLabel('→')
        lbl_to.setStyleSheet('color: #555; font-size: 16px;')
        self.w_vel_max = QComboBox()
        self.w_vel_max.addItems([str(v) for v in VEL_MAX_VALUES])
        self.w_vel_max.setFixedWidth(72)
        self.w_vel_max.currentIndexChanged.connect(self._on_vel_max_changed)
        vel_layout.addWidget(self.w_vel_min)
        vel_layout.addWidget(lbl_to)
        vel_layout.addWidget(self.w_vel_max)
        vel_layout.addStretch()
        form.addRow('Velocity:', vel_widget)

        self.w_gate = QComboBox()
        self.w_gate.addItems(GATE_LABELS)
        self.w_gate.setMaxVisibleItems(16)
        self.w_gate.currentIndexChanged.connect(self._on_gate_changed)
        form.addRow('Gate Time:', self.w_gate)

        # MIDI Port (not available in Simple or Control mode)
        self.w_midi_port = QComboBox()
        self.w_midi_port.addItems(MIDI_PORT_NAMES)
        self.w_midi_port.currentIndexChanged.connect(self._on_midi_port_setting_changed)
        self.l_midi_port = QLabel('MIDI Port:')
        form.addRow(self.l_midi_port, self.w_midi_port)

        # Control Type (Control mode only)
        self.w_control_type = QComboBox()
        self.w_control_type.addItems(CONTROL_TYPE_NAMES)
        self.w_control_type.currentIndexChanged.connect(self._on_control_type_changed)
        self.l_control_type = QLabel('Control Type:')
        form.addRow(self.l_control_type, self.w_control_type)

        self.w_link = QComboBox()
        self.w_link.currentIndexChanged.connect(self._on_link_changed)
        form.addRow('Link To:', self.w_link)

        root.addLayout(form)

        # ---- Additional notes group ----
        self.notes_group = QGroupBox('Additional Notes')
        nf = QFormLayout(self.notes_group)
        nf.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        nf.setVerticalSpacing(8)
        nf.setHorizontalSpacing(12)
        nf.setContentsMargins(12, 16, 12, 12)

        def _make_note_label():
            lbl = QLabel('---')
            lbl.setMinimumWidth(48)
            lbl.setStyleSheet('color: #4ec9b0; font-weight: bold;')
            return lbl

        def _note_row(form, row_label):
            spin  = self._make_note_spin()
            label = _make_note_label()
            form.addRow(row_label, self._note_widget(spin, label, lambda s=spin: s.value()))
            return spin, label

        self.w_note2, self.l_note2 = _note_row(nf, 'Note 2:')
        self.w_note3, self.l_note3 = _note_row(nf, 'Note 3:')
        self.w_note4, self.l_note4 = _note_row(nf, 'Note 4:')

        # Divider between 4-note and 8-note section
        self.notes_divider = QFrame()
        self.notes_divider.setFrameShape(QFrame.Shape.HLine)
        self.notes_divider.setStyleSheet('background-color: #3a3a3a; max-height: 1px; border: none;')
        nf.addRow(self.notes_divider)

        self.w_note5, self.l_note5 = _note_row(nf, 'Note 5:')
        self.w_note6, self.l_note6 = _note_row(nf, 'Note 6:')
        self.w_note7, self.l_note7 = _note_row(nf, 'Note 7:')
        self.w_note8, self.l_note8 = _note_row(nf, 'Note 8:')

        # Store row widgets for show/hide (grab from form)
        self._note58_widgets = [self.w_note5, self.l_note5,
                                 self.w_note6, self.l_note6,
                                 self.w_note7, self.l_note7,
                                 self.w_note8, self.l_note8,
                                 self.notes_divider]

        for spin, handler in [
            (self.w_note2, self._on_note2_changed),
            (self.w_note3, self._on_note3_changed),
            (self.w_note4, self._on_note4_changed),
            (self.w_note5, self._on_note5_changed),
            (self.w_note6, self._on_note6_changed),
            (self.w_note7, self._on_note7_changed),
            (self.w_note8, self._on_note8_changed),
        ]:
            spin.valueChanged.connect(handler)

        root.addWidget(self.notes_group)
        self.notes_group.hide()
        self.setEnabled(False)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_surface(self, surface):
        self._surface = surface
        self._loading = True
        self.setEnabled(True)

        self.l_surface_name.setText(surface.name)
        self.w_mode.setCurrentIndex(surface.mode)
        self.w_channel.setValue(surface.channel)
        self.w_curve.setValue(surface.curve)
        self._set_note(self.w_note, self.l_note, surface.note)
        # Use get() via dict to avoid ValueError on unusual stored bytes
        vmin_idx = VEL_MIN_VALUES.index(surface.vel_min) if surface.vel_min in VEL_MIN_VALUES else 0
        vmax_idx = VEL_MAX_VALUES.index(surface.vel_max) if surface.vel_max in VEL_MAX_VALUES else len(VEL_MAX_VALUES) - 1
        self.w_vel_min.setCurrentIndex(vmin_idx)
        self.w_vel_max.setCurrentIndex(vmax_idx)
        self.w_gate.setCurrentIndex(surface.gate)
        self.w_midi_port.setCurrentIndex(surface.midi_port)

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

        self._set_note(self.w_note2, self.l_note2, surface.note2)
        self._set_note(self.w_note3, self.l_note3, surface.note3)
        self._set_note(self.w_note4, self.l_note4, surface.note4)
        self._set_note(self.w_note5, self.l_note5, surface.note5)
        self._set_note(self.w_note6, self.l_note6, surface.note6)
        self._set_note(self.w_note7, self.l_note7, surface.note7)
        self._set_note(self.w_note8, self.l_note8, surface.note8)

        ct = surface.control_type
        self.w_control_type.setCurrentIndex(ct if 0 <= ct < len(CONTROL_TYPE_NAMES) else 0)

        self._refresh_mode_widgets()
        self._loading = False

    def clear(self):
        self._surface = None
        self.l_surface_name.setText('')
        self.setEnabled(False)
        self.notes_group.hide()
        self.l_control_type.hide()
        self.w_control_type.hide()
        self.l_midi_port.hide()
        self.w_midi_port.hide()

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

        self.l_control_type.setVisible(is_control)
        self.w_control_type.setVisible(is_control)

        has_port = mode in MODES_WITH_PORT
        self.l_midi_port.setVisible(has_port)
        self.w_midi_port.setVisible(has_port)

        if is_control:
            self.notes_group.hide()
        elif mode in MODES_8_NOTES:
            self.notes_group.setTitle('Additional Notes')
            self.notes_group.show()
            for w in self._note58_widgets:
                w.show()
        elif mode in MODES_4_NOTES:
            self.notes_group.setTitle('Additional Notes')
            self.notes_group.show()
            for w in self._note58_widgets:
                w.hide()
        else:
            self.notes_group.hide()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_mode_changed(self, idx):
        if self._loading or self._surface is None: return
        self._surface.mode = idx
        self._refresh_mode_widgets()

    def _on_channel_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.channel = val

    def _on_curve_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.curve = val

    def _on_note_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.note = val
        self.l_note.setText(note_name(val))

    def _on_vel_min_changed(self, idx):
        if self._loading or self._surface is None: return
        self._surface.vel_min = VEL_MIN_VALUES[idx]

    def _on_vel_max_changed(self, idx):
        if self._loading or self._surface is None: return
        self._surface.vel_max = VEL_MAX_VALUES[idx]

    def _on_gate_changed(self, idx):
        if self._loading or self._surface is None: return
        self._surface.gate = idx

    def _on_control_type_changed(self, idx):
        if self._loading or self._surface is None: return
        self._surface.control_type = idx

    def _on_midi_port_setting_changed(self, idx):
        if self._loading or self._surface is None: return
        self._surface.midi_port = idx

    def _on_link_changed(self, idx):
        if self._loading or self._surface is None: return
        self._surface.link = self.w_link.itemData(idx)

    def _on_note2_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.note2 = val; self.l_note2.setText(note_name(val))

    def _on_note3_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.note3 = val; self.l_note3.setText(note_name(val))

    def _on_note4_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.note4 = val; self.l_note4.setText(note_name(val))

    def _on_note5_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.note5 = val; self.l_note5.setText(note_name(val))

    def _on_note6_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.note6 = val; self.l_note6.setText(note_name(val))

    def _on_note7_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.note7 = val; self.l_note7.setText(note_name(val))

    def _on_note8_changed(self, val):
        if self._loading or self._surface is None: return
        self._surface.note8 = val; self.l_note8.setText(note_name(val))


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
        self.resize(820, 660)
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
        self.w_tempo = QDoubleSpinBox()
        self.w_tempo.setRange(TEMPO_MIN, TEMPO_MAX)
        self.w_tempo.setDecimals(1)
        self.w_tempo.setSingleStep(1.0)
        self.w_tempo.setSuffix(' BPM')
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
        if not path:
            return
        try:
            with open(path, 'rb') as f:
                raw = f.read()
            hdr = parse_sysex_header(raw)
            if hdr.dump_type == DUMP_TYPE_ALL_KITS:
                bank = load_kit_bank(path)
                self._bank = bank
                self._kit  = bank.kits[0]
            else:
                self._bank = None
                self._kit  = load_kit(path)
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Failed to load file:\n{e}')
            return
        self._path    = path
        self._unsaved = False
        self._populate_kit()

    def _on_kit_selected(self, idx):
        if self._bank is None or idx < 0:
            return
        self._kit = self._bank.kits[idx]
        self.w_kit_name.setText(self._kit.name)
        self.w_tempo.blockSignals(True)
        self.w_tempo.setValue(self._kit.tempo)
        self.w_tempo.blockSignals(False)
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
        if not path: return
        self._path = path
        self._write_kit(path)

    def _write_kit(self, path):
        try:
            if self._bank is not None:
                save_kit_bank(self._bank, path)
            else:
                save_kit(self._kit, path)
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Failed to save:\n{e}')
            return
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
        self.w_tempo.blockSignals(True)
        self.w_tempo.setValue(self._kit.tempo)
        self.w_tempo.blockSignals(False)
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
        # Snap the spinbox to the actually-stored value so the display always
        # reflects what will be written to the hardware (integer quantisation
        # means 120.0 BPM stores as 120.5, etc.)
        canonical = self._kit.tempo
        if canonical != value:
            self.w_tempo.blockSignals(True)
            self.w_tempo.setValue(canonical)
            self.w_tempo.blockSignals(False)
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
        try:
            with open(sys.argv[1], 'rb') as f:
                raw = f.read()
            hdr = parse_sysex_header(raw)
            if hdr.dump_type == DUMP_TYPE_ALL_KITS:
                win._bank = load_kit_bank(sys.argv[1])
                win._kit  = win._bank.kits[0]
            else:
                win._kit = load_kit(sys.argv[1])
            win._path = sys.argv[1]
            win._populate_kit()
        except Exception as e:
            print(f'Error loading {sys.argv[1]}: {e}')

    sys.exit(app.exec())
