"""SJSON parser and writer for BeamNG .jbeam/.pc files.

Dependency-free, single-file implementation.
SJSON = JSON superset with optional commas, comments, := or = separators, optional outer braces.
"""

from __future__ import annotations
from typing import Any, Union
import math

__all__ = ["parse", "write", "ParseError"]


class ParseError(Exception):
    def __init__(self, message: str, line: int = 0):
        super().__init__(f"{message} at line {line}")
        self.message = message
        self.line = line


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.n = len(text)
        self.i = 0
        self.line = 1

    def skip_ws(self):
        while self.i < self.n:
            c = self.s[self.i]
            if c == '\n':
                self.line += 1
                self.i += 1
                continue
            if c <= ' ' or c == ',':
                self.i += 1
                continue
            if c == '/' and self.i + 1 < self.n and self.s[self.i + 1] == '/':
                self.i += 2
                while self.i < self.n and self.s[self.i] != '\n':
                    self.i += 1
                continue
            if c == '/' and self.i + 1 < self.n and self.s[self.i + 1] == '*':
                self.i += 2
                while self.i + 1 < self.n and not (self.s[self.i] == '*' and self.s[self.i + 1] == '/'):
                    if self.s[self.i] == '\n':
                        self.line += 1
                    self.i += 1
                if self.i + 1 < self.n:
                    self.i += 2
                continue
            break

    def read_string(self) -> str:
        self.i += 1  # skip opening quote
        result = []
        escapes = {'n': '\n', 't': '\t', 'r': '\r', 'f': '\f', 'b': '\b', '"': '"', '\\': '\\'}
        while self.i < self.n and self.s[self.i] != '"':
            if self.s[self.i] == '\\' and self.i + 1 < self.n:
                self.i += 1
                result.append(escapes.get(self.s[self.i], self.s[self.i]))
            else:
                if self.s[self.i] == '\n':
                    self.line += 1
                result.append(self.s[self.i])
            self.i += 1
        if self.i < self.n:
            self.i += 1  # skip closing quote
        return ''.join(result)

    def read_number(self) -> float:
        start = self.i
        s = self.s
        if self.i < self.n and s[self.i] in '+-':
            self.i += 1
        while self.i < self.n and (s[self.i].isdigit() or s[self.i] == '.'):
            self.i += 1
        if self.i < self.n and s[self.i] in 'eE':
            self.i += 1
            if self.i < self.n and s[self.i] in '+-':
                self.i += 1
            while self.i < self.n and s[self.i].isdigit():
                self.i += 1
        token = s[start:self.i]
        try:
            if '.' in token or 'e' in token or 'E' in token:
                return float(token)
            return int(token)
        except ValueError:
            return float(token)

    def parse_value(self) -> Any:
        self.skip_ws()
        if self.i >= self.n:
            return None
        c = self.s[self.i]
        if c == '"':
            return self.read_string()
        if c == '-' or c == '+' or c.isdigit() or c == '.':
            return self.read_number()
        if c == 't' and self.s[self.i:self.i + 4] == 'true':
            self.i += 4
            return True
        if c == 'f' and self.s[self.i:self.i + 5] == 'false':
            self.i += 5
            return False
        if c == 'n' and self.s[self.i:self.i + 4] == 'null':
            self.i += 4
            return None
        if c == '[':
            return self.parse_array()
        if c == '{':
            return self.parse_object(implicit=False)
        self.i += 1
        return None

    def parse_array(self) -> list:
        self.i += 1  # skip '['
        self.skip_ws()
        arr = []
        while self.i < self.n and self.s[self.i] != ']':
            arr.append(self.parse_value())
            self.skip_ws()
        if self.i < self.n:
            self.i += 1  # skip ']'
        return arr

    def parse_object(self, implicit=False) -> dict:
        if not implicit:
            self.i += 1  # skip '{'
        self.skip_ws()
        obj = {}
        while True:
            if implicit:
                if self.i >= self.n:
                    break
            else:
                if self.i >= self.n or self.s[self.i] == '}':
                    break
            if self.s[self.i] != '"':
                self.i += 1
                self.skip_ws()
                continue
            key = self.read_string()
            self.skip_ws()
            if self.i < self.n and (self.s[self.i] == ':' or self.s[self.i] == '='):
                self.i += 1
            val = self.parse_value()
            obj[key] = val
            self.skip_ws()
        if not implicit and self.i < self.n and self.s[self.i] == '}':
            self.i += 1
        return obj

    def parse(self) -> Any:
        self.skip_ws()
        if self.i < self.n and self.s[self.i] in '{[':
            return self.parse_value()
        return self.parse_object(implicit=True)


def parse(text: str) -> Any:
    """Parse SJSON text into Python objects (dict/list/str/int/float/bool/None)."""
    if not text:
        raise ParseError("Empty input")
    p = _Parser(text)
    return p.parse()


def _fmt_float(v: float) -> str:
    if v == int(v) and not math.isinf(v) and not math.isnan(v):
        return str(int(v))
    return repr(v)


def _escape_str(s: str) -> str:
    result = []
    for c in s:
        if c == '\\':
            result.append('\\\\')
        elif c == '"':
            result.append('\\"')
        elif c == '\n':
            result.append('\\n')
        elif c == '\t':
            result.append('\\t')
        elif c == '\r':
            result.append('\\r')
        else:
            result.append(c)
    return ''.join(result)


def write(data: Any, indent: int = 0) -> str:
    """Serialize Python objects to SJSON text."""
    pad = '  ' * indent
    if data is None:
        return "null"
    if isinstance(data, bool):
        return "true" if data else "false"
    if isinstance(data, (int, float)):
        return _fmt_float(float(data))
    if isinstance(data, str):
        return f'"{_escape_str(data)}"'
    if isinstance(data, list):
        if not data:
            return "[]"
        parts = []
        for item in data:
            parts.append(f"{'  ' * (indent + 1)}{write(item, indent + 1)}")
        return "[\n" + ",\n".join(parts) + f"\n{pad}]"
    if isinstance(data, dict):
        if not data:
            return "{}"
        parts = []
        for k, v in data.items():
            parts.append(f"{'  ' * (indent + 1)}\"{_escape_str(k)}\": {write(v, indent + 1)}")
        return "{\n" + ",\n".join(parts) + f"\n{pad}}}"
    return "null"
