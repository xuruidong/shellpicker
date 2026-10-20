"""Pure-Python port of the Shanbay word-list decode routine.

The original implementation lived in src/assets/shanbay.js (adapted from
yihong0618/shanbei_remember) and was evaluated through PyExecJS, which
requires a JavaScript engine (Node, etc.). Without one, execjs silently
falls back to Windows' JScript runtime, which cannot parse ES6 syntax and
produces GBK-encoded output that breaks subprocess reads. Reimplementing
the decoder in Python removes that dependency entirely.
"""

import base64
import json

B32_CODE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
B64_CODE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
CNT = [1, 2, 2, 2, 2, 2]

MIN_LOOP = 8
PRE_LOOP = 8
BAY_SH0 = 1
BAY_SH1 = 10
BAY_SH8 = 8
BAY_MASK = 0x7FFFFFFF
VERSION = 1


def _u32(x):
    return x & 0xFFFFFFFF


def _to_uint32(x):
    # Mirrors JavaScript's `>>> 0` on a non-negative float64.
    return int(x) % 0x100000000


def _mul(a, b):
    # Faithful port of the JS `Num.mul`, which splits the product into
    # high/low 16-bit halves so the result survives IEEE-754 double rounding
    # exactly as the original does.
    a = _u32(a)
    b = _u32(b)
    high16 = float(a & 0xFFFF0000) * float(b)
    low16 = float(a & 0x0000FFFF) * float(b)
    return _u32(_to_uint32(high16) + _to_uint32(low16))


class Random:
    def __init__(self):
        self.status = [0, 0, 0, 0]
        self.mat1 = 0
        self.mat2 = 0
        self.tmat = 0

    def seed(self, seeds):
        for idx in range(4):
            self.status[idx] = ord(seeds[idx]) if idx < len(seeds) else 110
        self.mat1, self.mat2, self.tmat = self.status[1], self.status[2], self.status[3]
        self.init()

    def init(self):
        for idx in range(MIN_LOOP - 1):
            n = self.status[idx & 3]
            mixed = _u32(n ^ (n >> 30))
            self.status[(idx + 1) & 3] = _u32(
                self.status[(idx + 1) & 3] ^ _u32(idx + 1 + _mul(1812433253, mixed))
            )

        if (self.status[0] & BAY_MASK) == 0 and self.status[1] == 0 \
                and self.status[2] == 0 and self.status[3] == 0:
            self.status = [66, 65, 89, 83]

        for _ in range(PRE_LOOP):
            self.next_state()

    def next_state(self):
        y = self.status[3]
        x = _u32((self.status[0] & BAY_MASK) ^ self.status[1] ^ self.status[2])
        x = _u32(x ^ (x << BAY_SH0))
        y = _u32(y ^ ((y >> BAY_SH0) ^ x))

        s1, s2 = self.status[1], self.status[2]
        self.status[0] = s1
        self.status[1] = s2
        self.status[2] = _u32(x ^ (y << BAY_SH1))
        self.status[3] = y

        if y & 1:
            self.status[1] = _u32(self.status[1] ^ self.mat1)
            self.status[2] = _u32(self.status[2] ^ self.mat2)

    def generate(self, max_val):
        self.next_state()

        t0 = self.status[3]
        t1 = _u32(self.status[0] ^ (self.status[2] >> BAY_SH8))
        t0 = _u32(t0 ^ t1)
        if t1 & 1:
            t0 = _u32(t0 ^ self.tmat)

        return t0 % max_val


class Node:
    def __init__(self):
        self.char = "."
        self.children = {}


class Tree:
    def __init__(self):
        self.random = Random()
        self.sign = ""
        self.inter = {}
        self.head = Node()

    def init(self, sign):
        self.random.seed(sign)
        self.sign = sign

        for i in range(64):
            self.add_symbol(B64_CODE[i], CNT[(i + 1) // 11])
        self.inter["="] = "="

    def add_symbol(self, char, length):
        ptr = self.head
        symbol = ""

        for _ in range(length):
            inner = B32_CODE[self.random.generate(32)]
            while inner in ptr.children and ptr.children[inner].char != ".":
                inner = B32_CODE[self.random.generate(32)]

            symbol += inner
            if inner not in ptr.children:
                ptr.children[inner] = Node()
            ptr = ptr.children[inner]

        ptr.char = char
        self.inter[char] = symbol
        return symbol

    def decode(self, enc):
        dec = ""
        i = 4
        n = len(enc)
        while i < n:
            if enc[i] == "=":
                dec += "="
                i += 1
                continue

            ptr = self.head
            while i < n and enc[i] in ptr.children:
                ptr = ptr.children[enc[i]]
                i += 1
            dec += ptr.char

        return dec


def _get_idx(c):
    x = ord(c)
    if x >= 65:
        return x - 65
    return x - 65 + 41


def _check_version(s):
    wi = _get_idx(s[0]) * 32 + _get_idx(s[1])
    x = _get_idx(s[2])
    check = _get_idx(s[3])
    return VERSION >= (wi * x + check) % 32


def decode(enc):
    if not _check_version(enc):
        return ""

    tree = Tree()
    tree.init(enc[:4])
    raw = tree.decode(enc)

    padded = raw + "=" * (-len(raw) % 4)
    buff = base64.b64decode(padded)
    return json.loads(buff.decode("utf-8"))


def decode_json(enc):
    """Decode a Shanbay-encoded payload into a Python object."""
    return decode(enc)
