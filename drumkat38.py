"""
drumKAT 3.8 SysEx parser / builder.

Confirmed byte map (from live diffing, May 2026):
  Header: 9 SysEx bytes + 118 internal bytes (kit-level data)
    header[12..30]: mode byte for each of the 19 surfaces (stride=1)
      surface 0 (Pad 1) = header[12], surface 1 (Pad 2) = header[13], ...
      mode encoding: nibble-swapped index  (0x00=Simple, 0x10=Multiple, ...)
    header[31..49]: link target per surface; nibble-swap encoded surface index; 0xFF = no link

  Pad blocks: 19 x 25 internal bytes starting at internal offset 118
    Surfaces: Pad 1-10 (index 0-9), Trigger 1-9 (index 10-18)
    block_start = 118 + pad_index * 25   (pad_index 0-based)
    +0  MIDI channel   stored = channel - 1  (0=ch1, 9=ch10, ...)
    +1  MIDI note      nibble-swapped: stored = ((note&0xF)<<4)|(note>>4)
    +2  Velocity       stored = vel*2 + 15;  range 0-120
    +3..+22  partially mapped:
      +7, +9, +19  additional note slots for Multiple mode (notes 2, 3, 4)
    +23..+24  0x30 0xFF  block terminator

SysEx header (9 bytes):
  F0 00 00 15 68 TT II VV AX
    TT = dump type  (10h = single kit)
    II = instrument ID
    VV = software version
    AX = aux (kit number, etc.)
"""

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# Gate time lookup table — index 0=LATCH, 254=No OFF, 1-253=seconds
# Stored byte = nibble_swap(index)
def _build_gate_table() -> list:
    t = [None]  # index 0 = LATCH
    v = 0.010
    while round(v, 3) <= 0.400:
        t.append(round(v, 3)); v += 0.005
    v = 0.425
    while round(v, 3) <= 4.225:
        t.append(round(v, 3)); v += 0.025
    v = 4.300
    while round(v, 3) <= 6.300:
        t.append(round(v, 3)); v += 0.100
    t.append(None)  # index 254 = No OFF
    return t

GATE_TABLE = _build_gate_table()
GATE_LATCH  = 0      # index
GATE_NO_OFF = 254    # index

SURFACES = (
    [f'Pad {i+1}' for i in range(10)]
    + [f'Trigger {i+1}' for i in range(9)]
)

# Mode names indexed 0-16 (hardware displays 1-17)
CONTROL_TYPE_NAMES = [
    'Home Base',              # 0
    'Alt Reset',              # 1
    'Motif Resync',           # 2
    'Kit Advance',            # 3
    'Kit Reverse',            # 4
    'Note Freeze Alt',        # 5
    'Group Step Transpose',   # 6
    'Group Step Trans R',     # 7
    'Auto Pad Transpose',     # 8
    'AutoPadTrans and Home',  # 9
    'Program Change',         # 10
    'Kit Change',             # 11
    'Tempo Change',           # 12
    'Tap Tempo',              # 13
    'Motif',                  # 14
    'Ext Midi Clock',         # 15
    'Pressure',               # 16
    'Ready Record',           # 17
]

MODE_NAMES = [
    'Simple',               # 0
    'Multiple',             # 1
    'Alternate',            # 2
    'Note Shift',           # 3
    'Gate Shift',           # 4
    'Velocity Shift',       # 5
    'Hat Open',             # 6
    'FC Gate',              # 7
    'Hat Note',             # 8
    'Alternate 8',          # 9
    'Random 8',             # 10
    'Melodic Multiple',     # 11
    'Melodic Alternate',    # 12
    'Melodic Random',       # 13
    'Melodic Note Shift',   # 14
    'Melodic Velocity Shift', # 15
    'Control',              # 16
]

KAT_MANUFACTURER = (0x00, 0x00, 0x15)
DRUMKAT_INSTRUMENT_ID = 0x68
DUMP_TYPE_KIT      = 0x10
DUMP_TYPE_ALL_KITS = 0x12
NUM_KITS = 30

HEADER_SIZE = 118       # internal bytes before first pad block
BLOCK_SIZE = 25         # internal bytes per pad/trigger block
NUM_SURFACES = 19

NAME_OFFSET = 0         # header bytes 0-11: kit name, nibble-swapped ASCII, space-padded
NAME_LENGTH = 12
MODE_BASE_OFFSET = 12   # header bytes 12-30: mode byte per surface, nibble-swap encoded
LINK_BASE_OFFSET = 31   # header bytes 31-49: link target per surface, nibble-swap encoded surface index; 0xFF = no link

# Offsets within each 25-byte block
OFF_CHANNEL      = 0
OFF_NOTE         = 1    # note 1
OFF_VELOCITY     = 2
OFF_GATE         = 3
OFF_NOTE2        = 5    # note 2  (stride-2 from +5; confirmed Alternate 8 dump)
OFF_NOTE3        = 7    # note 3
OFF_NOTE4        = 9    # note 4
OFF_NOTE5        = 11   # note 5
OFF_NOTE6        = 13   # note 6
OFF_NOTE7        = 15   # note 7
OFF_NOTE8        = 17   # note 8
# Control mode repurposes block+5 for control type (overlaps with OFF_NOTE2)
OFF_CONTROL_TYPE = 5    # stored = nibble_swap(control_type_index + CONTROL_TYPE_OFFSET)
CONTROL_TYPE_OFFSET = 5 # hardware internal index of CONTROL_TYPE_NAMES[0]


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def _decode_nibbles(payload: bytes) -> list[int]:
    """Convert nibble-encoded SysEx payload to internal bytes."""
    out = []
    for i in range(0, len(payload) - 1, 2):
        hi = payload[i] & 0x0F
        lo = payload[i + 1] & 0x0F
        out.append((hi << 4) | lo)
    return out


def _encode_nibbles(data: list[int]) -> bytes:
    """Convert internal bytes to nibble-encoded SysEx payload."""
    out = []
    for b in data:
        out.append((b >> 4) & 0x0F)
        out.append(b & 0x0F)
    return bytes(out)


def _swap_nibbles(b: int) -> int:
    return ((b & 0x0F) << 4) | ((b >> 4) & 0x0F)


def encode_note(midi_note: int) -> int:
    assert 0 <= midi_note <= 127
    return _swap_nibbles(midi_note)


def decode_note(stored: int) -> int:
    return _swap_nibbles(stored)


def encode_channel_curve(channel: int, curve: int) -> int:
    """channel is 1-16, curve is 1-16.
    Stored as ((curve-1) << 4) | (channel-1)."""
    assert 1 <= channel <= 16
    assert 1 <= curve <= 16
    return ((curve - 1) << 4) | (channel - 1)


def decode_channel_curve(stored: int) -> tuple[int, int]:
    """Returns (channel, curve): both 1-16."""
    return (stored & 0x0F) + 1, (stored >> 4) + 1


def encode_velocity(vel_min: int, vel_max: int) -> int:
    """Pack velMin (0,8,16..120) and velMax (7,15,23..127) into one byte.
    Upper nibble = velMin // 8; lower nibble = (velMax - 7) // 8."""
    assert 0 <= vel_min <= 120 and vel_min % 8 == 0
    assert 7 <= vel_max <= 127 and (vel_max - 7) % 8 == 0
    return ((vel_min // 8) << 4) | ((vel_max - 7) // 8)


def decode_velocity(stored: int) -> tuple[int, int]:
    """Returns (velMin, velMax): velMin in steps of 8 (0-120),
    velMax in steps of 8 offset by 7 (7-127)."""
    return (stored >> 4) * 8, (stored & 0x0F) * 8 + 7


def encode_mode(mode_index: int) -> int:
    """mode_index is 0-16; stored as nibble-swapped index."""
    assert 0 <= mode_index <= 16
    return _swap_nibbles(mode_index)


def decode_mode(stored: int) -> int:
    return _swap_nibbles(stored)


def encode_control_type(idx: int) -> int:
    """idx is 0-based index into CONTROL_TYPE_NAMES."""
    assert 0 <= idx < len(CONTROL_TYPE_NAMES)
    return _swap_nibbles(idx + CONTROL_TYPE_OFFSET)


def decode_control_type(stored: int) -> int:
    """Returns 0-based index into CONTROL_TYPE_NAMES."""
    return _swap_nibbles(stored) - CONTROL_TYPE_OFFSET


def encode_gate(gate_index: int) -> int:
    """gate_index: 0=LATCH, 1-253=seconds (see GATE_TABLE), 254=No OFF."""
    assert 0 <= gate_index <= 254
    return _swap_nibbles(gate_index)


def decode_gate(stored: int) -> int:
    """Returns gate table index (0=LATCH, 1-253=seconds, 254=No OFF)."""
    return _swap_nibbles(stored)


def gate_label(gate_index: int) -> str:
    if gate_index == GATE_LATCH:
        return 'Latch'
    if gate_index == GATE_NO_OFF:
        return 'No OFF'
    v = GATE_TABLE[gate_index]
    return f'{v:.3f}s' if v is not None else f'?({gate_index})'


def note_name(midi_note: int) -> str:
    if not 0 <= midi_note <= 127:
        return '???'
    return f'{NOTE_NAMES[midi_note % 12]}{(midi_note // 12) - 1}'


# ---------------------------------------------------------------------------
# SysEx header
# ---------------------------------------------------------------------------

class SysExHeader:
    def __init__(self, dump_type, instrument_id, sw_version, aux):
        self.dump_type     = dump_type
        self.instrument_id = instrument_id
        self.sw_version    = sw_version
        self.aux           = aux

    def __repr__(self):
        return (f'SysExHeader(dump_type=0x{self.dump_type:02X}, '
                f'instrument_id=0x{self.instrument_id:02X}, '
                f'sw_version=0x{self.sw_version:02X}, aux=0x{self.aux:02X})')


def parse_sysex_header(raw: bytes) -> SysExHeader:
    assert raw[0] == 0xF0, 'Not a SysEx message'
    assert raw[1:4] == bytes(KAT_MANUFACTURER), f'Unknown manufacturer: {raw[1:4].hex()}'
    assert raw[4] == DRUMKAT_INSTRUMENT_ID, f'Unknown instrument: 0x{raw[4]:02X}'
    return SysExHeader(
        dump_type     = raw[5],
        instrument_id = raw[6],
        sw_version    = raw[7],
        aux           = raw[8],
    )


# ---------------------------------------------------------------------------
# Pad / trigger surface
# ---------------------------------------------------------------------------

class Surface:
    def __init__(self, index: int, name: str, raw_block: list[int], kit_header: list[int]):
        self.index = index
        self.name  = name
        self._block = list(raw_block)       # 25 raw internal bytes
        self._kit_header = kit_header       # shared reference to Kit._header_bytes

    @property
    def mode(self) -> int:
        return decode_mode(self._kit_header[MODE_BASE_OFFSET + self.index])

    @mode.setter
    def mode(self, value: int):
        self._kit_header[MODE_BASE_OFFSET + self.index] = encode_mode(value)

    @property
    def mode_name(self) -> str:
        m = self.mode
        return MODE_NAMES[m] if m < len(MODE_NAMES) else f'Mode{m}'

    @property
    def channel(self) -> int:
        """MIDI channel 1-16."""
        return decode_channel_curve(self._block[OFF_CHANNEL])[0]

    @channel.setter
    def channel(self, value: int):
        _, crv = decode_channel_curve(self._block[OFF_CHANNEL])
        self._block[OFF_CHANNEL] = encode_channel_curve(value, crv)

    @property
    def curve(self) -> int:
        """Velocity curve 1-16."""
        return decode_channel_curve(self._block[OFF_CHANNEL])[1]

    @curve.setter
    def curve(self, value: int):
        ch, _ = decode_channel_curve(self._block[OFF_CHANNEL])
        self._block[OFF_CHANNEL] = encode_channel_curve(ch, value)

    @property
    def link(self) -> int | None:
        """Linked surface index (0-based), or None if not linked."""
        stored = self._kit_header[LINK_BASE_OFFSET + self.index]
        if stored == 0xFF:
            return None
        idx = _swap_nibbles(stored)
        return idx if idx < NUM_SURFACES else None

    @link.setter
    def link(self, value: int | None):
        """Set linked surface index (0-18), or None to clear."""
        if value is None:
            self._kit_header[LINK_BASE_OFFSET + self.index] = 0xFF
        else:
            assert 0 <= value <= 18
            self._kit_header[LINK_BASE_OFFSET + self.index] = _swap_nibbles(value)

    @property
    def link_name(self) -> str | None:
        idx = self.link
        return SURFACES[idx] if idx is not None else None

    @property
    def control_type(self) -> int:
        """0-based index into CONTROL_TYPE_NAMES. Only meaningful when mode == 16 (Control)."""
        return decode_control_type(self._block[OFF_CONTROL_TYPE])

    @control_type.setter
    def control_type(self, idx: int):
        self._block[OFF_CONTROL_TYPE] = encode_control_type(idx)

    @property
    def control_type_name(self) -> str:
        idx = self.control_type
        return CONTROL_TYPE_NAMES[idx] if 0 <= idx < len(CONTROL_TYPE_NAMES) else f'?({idx})'

    @property
    def note(self) -> int:
        return decode_note(self._block[OFF_NOTE])

    @note.setter
    def note(self, value: int):
        self._block[OFF_NOTE] = encode_note(value)

    @property
    def gate(self) -> int:
        """Gate time table index (0=LATCH, 1-253=seconds, 254=No OFF)."""
        return decode_gate(self._block[OFF_GATE])

    @gate.setter
    def gate(self, value: int):
        self._block[OFF_GATE] = encode_gate(value)

    @property
    def gate_label(self) -> str:
        return gate_label(self.gate)

    @property
    def vel_min(self) -> int:
        return decode_velocity(self._block[OFF_VELOCITY])[0]

    @vel_min.setter
    def vel_min(self, value: int):
        _, vmax = decode_velocity(self._block[OFF_VELOCITY])
        self._block[OFF_VELOCITY] = encode_velocity(value, vmax)

    @property
    def vel_max(self) -> int:
        return decode_velocity(self._block[OFF_VELOCITY])[1]

    @vel_max.setter
    def vel_max(self, value: int):
        vmin, _ = decode_velocity(self._block[OFF_VELOCITY])
        self._block[OFF_VELOCITY] = encode_velocity(vmin, value)

    @property
    def note2(self) -> int:
        return decode_note(self._block[OFF_NOTE2])

    @note2.setter
    def note2(self, value: int):
        self._block[OFF_NOTE2] = encode_note(value)

    @property
    def note3(self) -> int:
        return decode_note(self._block[OFF_NOTE3])

    @note3.setter
    def note3(self, value: int):
        self._block[OFF_NOTE3] = encode_note(value)

    @property
    def note4(self) -> int:
        return decode_note(self._block[OFF_NOTE4])

    @note4.setter
    def note4(self, value: int):
        self._block[OFF_NOTE4] = encode_note(value)

    @property
    def note5(self) -> int:
        return decode_note(self._block[OFF_NOTE5])

    @note5.setter
    def note5(self, value: int):
        self._block[OFF_NOTE5] = encode_note(value)

    @property
    def note6(self) -> int:
        return decode_note(self._block[OFF_NOTE6])

    @note6.setter
    def note6(self, value: int):
        self._block[OFF_NOTE6] = encode_note(value)

    @property
    def note7(self) -> int:
        return decode_note(self._block[OFF_NOTE7])

    @note7.setter
    def note7(self, value: int):
        self._block[OFF_NOTE7] = encode_note(value)

    @property
    def note8(self) -> int:
        return decode_note(self._block[OFF_NOTE8])

    @note8.setter
    def note8(self, value: int):
        self._block[OFF_NOTE8] = encode_note(value)

    def raw(self) -> list[int]:
        return list(self._block)

    def __repr__(self):
        base = (f'{self.name}: mode={self.mode_name} ch={self.channel} curve={self.curve} '
                f'note={self.note}({note_name(self.note)}) '
                f'velMin={self.vel_min} velMax={self.vel_max} gate={self.gate_label}')
        MODES_4 = {1, 2, 5, 11, 12, 14, 15}
        MODES_8 = {9, 10, 13}
        m = self.mode
        if m in MODES_4:
            base += (f' note2={self.note2}({note_name(self.note2)})'
                     f' note3={self.note3}({note_name(self.note3)})'
                     f' note4={self.note4}({note_name(self.note4)})')
        elif m in MODES_8:
            base += (f' note2={self.note2}({note_name(self.note2)})'
                     f' note3={self.note3}({note_name(self.note3)})'
                     f' note4={self.note4}({note_name(self.note4)})'
                     f' note5={self.note5}({note_name(self.note5)})'
                     f' note6={self.note6}({note_name(self.note6)})'
                     f' note7={self.note7}({note_name(self.note7)})'
                     f' note8={self.note8}({note_name(self.note8)})')
        if self.mode == 16:  # Control
            base += f' control_type={self.control_type_name}'
        if self.link is not None:
            base += f' link={self.link_name}'
        return base


# ---------------------------------------------------------------------------
# Kit
# ---------------------------------------------------------------------------

class Kit:
    def __init__(self, header: SysExHeader, internal: list[int]):
        self.sysex_header = header
        self._internal_size = len(internal)     # preserve original byte count
        self._header_bytes = list(internal[:HEADER_SIZE])
        self.surfaces: list[Surface] = []
        for i, name in enumerate(SURFACES):
            start = HEADER_SIZE + i * BLOCK_SIZE
            block = internal[start:start + BLOCK_SIZE]
            if len(block) < BLOCK_SIZE:
                block += [0xFF] * (BLOCK_SIZE - len(block))
            self.surfaces.append(Surface(i, name, block, self._header_bytes))

    def pad(self, n: int) -> Surface:
        """1-based pad access: pad(1) .. pad(10)."""
        assert 1 <= n <= 10
        return self.surfaces[n - 1]

    def trigger(self, n: int) -> Surface:
        """1-based trigger access: trigger(1) .. trigger(9)."""
        assert 1 <= n <= 9
        return self.surfaces[9 + n]

    def to_internal(self) -> list[int]:
        data = list(self._header_bytes)
        for s in self.surfaces:
            data.extend(s.raw())
        return data[:self._internal_size]   # preserve original length

    def to_sysex(self) -> bytes:
        internal = self.to_internal()
        h = self.sysex_header
        header = bytes([
            0xF0,
            *KAT_MANUFACTURER,
            DRUMKAT_INSTRUMENT_ID,
            h.dump_type,
            h.instrument_id,
            h.sw_version,
            h.aux,
        ])
        return header + _encode_nibbles(internal) + bytes([0xF7])

    @property
    def name(self) -> str:
        chars = [_swap_nibbles(self._header_bytes[NAME_OFFSET + i]) for i in range(NAME_LENGTH)]
        return ''.join(chr(c) if 32 <= c < 127 else '?' for c in chars).rstrip()

    @name.setter
    def name(self, value: str):
        padded = value[:NAME_LENGTH].ljust(NAME_LENGTH)
        for i, ch in enumerate(padded):
            self._header_bytes[NAME_OFFSET + i] = _swap_nibbles(ord(ch))

    def __repr__(self):
        lines = [f'Kit "{self.name}" (sw=0x{self.sysex_header.sw_version:02X})']
        for s in self.surfaces:
            lines.append(f'  {s}')
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def load_kit(path: str) -> Kit:
    with open(path, 'rb') as f:
        raw = f.read()
    assert raw[0] == 0xF0 and raw[-1] == 0xF7, 'Invalid SysEx file'
    header  = parse_sysex_header(raw)
    payload = raw[9:-1]
    internal = _decode_nibbles(payload)
    return Kit(header, internal)


def save_kit(kit: Kit, path: str):
    with open(path, 'wb') as f:
        f.write(kit.to_sysex())


# ---------------------------------------------------------------------------
# Kit bank (All Kits dump — 30 kits)
# ---------------------------------------------------------------------------

KIT_SIZE = 592   # internal bytes per kit (= 1184 nibble bytes)

class KitBank:
    """Holds all 30 kits from an All Kits SysEx dump."""

    def __init__(self, header: SysExHeader, internal: list[int]):
        self.sysex_header = header
        assert len(internal) == NUM_KITS * KIT_SIZE, \
            f'Expected {NUM_KITS * KIT_SIZE} bytes, got {len(internal)}'
        self.kits: list[Kit] = []
        for i in range(NUM_KITS):
            block = internal[i * KIT_SIZE:(i + 1) * KIT_SIZE]
            # Build a minimal SysExHeader for each kit slot
            slot_header = SysExHeader(DUMP_TYPE_KIT, header.instrument_id,
                                      header.sw_version, i)
            self.kits.append(Kit(slot_header, block))

    def kit(self, n: int) -> Kit:
        """1-based kit access: kit(1) .. kit(30)."""
        assert 1 <= n <= NUM_KITS
        return self.kits[n - 1]

    def to_internal(self) -> list[int]:
        data = []
        for k in self.kits:
            data.extend(k.to_internal())
        return data

    def to_sysex(self) -> bytes:
        internal = self.to_internal()
        h = self.sysex_header
        header = bytes([
            0xF0,
            *KAT_MANUFACTURER,
            DRUMKAT_INSTRUMENT_ID,
            h.dump_type,
            h.instrument_id,
            h.sw_version,
            h.aux,
        ])
        return header + _encode_nibbles(internal) + bytes([0xF7])

    def __repr__(self):
        lines = ['KitBank (30 kits):']
        for i, k in enumerate(self.kits):
            lines.append(f'  [{i+1:2d}] {k.name}')
        return '\n'.join(lines)


def load_kit_bank(path: str) -> KitBank:
    with open(path, 'rb') as f:
        raw = f.read()
    assert raw[0] == 0xF0 and raw[-1] == 0xF7, 'Invalid SysEx file'
    header = parse_sysex_header(raw)
    assert header.dump_type == DUMP_TYPE_ALL_KITS, \
        f'Expected All Kits dump (0x{DUMP_TYPE_ALL_KITS:02X}), got 0x{header.dump_type:02X}'
    internal = _decode_nibbles(raw[9:-1])
    return KitBank(header, internal)


def save_kit_bank(bank: KitBank, path: str):
    with open(path, 'wb') as f:
        f.write(bank.to_sysex())


# ---------------------------------------------------------------------------
# Quick self-test / demo
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else 'Kit 1.syx'
    kit = load_kit(path)
    print(f'Loaded: {path}')
    print(kit)
    print()

    # Round-trip check
    original_raw = open(path, 'rb').read()
    roundtrip_raw = kit.to_sysex()
    if original_raw == roundtrip_raw:
        print('Round-trip check: PASS (byte-for-byte identical)')
    else:
        diffs = [(i, a, b) for i, (a, b) in enumerate(zip(original_raw, roundtrip_raw)) if a != b]
        print(f'Round-trip check: FAIL ({len(diffs)} byte(s) differ)')
        for i, a, b in diffs[:10]:
            print(f'  byte {i}: {a:02X} -> {b:02X}')
