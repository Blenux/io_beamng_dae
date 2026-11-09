"""Pure-Python msgpack reader (big-endian, matches C++ MsgPackReader).

Supports: nil, bool, int8/16/32/64, uint8/16/32/64, float32/64, str, bin, array, map.
"""

import struct


class MsgPackValue:
    """Represents a parsed msgpack value with a type tag and data."""

    __slots__ = ("type", "b", "i", "u", "f", "str", "bin", "arr", "map_keys", "map_vals")

    NIL = 0
    BOOL = 1
    INT = 2
    UINT = 3
    FLOAT = 4
    STRING = 5
    BINARY = 6
    ARRAY = 7
    MAP = 8

    def __init__(self, type=NIL, b=False, i=0, u=0, f=0.0, str="", bin=None,
                 arr=None, map_keys=None, map_vals=None):
        self.type = type
        self.b = b
        self.i = i
        self.u = u
        self.f = f
        self.str = str
        self.bin = bin if bin is not None else b""
        self.arr = arr if arr is not None else []
        self.map_keys = map_keys if map_keys is not None else []
        self.map_vals = map_vals if map_vals is not None else []

    @staticmethod
    def make_bool(val):
        return MsgPackValue(type=MsgPackValue.BOOL, b=val)

    @staticmethod
    def make_int(val):
        return MsgPackValue(type=MsgPackValue.INT, i=val)

    @staticmethod
    def make_uint(val):
        return MsgPackValue(type=MsgPackValue.UINT, u=val)

    @staticmethod
    def make_float(val):
        return MsgPackValue(type=MsgPackValue.FLOAT, f=val)

    @staticmethod
    def make_string(s):
        return MsgPackValue(type=MsgPackValue.STRING, str=s)

    @staticmethod
    def make_binary(data):
        return MsgPackValue(type=MsgPackValue.BINARY, bin=bytes(data))

    @staticmethod
    def make_array(arr):
        return MsgPackValue(type=MsgPackValue.ARRAY, arr=arr)

    @staticmethod
    def make_map(keys, vals):
        return MsgPackValue(type=MsgPackValue.MAP, map_keys=keys, map_vals=vals)


class MsgPackReader:
    """Reads msgpack-encoded data from a bytes buffer (big-endian)."""

    def __init__(self, data=b""):
        self.data = data
        self.pos = 0
        self.size = len(data)
        self.error = False

    def _check(self, n):
        return (self.pos + n) <= self.size

    def _read_u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def _read_u16(self):
        v = struct.unpack_from(">H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def _read_u32(self):
        v = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def _read_u64(self):
        v = struct.unpack_from(">Q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def _read_i16(self):
        v = struct.unpack_from(">h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def _read_i32(self):
        v = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def _read_i64(self):
        v = struct.unpack_from(">q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def _read_f32(self):
        v = struct.unpack_from(">f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def _read_f64(self):
        v = struct.unpack_from(">d", self.data, self.pos)[0]
        self.pos += 8
        return v

    def parse_value(self):
        """Parse and return the next msgpack value."""
        if not self._check(1):
            self.error = True
            return MsgPackValue()

        tag = self._read_u8()

        # Fixmap (0x80-0x8F)
        if (tag & 0xF0) == 0x80:
            return self._parse_map(tag & 0x0F)

        # Fixarray (0x90-0x9F)
        if (tag & 0xF0) == 0x90:
            return self._parse_array(tag & 0x0F)

        # Fixstr (0xA0-0xBF)
        if (tag & 0xE0) == 0xA0:
            n = tag & 0x1F
            if not self._check(n):
                self.error = True
                return MsgPackValue()
            s = self.data[self.pos:self.pos + n].decode("utf-8", errors="replace")
            self.pos += n
            return MsgPackValue.make_string(s)

        # Positive fixint (0x00-0x7F)
        if tag <= 0x7F:
            return MsgPackValue.make_uint(tag)

        # Negative fixint (0xE0-0xFF)
        if tag >= 0xE0:
            return MsgPackValue.make_int(tag - 256 if tag >= 0x80 else tag)

        if tag == 0xC0:
            return MsgPackValue()  # nil

        if tag == 0xC2:
            return MsgPackValue.make_bool(False)

        if tag == 0xC3:
            return MsgPackValue.make_bool(True)

        # Unsigned ints
        if tag == 0xCC:
            if not self._check(1):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_uint(self._read_u8())

        if tag == 0xCD:
            if not self._check(2):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_uint(self._read_u16())

        if tag == 0xCE:
            if not self._check(4):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_uint(self._read_u32())

        if tag == 0xCF:
            if not self._check(8):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_uint(self._read_u64())

        # Signed ints
        if tag == 0xD0:
            if not self._check(1):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_int(self._read_u8() - 256 if self.data[self.pos] >= 0x80 else self._read_u8())

        if tag == 0xD1:
            if not self._check(2):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_int(self._read_i16())

        if tag == 0xD2:
            if not self._check(4):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_int(self._read_i32())

        if tag == 0xD3:
            if not self._check(8):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_int(self._read_i64())

        # Floats
        if tag == 0xCA:
            if not self._check(4):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_float(self._read_f32())

        if tag == 0xCB:
            if not self._check(8):
                self.error = True
                return MsgPackValue()
            return MsgPackValue.make_float(self._read_f64())

        # Str
        if tag == 0xD9:
            if not self._check(1):
                self.error = True
                return MsgPackValue()
            n = self._read_u8()
            if not self._check(n):
                self.error = True
                return MsgPackValue()
            s = self.data[self.pos:self.pos + n].decode("utf-8", errors="replace")
            self.pos += n
            return MsgPackValue.make_string(s)

        if tag == 0xDA:
            if not self._check(2):
                self.error = True
                return MsgPackValue()
            n = self._read_u16()
            if not self._check(n):
                self.error = True
                return MsgPackValue()
            s = self.data[self.pos:self.pos + n].decode("utf-8", errors="replace")
            self.pos += n
            return MsgPackValue.make_string(s)

        if tag == 0xDB:
            if not self._check(4):
                self.error = True
                return MsgPackValue()
            n = self._read_u32()
            if not self._check(n):
                self.error = True
                return MsgPackValue()
            s = self.data[self.pos:self.pos + n].decode("utf-8", errors="replace")
            self.pos += n
            return MsgPackValue.make_string(s)

        # Bin
        if tag == 0xC4:
            if not self._check(1):
                self.error = True
                return MsgPackValue()
            n = self._read_u8()
            if not self._check(n):
                self.error = True
                return MsgPackValue()
            v = self.data[self.pos:self.pos + n]
            self.pos += n
            return MsgPackValue.make_binary(v)

        if tag == 0xC5:
            if not self._check(2):
                self.error = True
                return MsgPackValue()
            n = self._read_u16()
            if not self._check(n):
                self.error = True
                return MsgPackValue()
            v = self.data[self.pos:self.pos + n]
            self.pos += n
            return MsgPackValue.make_binary(v)

        if tag == 0xC6:
            if not self._check(4):
                self.error = True
                return MsgPackValue()
            n = self._read_u32()
            if not self._check(n):
                self.error = True
                return MsgPackValue()
            v = self.data[self.pos:self.pos + n]
            self.pos += n
            return MsgPackValue.make_binary(v)

        # Array 16/32
        if tag == 0xDC:
            if not self._check(2):
                self.error = True
                return MsgPackValue()
            return self._parse_array(self._read_u16())

        if tag == 0xDD:
            if not self._check(4):
                self.error = True
                return MsgPackValue()
            return self._parse_array(self._read_u32())

        # Map 16/32
        if tag == 0xDE:
            if not self._check(2):
                self.error = True
                return MsgPackValue()
            return self._parse_map(self._read_u16())

        if tag == 0xDF:
            if not self._check(4):
                self.error = True
                return MsgPackValue()
            return self._parse_map(self._read_u32())

        # Unknown type
        self.error = True
        return MsgPackValue()

    def _parse_array(self, n):
        if n == 0:
            return MsgPackValue.make_array([])
        elems = []
        for _ in range(n):
            elems.append(self.parse_value())
            if self.error:
                return MsgPackValue()
        return MsgPackValue.make_array(elems)

    def _parse_map(self, n):
        if n == 0:
            return MsgPackValue.make_map([], [])
        keys = []
        vals = []
        for _ in range(n):
            keys.append(self.parse_value())
            if self.error:
                return MsgPackValue()
            vals.append(self.parse_value())
            if self.error:
                return MsgPackValue()
        return MsgPackValue.make_map(keys, vals)

    def read_bytes(self, n):
        """Read n raw bytes from the stream."""
        if not self._check(n):
            self.error = True
            return b""
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result

    def position(self):
        return self.pos

    def remaining(self):
        return self.size - self.pos

    def done(self):
        return self.pos >= self.size

    @staticmethod
    def map_get(map_val, key):
        """Look up a string key in a MsgPackValue map. Returns the value or None."""
        if map_val.type != MsgPackValue.MAP:
            return None
        for i, k in enumerate(map_val.map_keys):
            if k.type == MsgPackValue.STRING and k.str == key:
                return map_val.map_vals[i]
        return None
