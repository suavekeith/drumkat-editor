"""
drumKAT 3.8 SysEx parser / builder.

Byte map, worked out by diffing live dumps (confidence noted where it is not
a direct single-parameter diff).

SysEx framing
  F0 00 00 15 68 TT II VV AX <nibble pairs> F7
    TT dump type (10h = one kit, 12h = all 30 kits), II instrument ID,
    VV software version, AX aux (kit number).
  Every internal byte is sent as two bytes: high nibble, then low nibble.
  Most values are additionally stored nibble-swapped ("swapped" below).

Kit = 592 internal bytes = 117-byte header + 19 surfaces x 25 bytes.

Header
  0-11    kit name, swapped ASCII, space padded
  12-30   mode per surface, swapped index into MODE_NAMES
  31-49   link target per surface, swapped surface index, FF = none
  54-65   six program-change slots, 2 bytes each: channel-1 in the low nibble,
          then swapped program number (128 = not sent).       [inferred: the
          factory defaults and the per-kit values line up with Kit Edit
          screens 11/12; where the slot's side and volume live is unknown]
  66      tempo, swapped; BPM = 10000 / value (40..255 -> 250..39.2 BPM)
  86-88   Foot-Chick channel (low nibble) / swapped note / velocity [inferred]
  rest    unmapped (kept verbatim)

Surface (Pad 1-10, Trigger 1-9): 1 side byte + 24 data bytes
  side byte  two bits per sound: 00 None, 01 Right, 10 Left, 11 Both.
             Sound 1 = bits 5:4 (confirmed); sounds 2/3/4 = bits 7:6, 1:0,
             3:2 [inferred from kits that switch unused sounds off].
  data       four 6-byte sounds (confirmed by the "37 74 65 63" Multiple dump):
    +0  (curve-1) << 4 | (channel-1)
    +1  note, swapped; 128 = OFF
    +2  velocity: (min // 8) << 4 | (max - 7) // 8
    +3  gate time, swapped index into GATE_TABLE
    +4  swapped, meaning depends on mode:
          Velocity Shift  low end of the sound's dynamic range   [inferred]
          Note Shift      top note of the range                  [inferred]
          Gate Shift      longest gate (GATE_TABLE index)        [inferred]
          Multiple        probably the delay time                [unverified]
    +5  swapped: Velocity Shift = high end of the dynamic range  [inferred];
        Control mode (sound 1 only) = control type.

  Simple, Note Shift, Gate Shift, FC Gate and Control use sound 1 only.
  The 8-note modes (Hat Note, Alternate 8, Random 8, Melodic Random) keep
  sound 1's bytes +0..+3 and store notes 2-8 as (channel, note) pairs at
  +4/+5, +6/+7 ... +16/+17: channel-1 in the low nibble, swapped note. They
  share sound 1's velocity, curve, gate and side.
"""

from dataclasses import dataclass

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
NOTE_OFF = 128          # note value meaning "this slot is off"

SURFACES = [f'Pad {i}' for i in range(1, 11)] + [f'Trigger {i}' for i in range(1, 10)]
NUM_SURFACES = len(SURFACES)
NUM_KITS = 30

MODE_NAMES = [
    'Simple', 'Multiple', 'Alternate', 'Note Shift', 'Gate Shift',
    'Velocity Shift', 'Hat Open', 'FC Gate', 'Hat Note', 'Alternate 8',
    'Random 8', 'Melodic Multiple', 'Melodic Alternate', 'Melodic Random',
    'Melodic Note Shift', 'Melodic Velocity Shift', 'Control',
]
MODE_NOTE_SHIFT, MODE_GATE_SHIFT, MODE_VELOCITY_SHIFT, MODE_CONTROL = 3, 4, 5, 16
MODES_4_SOUNDS = {1, 2, 5, 6, 11, 12, 14, 15}   # four independent sounds
MODES_8_NOTES = {8, 9, 10, 13}                  # one sound + seven extra notes
MODES_WITH_PORT = set(range(1, 16))             # everything but Simple and Control

# Indexed by the value the hardware stores (Home Base = 5 and Alt Reset = 6
# are confirmed by dumps; the rest follows the order the unit cycles through).
CONTROL_TYPE_NAMES = [
    'Tap Tempo', 'Motif', 'Ext Midi Clock', 'Pressure', 'Ready Record',
    'Home Base', 'Alt Reset', 'Motif Resync', 'Kit Advance', 'Kit Reverse',
    'Note Freeze Alt', 'Group Step Transpose', 'Group Step Trans R',
    'Auto Pad Transpose', 'AutoPadTrans and Home', 'Program Change',
    'Kit Change', 'Tempo Change',
]

MIDI_PORT_NAMES = ['None', 'Right', 'Left', 'Both']   # index = stored 2-bit value
_PORT_SHIFTS = (4, 6, 0, 2)                           # bit position per sound

# Gate times: index 0 = Latch, 1-253 = seconds, 254 = No OFF.
GATE_LATCH, GATE_NO_OFF = 0, 254
GATE_TABLE = ([None]
              + [ms / 1000 for ms in range(10, 401, 5)]
              + [ms / 1000 for ms in range(425, 4226, 25)]
              + [ms / 1000 for ms in range(4300, 6301, 100)]
              + [None])

KAT_MANUFACTURER = bytes((0x00, 0x00, 0x15))
DRUMKAT_INSTRUMENT_ID = 0x68
DUMP_TYPE_KIT = 0x10
DUMP_TYPE_ALL_KITS = 0x12
SYSEX_HEADER_SIZE = 9

HEADER_SIZE = 117
SURFACE_SIZE = 25       # side byte + 24 data bytes
SOUND_SIZE = 6
KIT_SIZE = HEADER_SIZE + NUM_SURFACES * SURFACE_SIZE    # 592

NAME_LENGTH = 12
MODE_BASE_OFFSET = 12
LINK_BASE_OFFSET = 31
PROGRAM_BASE_OFFSET = 54
NUM_PROGRAM_SLOTS = 6
TEMPO_OFFSET = 66
TEMPO_SCALE = 10000
TEMPO_STORED_MIN, TEMPO_STORED_MAX = 40, 255            # 250 .. 39.2 BPM
TEMPO_MIN = round(TEMPO_SCALE / TEMPO_STORED_MAX, 1)
TEMPO_MAX = TEMPO_SCALE / TEMPO_STORED_MIN


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def swap_nibbles(b: int) -> int:
    return ((b & 0x0F) << 4) | (b >> 4)


def _check(value: int, lo: int, hi: int, what: str) -> int:
    if not lo <= value <= hi:
        raise ValueError(f'{what} must be {lo}-{hi}, got {value}')
    return value


def _decode_nibbles(payload: bytes) -> list[int]:
    if len(payload) % 2:
        raise ValueError(f'SysEx payload has an odd length ({len(payload)})')
    return [((payload[i] & 0x0F) << 4) | (payload[i + 1] & 0x0F)
            for i in range(0, len(payload), 2)]


def _encode_nibbles(data: list[int]) -> bytes:
    return bytes(n for b in data for n in (b >> 4, b & 0x0F))


def tempo_from_stored(stored: int) -> float:
    stored = min(max(stored, TEMPO_STORED_MIN), TEMPO_STORED_MAX)
    return round(TEMPO_SCALE / stored, 1)


def tempo_to_stored(bpm: float) -> int:
    return min(max(round(TEMPO_SCALE / max(bpm, 1)), TEMPO_STORED_MIN), TEMPO_STORED_MAX)


def gate_label(gate_index: int) -> str:
    if gate_index == GATE_LATCH:
        return 'Latch'
    if gate_index == GATE_NO_OFF:
        return 'No OFF'
    return f'{GATE_TABLE[gate_index]:.3f}s'


def note_name(midi_note: int) -> str:
    if midi_note == NOTE_OFF:
        return 'OFF'
    if not 0 <= midi_note <= 127:
        return '???'
    return f'{NOTE_NAMES[midi_note % 12]}{midi_note // 12 - 1}'


def _swapped(offset: int, lo: int, hi: int, what: str, doc: str) -> property:
    """Property for a nibble-swapped byte at `offset` from the owner's base."""
    def get(self):
        return min(swap_nibbles(self._block[self._base + offset]), hi)

    def put(self, value):
        self._block[self._base + offset] = swap_nibbles(_check(value, lo, hi, what))

    return property(get, put, doc=doc)


# ---------------------------------------------------------------------------
# Sounds and surfaces
# ---------------------------------------------------------------------------

class NoteSlot:
    """A (channel, note) pair inside a surface block."""

    def __init__(self, block: list[int], channel_offset: int, note_offset: int):
        self._block = block
        self._base = 0
        self._ch = channel_offset
        self._note = note_offset

    @property
    def channel(self) -> int:
        """MIDI channel 1-16."""
        return (self._block[self._ch] & 0x0F) + 1

    @channel.setter
    def channel(self, value: int):
        self._block[self._ch] = (self._block[self._ch] & 0xF0) | (_check(value, 1, 16, 'channel') - 1)

    @property
    def note(self) -> int:
        """MIDI note 0-127, or NOTE_OFF."""
        return min(swap_nibbles(self._block[self._note]), NOTE_OFF)

    @note.setter
    def note(self, value: int):
        self._block[self._note] = swap_nibbles(_check(value, 0, NOTE_OFF, 'note'))


class Sound(NoteSlot):
    """One of the four 6-byte sounds of a surface."""

    def __init__(self, block: list[int], ports: list[int], surface_index: int, index: int):
        base = index * SOUND_SIZE
        super().__init__(block, base, base + 1)
        self._base = base
        self._ports = ports
        self._surface_index = surface_index
        self._port_shift = _PORT_SHIFTS[index]

    @property
    def curve(self) -> int:
        """Velocity curve 1-16."""
        return (self._block[self._base] >> 4) + 1

    @curve.setter
    def curve(self, value: int):
        self._block[self._base] = ((_check(value, 1, 16, 'curve') - 1) << 4) | (self._block[self._base] & 0x0F)

    @property
    def vel_min(self) -> int:
        """Minimum velocity: 0, 8, 16 ... 120."""
        return (self._block[self._base + 2] >> 4) * 8

    @vel_min.setter
    def vel_min(self, value: int):
        self._block[self._base + 2] = ((_check(value, 0, 120, 'vel_min') // 8) << 4) | (self._block[self._base + 2] & 0x0F)

    @property
    def vel_max(self) -> int:
        """Maximum velocity: 7, 15, 23 ... 127."""
        return (self._block[self._base + 2] & 0x0F) * 8 + 7

    @vel_max.setter
    def vel_max(self, value: int):
        self._block[self._base + 2] = (self._block[self._base + 2] & 0xF0) | ((_check(value, 7, 127, 'vel_max') - 7) // 8)

    gate = _swapped(3, 0, GATE_NO_OFF, 'gate', 'GATE_TABLE index (0 = Latch, 254 = No OFF).')
    dyn_min = _swapped(4, 0, 127, 'dyn_min', 'Velocity Shift: softest hit that plays this sound.')
    dyn_max = _swapped(5, 0, 127, 'dyn_max', 'Velocity Shift: hardest hit that plays this sound.')

    @property
    def port(self) -> int:
        """Index into MIDI_PORT_NAMES. Ignored by the unit in Simple and Control modes."""
        return (self._ports[self._surface_index] >> self._port_shift) & 3

    @port.setter
    def port(self, value: int):
        cleared = self._ports[self._surface_index] & ~(3 << self._port_shift)
        self._ports[self._surface_index] = cleared | (_check(value, 0, 3, 'port') << self._port_shift)

    def describe(self) -> str:
        return (f'ch={self.channel} note={self.note}({note_name(self.note)}) '
                f'vel={self.vel_min}-{self.vel_max} curve={self.curve} gate={gate_label(self.gate)}')


class Surface:
    """A pad or trigger. `sounds[0]` is the sound every mode uses."""

    def __init__(self, index: int, block: list[int], kit_header: list[int], ports: list[int]):
        self.index = index
        self.name = SURFACES[index]
        self._block = list(block)
        self._base = 0
        self._header = kit_header       # shared with the Kit
        self.sounds = [Sound(self._block, ports, index, k) for k in range(4)]
        self.notes8 = [self.sounds[0]] + [NoteSlot(self._block, 2 + 2 * n, 3 + 2 * n) for n in range(1, 8)]

    @property
    def mode(self) -> int:
        return swap_nibbles(self._header[MODE_BASE_OFFSET + self.index])

    @mode.setter
    def mode(self, value: int):
        self._header[MODE_BASE_OFFSET + self.index] = swap_nibbles(_check(value, 0, len(MODE_NAMES) - 1, 'mode'))

    @property
    def mode_name(self) -> str:
        return MODE_NAMES[self.mode] if self.mode < len(MODE_NAMES) else f'Mode{self.mode}'

    @property
    def slots(self) -> list[NoteSlot]:
        """The notes this surface's mode can play: 4 sounds, 8 notes, or 1 sound."""
        if self.mode in MODES_4_SOUNDS:
            return self.sounds
        if self.mode in MODES_8_NOTES:
            return self.notes8
        return self.sounds[:1]

    @property
    def link(self) -> int | None:
        """Linked surface index, or None."""
        idx = swap_nibbles(self._header[LINK_BASE_OFFSET + self.index])
        return idx if idx < NUM_SURFACES else None

    @link.setter
    def link(self, value: int | None):
        self._header[LINK_BASE_OFFSET + self.index] = (
            0xFF if value is None else swap_nibbles(_check(value, 0, NUM_SURFACES - 1, 'link')))

    control_type = _swapped(5, 0, 255, 'control_type', 'Index into CONTROL_TYPE_NAMES (Control mode).')
    shift_end = _swapped(4, 0, 255, 'shift_end',
                         'Note Shift: top note. Gate Shift: longest gate (GATE_TABLE index).')

    @property
    def control_type_name(self) -> str:
        ct = self.control_type
        return CONTROL_TYPE_NAMES[ct] if ct < len(CONTROL_TYPE_NAMES) else f'?({ct})'

    def raw(self) -> list[int]:
        return list(self._block)

    def __repr__(self):
        mode = self.mode
        parts = [f'{self.name}: {self.mode_name}']
        if mode in MODES_8_NOTES:
            parts.append(self.sounds[0].describe())
            parts.append('notes=' + ','.join(f'{s.note}/ch{s.channel}' for s in self.notes8))
        else:
            parts += [f'[{s.describe()}]' for s in self.slots]
        if mode in MODES_WITH_PORT:
            parts.append('port=' + '/'.join(MIDI_PORT_NAMES[s.port] for s in self.slots if isinstance(s, Sound)))
        if mode == MODE_CONTROL:
            parts.append(f'control={self.control_type_name}')
        if self.link is not None:
            parts.append(f'link={SURFACES[self.link]}')
        return ' '.join(parts)


# Shortcuts so `surface.note`, `surface.channel` ... address sound 1.
def _first_sound(attr: str) -> property:
    return property(lambda self: getattr(self.sounds[0], attr),
                    lambda self, value: setattr(self.sounds[0], attr, value))


for _attr in ('channel', 'note', 'curve', 'vel_min', 'vel_max', 'gate', 'port'):
    setattr(Surface, _attr, _first_sound(_attr))


# ---------------------------------------------------------------------------
# SysEx framing
# ---------------------------------------------------------------------------

@dataclass
class SysExHeader:
    dump_type: int
    instrument_id: int
    sw_version: int
    aux: int


def parse_sysex(raw: bytes) -> tuple[SysExHeader, list[int]]:
    """Validate a drumKAT SysEx message; return its header and internal bytes."""
    if len(raw) < SYSEX_HEADER_SIZE + 1 or raw[0] != 0xF0 or raw[-1] != 0xF7:
        raise ValueError('Not a SysEx file (missing F0 ... F7 framing)')
    if raw[1:4] != KAT_MANUFACTURER:
        raise ValueError(f'Not a KAT SysEx file (manufacturer {raw[1:4].hex()})')
    if raw[4] != DRUMKAT_INSTRUMENT_ID:
        raise ValueError(f'Not a drumKAT dump (instrument 0x{raw[4]:02X})')
    return SysExHeader(*raw[5:9]), _decode_nibbles(raw[SYSEX_HEADER_SIZE:-1])


def _build_sysex(h: SysExHeader, internal: list[int]) -> bytes:
    head = bytes((0xF0, *KAT_MANUFACTURER, DRUMKAT_INSTRUMENT_ID,
                  h.dump_type, h.instrument_id, h.sw_version, h.aux))
    return head + _encode_nibbles(internal) + b'\xF7'


# ---------------------------------------------------------------------------
# Kit and kit bank
# ---------------------------------------------------------------------------

class Kit:
    def __init__(self, header: SysExHeader, internal: list[int]):
        if len(internal) != KIT_SIZE:
            raise ValueError(f'Kit data must be {KIT_SIZE} bytes, got {len(internal)}')
        self.sysex_header = header
        self._header = list(internal[:HEADER_SIZE])
        starts = range(HEADER_SIZE, KIT_SIZE, SURFACE_SIZE)
        self._ports = [internal[s] for s in starts]
        self.surfaces = [Surface(i, internal[s + 1:s + SURFACE_SIZE], self._header, self._ports)
                         for i, s in enumerate(starts)]

    def pad(self, n: int) -> Surface:
        """pad(1) .. pad(10)."""
        return self.surfaces[_check(n, 1, 10, 'pad') - 1]

    def trigger(self, n: int) -> Surface:
        """trigger(1) .. trigger(9)."""
        return self.surfaces[_check(n, 1, 9, 'trigger') + 9]

    @property
    def name(self) -> str:
        chars = (swap_nibbles(b) for b in self._header[:NAME_LENGTH])
        return ''.join(chr(c) if 32 <= c < 127 else '?' for c in chars).rstrip()

    @name.setter
    def name(self, value: str):
        text = value[:NAME_LENGTH].ljust(NAME_LENGTH)
        self._header[:NAME_LENGTH] = [swap_nibbles(ord(c) if 32 <= ord(c) < 127 else ord('?')) for c in text]

    @property
    def tempo(self) -> float:
        """BPM, to one decimal. Only 216 tempos exist (10000 / 40..255)."""
        return tempo_from_stored(swap_nibbles(self._header[TEMPO_OFFSET]))

    @tempo.setter
    def tempo(self, bpm: float):
        self._header[TEMPO_OFFSET] = swap_nibbles(tempo_to_stored(bpm))

    def program_change(self, slot: int) -> tuple[int, int | None]:
        """(channel 1-16, program 0-127 or None) for slot 0-5."""
        o = PROGRAM_BASE_OFFSET + 2 * _check(slot, 0, NUM_PROGRAM_SLOTS - 1, 'slot')
        program = swap_nibbles(self._header[o + 1])
        return (self._header[o] & 0x0F) + 1, program if program < 128 else None

    def set_program_change(self, slot: int, channel: int, program: int | None):
        o = PROGRAM_BASE_OFFSET + 2 * _check(slot, 0, NUM_PROGRAM_SLOTS - 1, 'slot')
        self._header[o] = (self._header[o] & 0xF0) | (_check(channel, 1, 16, 'channel') - 1)
        self._header[o + 1] = swap_nibbles(128 if program is None else _check(program, 0, 127, 'program'))

    def to_internal(self) -> list[int]:
        data = list(self._header)
        for port, surface in zip(self._ports, self.surfaces):
            data.append(port)
            data += surface.raw()
        return data

    def to_sysex(self) -> bytes:
        return _build_sysex(self.sysex_header, self.to_internal())

    def __repr__(self):
        return '\n'.join([f'Kit "{self.name}" {self.tempo} BPM'] + [f'  {s}' for s in self.surfaces])


class KitBank:
    """All 30 kits from an All Kits dump."""

    def __init__(self, header: SysExHeader, internal: list[int]):
        if len(internal) != NUM_KITS * KIT_SIZE:
            raise ValueError(f'All Kits data must be {NUM_KITS * KIT_SIZE} bytes, got {len(internal)}')
        self.sysex_header = header
        self.kits = [Kit(SysExHeader(DUMP_TYPE_KIT, header.instrument_id, header.sw_version, i),
                         internal[i * KIT_SIZE:(i + 1) * KIT_SIZE])
                     for i in range(NUM_KITS)]

    def kit(self, n: int) -> Kit:
        """kit(1) .. kit(30)."""
        return self.kits[_check(n, 1, NUM_KITS, 'kit') - 1]

    def to_sysex(self) -> bytes:
        return _build_sysex(self.sysex_header, [b for k in self.kits for b in k.to_internal()])

    def __repr__(self):
        return '\n'.join(['KitBank:'] + [f'  [{i:2d}] {k.name}' for i, k in enumerate(self.kits, 1)])


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def load(path: str) -> Kit | KitBank:
    """Load a single-kit or All Kits dump, whichever the file contains."""
    with open(path, 'rb') as f:
        header, internal = parse_sysex(f.read())
    if header.dump_type == DUMP_TYPE_ALL_KITS:
        return KitBank(header, internal)
    if header.dump_type == DUMP_TYPE_KIT:
        return Kit(header, internal)
    raise ValueError(f'Unsupported dump type 0x{header.dump_type:02X} (expected a kit or All Kits dump)')


def _load_as(path: str, kind: type):
    obj = load(path)
    if not isinstance(obj, kind):
        raise ValueError(f'{path} is not a {kind.__name__} dump')
    return obj


def load_kit(path: str) -> Kit:
    return _load_as(path, Kit)


def load_kit_bank(path: str) -> KitBank:
    return _load_as(path, KitBank)


def save(obj: Kit | KitBank, path: str):
    with open(path, 'wb') as f:
        f.write(obj.to_sysex())


if __name__ == '__main__':
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else 'Kit 1.syx'
    loaded = load(path)
    print(loaded)
    with open(path, 'rb') as f:
        same = f.read() == loaded.to_sysex()
    print('Round-trip:', 'PASS' if same else 'FAIL')
