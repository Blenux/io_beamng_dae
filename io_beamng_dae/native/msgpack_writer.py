"""Pure-Python msgpack writer (big-endian, matches C++ MsgPackWriter).

Supports: nil, bool, int8/16/32/64, uint8/16/32/64, float32/64, str, bin, array, map.
"""

import struct


class MsgPackWriter:
    """Writes msgpack-encoded data to an internal byte buffer (big-endian)."""

    def __init__(self):
        self._buf = bytearray()

    def _write_u8(self, v):
        self._buf.append(v & 0xFF)

    def _write_u16_be(self, v):
        self._buf.extend(struct.pack(">H", v))

    def _write_u32_be(self, v):
        self._buf.extend(struct.pack(">I", v))

    def _write_u64_be(self, v):
        self._buf.extend(struct.pack(">Q", v))

    def _write_i64_be(self, v):
        self._buf.extend(struct.pack(">q", v))

    def _write_f32_be(self, v):
        self._buf.extend(struct.pack(">f", v))

    def _write_f64_be(self, v):
        self._buf.extend(struct.pack(">d", v))

    def write_nil(self):
        self._write_u8(0xC0)

    def write_bool(self, v):
        self._write_u8(0xC3 if v else 0xC2)

    def write_int32(self, v):
        if 0 <= v <= 127:
            self._write_u8(v)
        elif v < 0 and v >= -32:
            self._write_u8(v & 0xFF)
        elif -128 <= v <= 127:
            self._write_u8(0xD0)
            self._write_u8(v & 0xFF)
        elif -32768 <= v <= 32767:
            self._write_u8(0xD1)
            self._write_u16_be(v & 0xFFFF)
        else:
            self._write_u8(0xD2)
            self._write_u32_be(v & 0xFFFFFFFF)

    def write_int64(self, v):
        if -2147483648 <= v <= 2147483647:
            self.write_int32(v)
        else:
            self._write_u8(0xD3)
            self._write_i64_be(v)

    def write_uint32(self, v):
        if v <= 127:
            self._write_u8(v)
        elif v <= 255:
            self._write_u8(0xCC)
            self._write_u8(v)
        elif v <= 65535:
            self._write_u8(0xCD)
            self._write_u16_be(v)
        else:
            self._write_u8(0xCE)
            self._write_u32_be(v)

    def write_float(self, v):
        self._write_u8(0xCA)
        self._write_f32_be(v)

    def write_double(self, v):
        self._write_u8(0xCB)
        self._write_f64_be(v)

    def write_string(self, s):
        encoded = s.encode("utf-8")
        n = len(encoded)
        if n <= 31:
            self._write_u8(0xA0 | n)
        elif n <= 255:
            self._write_u8(0xD9)
            self._write_u8(n)
        elif n <= 65535:
            self._write_u8(0xDA)
            self._write_u16_be(n)
        else:
            self._write_u8(0xDB)
            self._write_u32_be(n)
        self._buf.extend(encoded)

    def write_binary(self, data):
        n = len(data)
        if n <= 255:
            self._write_u8(0xC4)
            self._write_u8(n)
        elif n <= 65535:
            self._write_u8(0xC5)
            self._write_u16_be(n)
        else:
            self._write_u8(0xC6)
            self._write_u32_be(n)
        self._buf.extend(data)

    def write_array_size(self, n):
        if n <= 15:
            self._write_u8(0x90 | n)
        elif n <= 65535:
            self._write_u8(0xDC)
            self._write_u16_be(n)
        else:
            self._write_u8(0xDD)
            self._write_u32_be(n)

    def write_map_size(self, n):
        if n <= 15:
            self._write_u8(0x80 | n)
        elif n <= 65535:
            self._write_u8(0xDE)
            self._write_u16_be(n)
        else:
            self._write_u8(0xDF)
            self._write_u32_be(n)

    def write_float_array(self, values):
        """Write an array of float32 values."""
        self.write_array_size(len(values))
        for v in values:
            self.write_float(v)

    def take_bytes(self):
        """Return the accumulated bytes and clear the buffer."""
        result = bytes(self._buf)
        self._buf.clear()
        return result

    def get_bytes(self):
        """Return the accumulated bytes without clearing."""
        return bytes(self._buf)

    def size(self):
        return len(self._buf)

    def clear(self):
        self._buf.clear()
