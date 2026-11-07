"""JScript number formatting: ToString, toFixed, toExponential, toPrecision, toString(radix)."""

from __future__ import annotations

import math
import struct

_MASK = 0xFFFFFFFF
_MAX_RGB = 50


def _words(x: float) -> tuple[int, int]:
    bits = struct.unpack("<Q", struct.pack("<d", x))[0]
    return bits >> 32, bits & _MASK


def _from_words(hi: int, lo: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", (hi << 32) | lo))[0]


def _clz32(x: int) -> int:
    return 32 - x.bit_length()


class _BigNum:
    __slots__ = ("err", "exp", "lu0", "lu1", "lu2")

    lu0: int
    lu1: int
    lu2: int
    exp: int
    err: int

    def __init__(self, lu0: int, lu1: int, lu2: int, exp: int, err: int) -> None:
        self.lu0 = lu0
        self.lu1 = lu1
        self.lu2 = lu2
        self.exp = exp
        self.err = err

    def copy(self) -> _BigNum:
        return _BigNum(self.lu0, self.lu1, self.lu2, self.exp, self.err)

    def zero(self) -> bool:
        return self.lu2 == 0 and self.lu1 == 0 and self.lu0 == 0

    def normalize(self) -> None:
        if self.lu2 == 0:
            if self.lu1 == 0:
                if self.lu0 == 0:
                    self.exp = 0
                    return
                self.lu2 = self.lu0
                self.lu0 = 0
                self.exp -= 64
            else:
                self.lu2 = self.lu1
                self.lu1 = self.lu0
                self.lu0 = 0
                self.exp -= 32
        w1 = _clz32(self.lu2)
        if w1:
            w2 = 32 - w1
            self.lu2 = ((self.lu2 << w1) | (self.lu1 >> w2)) & _MASK
            self.lu1 = ((self.lu1 << w1) | (self.lu0 >> w2)) & _MASK
            self.lu0 = (self.lu0 << w1) & _MASK
            self.exp -= w1

    def mul(self, op: _BigNum) -> None:
        r = [0] * 6

        def add(i: int, v: int) -> int:
            s = r[i] + (v & _MASK)
            r[i] = s & _MASK
            return 1 if s > _MASK else 0

        t = self.lu0
        if t:
            p = t * op.lu0
            r[0], r[1] = p & _MASK, p >> 32
            p = t * op.lu1
            c = add(1, p & _MASK)
            add(2, (p >> 32) + c)
            p = t * op.lu2
            c = add(2, p & _MASK)
            add(3, (p >> 32) + c)
        t = self.lu1
        if t:
            p = t * op.lu0
            c = add(1, p & _MASK)
            c = add(2, (p >> 32) + c)
            if c and add(3, 1):
                add(4, 1)
            p = t * op.lu1
            c = add(2, p & _MASK)
            c = add(3, (p >> 32) + c)
            if c:
                add(4, 1)
            p = t * op.lu2
            c = add(3, p & _MASK)
            add(4, (p >> 32) + c)
        t = self.lu2
        p = t * op.lu0
        c = add(2, p & _MASK)
        c = add(3, (p >> 32) + c)
        if c and add(4, 1):
            add(5, 1)
        p = t * op.lu1
        c = add(3, p & _MASK)
        c = add(4, (p >> 32) + c)
        if c:
            add(5, 1)
        p = t * op.lu2
        c = add(4, p & _MASK)
        add(5, (p >> 32) + c)

        self.exp += op.exp
        self.err += op.err

        normalized = False
        if not (r[5] & 0x80000000):
            if (
                (r[2] & 0x40000000)
                and ((r[2] & 0xBFFFFFFF) or r[1] or r[0])
                and add(2, 0x40000000)
                and add(3, 1)
                and add(4, 1)
            ):
                add(5, 1)
                if r[5] & 0x80000000:
                    normalized = True
            if not normalized:
                self.lu2 = ((r[5] << 1) | (r[4] >> 31)) & _MASK
                self.lu1 = ((r[4] << 1) | (r[3] >> 31)) & _MASK
                self.lu0 = ((r[3] << 1) | (r[2] >> 31)) & _MASK
                self.exp -= 1
                self.err = (self.err << 1) & _MASK
                if (r[2] & 0x7FFFFFFF) or r[1] or r[0]:
                    self.err += 1
                return
        elif (
            (r[2] & 0x80000000)
            and ((r[3] & 1) or (r[2] & 0x7FFFFFFF) or r[1] or r[0])
            and add(3, 1)
            and add(4, 1)
            and add(5, 1)
        ):
            r[5] = 0x80000000
            self.exp += 1
        self.lu2, self.lu1, self.lu0 = r[5], r[4], r[3]
        if r[2] or r[1] or r[0]:
            self.err += 1

    def lu_mod1(self) -> int:
        if self.exp <= 0:
            return 0
        t = self.lu2 >> (32 - self.exp)
        self.lu2 &= 0x7FFFFFFF >> (self.exp - 1)
        self.normalize()
        return t

    def upper_bound(self) -> None:
        t = (self.err + 1) >> 1
        if t:
            s = self.lu0 + t
            self.lu0 = s & _MASK
            if s > _MASK:
                s = self.lu1 + 1
                self.lu1 = s & _MASK
                if s > _MASK:
                    s = self.lu2 + 1
                    self.lu2 = s & _MASK
                    if s > _MASK:
                        self.lu2 = 0x80000000
                        self.lu0 = (self.lu0 >> 1) + (self.lu0 & 1)
                        self.exp += 1
        self.err = 0

    def lower_bound(self) -> None:
        t = (self.err + 1) >> 1
        if t:
            s = self.lu0 + ((-t) & _MASK)
            self.lu0 = s & _MASK
            if s <= _MASK:
                s = self.lu1 + _MASK
                self.lu1 = s & _MASK
                if s <= _MASK:
                    self.lu2 = (self.lu2 + _MASK) & _MASK
                    if not (self.lu2 & 0x80000000):
                        self.normalize()
        self.err = 0


def _bn(lu0: int, lu1: int, lu2: int, exp: int, err: int) -> _BigNum:
    return _BigNum(lu0, lu1, lu2, exp, err)


_POS = [
    _bn(0x00000000, 0x00000000, 0xA0000000, 4, 0),
    _bn(0x00000000, 0x00000000, 0xC8000000, 7, 0),
    _bn(0x00000000, 0x00000000, 0xFA000000, 10, 0),
    _bn(0x00000000, 0x00000000, 0x9C400000, 14, 0),
    _bn(0x00000000, 0x00000000, 0xC3500000, 17, 0),
    _bn(0x00000000, 0x00000000, 0xF4240000, 20, 0),
    _bn(0x00000000, 0x00000000, 0x98968000, 24, 0),
    _bn(0x00000000, 0x00000000, 0xBEBC2000, 27, 0),
    _bn(0x00000000, 0x00000000, 0xEE6B2800, 30, 0),
    _bn(0x00000000, 0x00000000, 0x9502F900, 34, 0),
    _bn(0x00000000, 0x00000000, 0xBA43B740, 37, 0),
    _bn(0x00000000, 0x00000000, 0xE8D4A510, 40, 0),
    _bn(0x00000000, 0x00000000, 0x9184E72A, 44, 0),
    _bn(0x00000000, 0x80000000, 0xB5E620F4, 47, 0),
    _bn(0x00000000, 0xA0000000, 0xE35FA931, 50, 0),
    _bn(0x00000000, 0x04000000, 0x8E1BC9BF, 54, 0),
    _bn(0x00000000, 0xC5000000, 0xB1A2BC2E, 57, 0),
    _bn(0x00000000, 0x76400000, 0xDE0B6B3A, 60, 0),
    _bn(0x00000000, 0x89E80000, 0x8AC72304, 64, 0),
    _bn(0x00000000, 0xAC620000, 0xAD78EBC5, 67, 0),
    _bn(0x00000000, 0x177A8000, 0xD8D726B7, 70, 0),
    _bn(0x00000000, 0x6EAC9000, 0x87867832, 74, 0),
    _bn(0x00000000, 0x0A57B400, 0xA968163F, 77, 0),
    _bn(0x00000000, 0xCCEDA100, 0xD3C21BCE, 80, 0),
    _bn(0x00000000, 0x401484A0, 0x84595161, 84, 0),
    _bn(0x00000000, 0x9019A5C8, 0xA56FA5B9, 87, 0),
    _bn(0x00000000, 0xF4200F3A, 0xCECB8F27, 90, 0),
    _bn(0x40000000, 0xF8940984, 0x813F3978, 94, 0),
    _bn(0x50000000, 0x36B90BE5, 0xA18F07D7, 97, 0),
    _bn(0xA4000000, 0x04674EDE, 0xC9F2C9CD, 100, 0),
    _bn(0x4D000000, 0x45812296, 0xFC6F7C40, 103, 0),
    _bn(0xF0200000, 0x2B70B59D, 0x9DC5ADA8, 107, 0),
    _bn(0x3CBF6B72, 0xFFCFA6D5, 0xC2781F49, 213, 1),
    _bn(0xC5CFE94F, 0xC59B14A2, 0xEFB3AB16, 319, 1),
    _bn(0xC66F336C, 0x80E98CDF, 0x93BA47C9, 426, 1),
    _bn(0x577B986B, 0x7FE617AA, 0xB616A12B, 532, 1),
    _bn(0x85BBE254, 0x3927556A, 0xE070F78D, 638, 1),
    _bn(0x82BD6B71, 0xE33CC92F, 0x8A5296FF, 745, 1),
    _bn(0xDDBB901C, 0x9DF9DE8D, 0xAA7EEBFB, 851, 1),
    _bn(0x73832EEC, 0x5C6A2F8C, 0xD226FC19, 957, 1),
    _bn(0xE6A11583, 0xF2CCE375, 0x81842F29, 1064, 1),
    _bn(0x5EBF18B7, 0xDB900AD2, 0x9FA42700, 1170, 1),
    _bn(0x1027FFF5, 0xAEF8AA17, 0xC4C5E310, 1276, 1),
    _bn(0xB5E54F71, 0xE9B09C58, 0xF28A9C07, 1382, 1),
    _bn(0xA7EA9C88, 0xEBF7F3D3, 0x957A4AE1, 1489, 1),
    _bn(0x7DF40A74, 0x0795A262, 0xB83ED8DC, 1595, 1),
]

_NEG = [
    _bn(0xCCCCCCCD, 0xCCCCCCCC, 0xCCCCCCCC, -3, 1),
    _bn(0x3D70A3D7, 0x70A3D70A, 0xA3D70A3D, -6, 1),
    _bn(0x645A1CAC, 0x8D4FDF3B, 0x83126E97, -9, 1),
    _bn(0xD3C36113, 0xE219652B, 0xD1B71758, -13, 1),
    _bn(0x0FCF80DC, 0x1B478423, 0xA7C5AC47, -16, 1),
    _bn(0xA63F9A4A, 0xAF6C69B5, 0x8637BD05, -19, 1),
    _bn(0x3D329076, 0xE57A42BC, 0xD6BF94D5, -23, 1),
    _bn(0xFDC20D2B, 0x8461CEFC, 0xABCC7711, -26, 1),
    _bn(0x31680A89, 0x36B4A597, 0x89705F41, -29, 1),
    _bn(0xB573440E, 0xBDEDD5BE, 0xDBE6FECE, -33, 1),
    _bn(0xF78F69A5, 0xCB24AAFE, 0xAFEBFF0B, -36, 1),
    _bn(0xF93F87B7, 0x6F5088CB, 0x8CBCCC09, -39, 1),
    _bn(0x2865A5F2, 0x4BB40E13, 0xE12E1342, -43, 1),
    _bn(0x538484C2, 0x095CD80F, 0xB424DC35, -46, 1),
    _bn(0x0F9D3701, 0x3AB0ACD9, 0x901D7CF7, -49, 1),
    _bn(0x4C2EBE68, 0xC44DE15B, 0xE69594BE, -53, 1),
    _bn(0x09BEFEBA, 0x36A4B449, 0xB877AA32, -56, 1),
    _bn(0x3AFF322E, 0x921D5D07, 0x9392EE8E, -59, 1),
    _bn(0x2B31E9E4, 0xB69561A5, 0xEC1E4A7D, -63, 1),
    _bn(0x88F4BB1D, 0x92111AEA, 0xBCE50864, -66, 1),
    _bn(0xD3F6FC17, 0x74DA7BEE, 0x971DA050, -69, 1),
    _bn(0x5324C68B, 0xBAF72CB1, 0xF1C90080, -73, 1),
    _bn(0x75B7053C, 0x95928A27, 0xC16D9A00, -76, 1),
    _bn(0xC4926A96, 0x44753B52, 0x9ABE14CD, -79, 1),
    _bn(0x3A83DDBE, 0xD3EEC551, 0xF79687AE, -83, 1),
    _bn(0x95364AFE, 0x76589DDA, 0xC6120625, -86, 1),
    _bn(0x775EA265, 0x91E07E48, 0x9E74D1B7, -89, 1),
    _bn(0x8BCA9D6E, 0x8300CA0D, 0xFD87B5F2, -93, 1),
    _bn(0x096EE458, 0x359A3B3E, 0xCAD2F7F5, -96, 1),
    _bn(0xA125837A, 0x5E14FC31, 0xA2425FF7, -99, 1),
    _bn(0x80EACF95, 0x4B43FCF4, 0x81CEB32C, -102, 1),
    _bn(0x67DE18EE, 0x453994BA, 0xCFB11EAD, -106, 1),
    _bn(0x3F2398D7, 0xA539E9A5, 0xA87FEA27, -212, 1),
    _bn(0x11DBCB02, 0xFD75539B, 0x88B402F7, -318, 1),
    _bn(0xAC7CB3F7, 0x64BCE4A0, 0xDDD0467C, -425, 1),
    _bn(0x59ED2167, 0xDB73A093, 0xB3F4E093, -531, 1),
    _bn(0x7B6306A3, 0x5423CC06, 0x91FF8377, -637, 1),
    _bn(0xA4F8BF56, 0x4A314EBD, 0xECE53CEC, -744, 1),
    _bn(0xFA911156, 0x637A1939, 0xC0314325, -850, 1),
    _bn(0x4EE367F9, 0x836AC577, 0x9BECCE62, -956, 1),
    _bn(0x8920B099, 0x478238D0, 0xFD00B897, -1063, 1),
    _bn(0x0092757C, 0x46F34F7D, 0xCD42A113, -1169, 1),
    _bn(0x88DBA000, 0xB11B0857, 0xA686E3E8, -1275, 1),
    _bn(0x1A4EB007, 0x3FFC68A6, 0x871A4981, -1381, 1),
    _bn(0x84C663CF, 0xB6074244, 0xDB377599, -1488, 1),
    _bn(0x61EB52E2, 0x79007736, 0xB1D983B4, -1594, 1),
]

_TENS = [10.0**i for i in range(29)]


def _rgb_fast(dbl: float, ndigits: int = -1) -> tuple[list[int], int] | None:
    hi, lo = _words(dbl)
    exp2 = (hi >> 20) & 0x7FF
    exp10 = 0
    if exp2 > 0:
        if 1023 <= exp2 <= 1075 and dbl == math.floor(dbl):
            return _small_int(dbl)
        base = _BigNum(
            0,
            (lo << 11) & _MASK,
            (0x80000000 | ((hi & 0x000FFFFFF) << 11) | (lo >> 21)) & _MASK,
            exp2 - 1022,
            0,
        )
        hh = base.copy()
        hh.lu1 |= 1 << 10
        ll = base.copy()
        t = 0xFFFFFE00 if ll.lu2 == 0x80000000 and ll.lu1 == 0 else 0xFFFFFC00
        s = ll.lu1 + t
        ll.lu1 = s & _MASK
        if s <= _MASK:
            ll.lu2 = (ll.lu2 + _MASK) & _MASK
            if not (ll.lu2 & 0x80000000):
                ll.normalize()
    else:
        base = _BigNum(0, lo, hi & 0x000FFFFF, -1010, 0)
        hh = base.copy()
        hh.lu0 = 0x80000000
        ll = hh.copy()
        s = ll.lu1 + _MASK
        ll.lu1 = s & _MASK
        if s <= _MASK:
            ll.lu2 = (ll.lu2 + _MASK) & _MASK
        base.normalize()
        hh.normalize()
        ll.normalize()

    if hh.exp >= 32:
        it = (hh.exp - 25) * 15 // -_NEG[45].exp
        if it > 0:
            p = _NEG[30 + it]
            hh.mul(p)
            ll.mul(p)
            exp10 += it * 32
        if hh.exp >= 32:
            it = (hh.exp - 25) * 32 // -_NEG[31].exp
            p = _NEG[it - 1]
            hh.mul(p)
            ll.mul(p)
            exp10 += it
    elif hh.exp < 1:
        it = (25 - hh.exp) * 15 // _POS[45].exp
        if it > 0:
            p = _POS[30 + it]
            hh.mul(p)
            ll.mul(p)
            exp10 -= it * 32
        if hh.exp < 1:
            it = (25 - hh.exp) * 32 // _POS[31].exp
            p = _POS[it - 1]
            hh.mul(p)
            ll.mul(p)
            exp10 -= it

    hl = hh.copy()
    hh.upper_bound()
    hl.lower_bound()
    lu_hh = hh.lu_mod1()
    lu_hl = hl.lu_mod1()
    lh = ll.copy()
    lh.upper_bound()
    ll.lower_bound()
    lu_lh = lh.lu_mod1()
    lu_ll = ll.lu_mod1()

    scale = 1
    if lu_hh >= 100000000:
        scale = 100000000
        exp10 += 8
    else:
        if lu_hh >= 10000:
            scale = 10000
            exp10 += 4
        if lu_hh >= 100 * scale:
            scale *= 100
            exp10 += 2
    if lu_hh >= 10 * scale:
        scale *= 10
        exp10 += 1
    exp10 += 1

    digits: list[int] = []
    b_hh = b_ll = 0
    while len(digits) < _MAX_RGB:
        b_hh, lu_hh = divmod(lu_hh, scale)
        b_ll, lu_ll = divmod(lu_ll, scale)
        if b_hh != b_ll:
            break
        digits.append(b_hh)
        if scale == 1:
            scale = 10000000
            hh.mul(_POS[7])
            hh.upper_bound()
            lu_hh = hh.lu_mod1()
            if lu_hh >= 100000000:
                return None
            hl.mul(_POS[7])
            hl.lower_bound()
            lu_hl = hl.lu_mod1()
            lh.mul(_POS[7])
            lh.upper_bound()
            lu_lh = lh.lu_mod1()
            ll.mul(_POS[7])
            ll.lower_bound()
            lu_ll = ll.lu_mod1()
        else:
            scale //= 10

    b_lh = (lu_lh // scale) % 10
    lu_lh %= scale
    b_hl = (lu_hl // scale) % 10
    lu_hl %= scale
    if b_lh >= b_hl:
        return None
    if b_lh == 0 and lu_lh == 0 and lh.zero() and not (lo & 1):
        pass
    elif b_hl - b_lh > 1:
        if len(digits) >= _MAX_RGB:
            return None
        digits.append((b_hl + b_lh + 1) // 2)
    elif lu_hl != 0 or not hl.zero() or not (lo & 1):
        if len(digits) >= _MAX_RGB:
            return None
        if ndigits > 0 and len(digits) - exp10 >= ndigits:
            return None
        digits.append(b_hl)
    else:
        return None
    return digits, exp10


def _small_int(dbl: float) -> tuple[list[int], int]:
    it = 0
    if dbl >= _TENS[it + 8]:
        it += 8
    if dbl >= _TENS[it + 4]:
        it += 4
    if dbl >= _TENS[it + 2]:
        it += 2
    if dbl >= _TENS[it + 1]:
        it += 1
    exp10 = it + 1
    digits: list[int] = []
    while dbl != 0 and len(digits) < _MAX_RGB and it >= 0:
        b = int(dbl / _TENS[it])
        dbl -= b * _TENS[it]
        digits.append(b)
        it -= 1
    return digits, exp10


def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


def _rgb_precise(dbl: float, ndigits: int = -1) -> tuple[list[int], int]:
    hi, lo = _words(dbl)
    exp2 = ((hi & 0x7FF00000) >> 20) - 1075
    m1 = hi & 0x000FFFFF
    m0 = lo
    single = False
    pow2 = False
    if exp2 == -1075:
        if m1 == 0:
            single = True
        t = _from_words(0x4FF00000, 0) * dbl
        thi, tlo = _words(t)
        w1 = ((thi & 0x7FF00000) >> 20) - (256 + 1023)
        dbl_t = _from_words((thi & 0x000FFFFF) | 0x3FF00000, tlo)
        exp2 += 1
    else:
        dbl_t = _from_words((hi & 0x000FFFFF) | 0x3FF00000, lo)
        w1 = exp2 + 52
        if m0 == 0 and m1 == 0 and exp2 > -1074:
            m1 = 0x00200000
            exp2 -= 1
            pow2 = True
        else:
            m1 |= 0x00100000

    est = (dbl_t - 1.5) * 0.289529654602168 + 0.1760912590558 + w1 * 0.301029995663981
    exp10 = int(est)
    if est < 0 and est != exp10:
        exp10 -= 1

    if exp2 >= 0:
        c2num, c2den = exp2, 0
    else:
        c2num, c2den = 0, -exp2
    if exp10 >= 0:
        c5num, c5den = 0, exp10
        c2den += exp10
    else:
        c2num -= exp10
        c5num, c5den = -exp10, 0
    if c2num > 0 and c2den > 0:
        w = min(c2num, c2den)
        c2num -= w
        c2den -= w
    c2num += 1
    c2den += 1

    mant = m0 if single else (m1 << 32) | m0
    bi_hi = 1
    bi_den = 1
    if c5num > 0:
        bi_hi = 5**c5num
        bi_num = bi_hi * mant
    else:
        bi_num = mant
        if c5den > 0:
            bi_den = 5**c5den

    top = bi_den >> (32 * ((bi_den.bit_length() - 1) // 32))
    w = (_clz32(top) + 28 - c2den) & 0x1F
    c2num += w
    c2den += w
    bi_num <<= c2num
    if c2num > 1:
        bi_hi <<= c2num - 1
    bi_den <<= c2den

    bi_lo = bi_hi
    if pow2:
        bi_hi <<= 1

    even = not (lo & 1)
    digits: list[int] = []
    while len(digits) < _MAX_RGB:
        bt, bi_num = divmod(bi_num, bi_den)
        if not digits and bt == 0:
            exp10 -= 1
        else:
            lo_ref = bi_lo if pow2 else bi_hi
            w1 = _sign(bi_num - lo_ref)
            w2 = 1 if bi_den < bi_hi else _sign(bi_num - (bi_den - bi_hi))
            if w2 == 0 and even:
                if bt == 9:
                    return _round_up9(digits, exp10)
                if w1 > 0:
                    bt += 1
                digits.append(bt)
                break
            if w1 < 0 or (w1 == 0 and even):
                if w2 > 0:
                    bi_num <<= 1
                    w2 = _sign(bi_num - bi_den)
                    if w2 > 0 or (w2 == 0 and (bt & 1)):
                        if bt == 9:
                            return _round_up9(digits, exp10)
                        bt += 1
                digits.append(bt)
                break
            if w2 > 0:
                if bt != 9:
                    digits.append(
                        bt + (1 if ndigits == -1 or len(digits) < ndigits else 0)
                    )
                    break
                return _round_up9(digits, exp10)
            digits.append(bt)
        bi_num *= 10
        bi_hi *= 10
        if pow2:
            bi_lo *= 10
    return digits, exp10 + 1


def _round_up9(digits: list[int], exp10: int) -> tuple[list[int], int]:
    i = len(digits)
    while i > 0:
        i -= 1
        if digits[i] != 9:
            return [*digits[:i], digits[i] + 1], exp10 + 1
    return [1], exp10 + 2


def _digits(x: float, ndigits: int = -1) -> tuple[list[int], int]:
    fast = _rgb_fast(x, ndigits)
    if fast is not None:
        return fast
    return _rgb_precise(x, ndigits)


def _format_digits(digits: list[int], exp10: int) -> str:
    ds = "".join(map(str, digits))
    if exp10 <= -6 or exp10 > 21:
        s = ds[0] + ("." + ds[1:] if len(ds) > 1 else "")
        e = exp10 - 1
        return s + "e" + ("-" if e < 0 else "+") + str(abs(e))
    if exp10 <= 0:
        return "0." + "0" * (-exp10) + ds
    out = ""
    for i, d in enumerate(ds):
        out += d
        if i + 1 == exp10 and i + 1 < len(ds):
            out += "."
    return out + "0" * max(0, exp10 - len(ds))


def to_string(x: float) -> str:
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    digits, exp10 = _digits(abs(x))
    return sign + _format_digits(digits, exp10)


def _round_to(src: list[int], n: int) -> tuple[list[int], int]:
    if len(src) <= n:
        return list(src), 0
    dst = list(src[:n])
    if src[n] >= 5:
        i = n - 1
        while i >= 0:
            if dst[i] + 1 > 9:
                dst[i] = 0
                i -= 1
            else:
                dst[i] += 1
                break
        if i < 0:
            return [1, *dst], 1
    return dst, 0


def _fixed_digits(digits: list[int], exp10: int, frac: int) -> str:
    ds = "".join(map(str, digits))
    if exp10 <= 0:
        if frac < 0:
            frac = -exp10 + len(ds)
        out = "0"
        if frac > 0:
            zeros = min(-exp10, frac)
            body = "0" * zeros
            rest = frac - zeros
            body += ds[:rest]
            body += "0" * (frac - len(body))
            out += "." + body
        return out
    if frac < 0:
        frac = 0 if len(ds) <= exp10 else len(ds) - exp10
    left = ds[:exp10] + "0" * max(0, exp10 - len(ds))
    if frac > 0:
        right = ds[exp10 : exp10 + frac]
        return left + "." + right + "0" * (frac - len(right))
    return left


def _exp_digits(digits: list[int], exp10: int, frac: int) -> str:
    ds = "".join(map(str, digits))
    out = ds[0]
    if frac < 0:
        if len(ds) > 1:
            out += "." + ds[1:]
    elif frac > 0:
        right = ds[1 : 1 + frac]
        out += "." + right + "0" * (frac - len(right))
    e = exp10 - 1
    return out + "e" + ("-" if e < 0 else "+") + str(abs(e))


def _format(x: float, kind: str, n: int) -> str:
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    sign = ""
    if x == 0:
        digits, exp10 = [0], 1
    else:
        if x < 0 or (x == 0 and math.copysign(1, x) < 0):
            sign = "-"
        digits, exp10 = _digits(abs(x))
    if kind == "fixed":
        if n >= 0:
            if exp10 + n > 0:
                adj, carry = _round_to(digits, exp10 + n)
                exp10 += carry
            elif digits[0] >= 5:
                adj = [1]
                exp10 += 1
            else:
                adj = [0]
        else:
            adj, _ = _round_to(digits, _MAX_RGB - 1)
        return sign + _fixed_digits(adj, exp10, n)
    if kind == "exponential":
        if n >= 0:
            adj, carry = _round_to(digits, n + 1)
            exp10 += carry
        else:
            adj, _ = _round_to(digits, _MAX_RGB - 1)
        return sign + _exp_digits(adj, exp10, n)
    adj, carry = _round_to(digits, n)
    exp10 += carry
    if exp10 - 1 < -6 or exp10 - 1 >= n:
        return sign + _exp_digits(adj, exp10, n - 1)
    return sign + _fixed_digits(adj, exp10, n - exp10)


def to_fixed(x: float, digits: int) -> str:
    return _format(x, "fixed", digits)


def to_exponential(x: float, digits: int) -> str:
    return _format(x, "exponential", digits)


def to_precision(x: float, precision: int) -> str:
    return _format(x, "precision", precision)


_SIG_DIGITS = [
    0, 0, 54, 34, 28, 24, 22, 20, 19, 18, 17, 17, 16, 16, 15, 15, 15, 14, 14,
    14, 14, 13, 13, 13, 13, 13, 13, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
]  # fmt: skip


def _to_digit(v: int) -> str:
    return chr(ord("0") + v) if v < 10 else chr(ord("a") - 10 + v)


def _large_radix(x: float, radix: int, total: int) -> str:
    count = 1
    den = 1.0
    while den * radix <= x:
        den *= radix
        count += 1
    m = x / den
    first = int(m)
    m -= first
    frac = ""
    sig = 1
    while m != 0 and sig < total:
        m *= radix
        d = min(int(m), radix - 1)
        m -= d
        frac += _to_digit(d)
        sig += 1
    frac = frac.rstrip("0")
    return _to_digit(first) + ("." + frac if frac else "") + f"(e+{count - 1})"


def to_radix(x: float, radix: int) -> str:
    """``Number.prototype.toString(radix)`` for radix != 10."""
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    out = ""
    if x < 0:
        out = "-"
        x = -x
    bits = {2: 1, 4: 2, 8: 3, 16: 4, 32: 5}.get(radix, 0)
    max_out = _SIG_DIGITS[radix]
    if x >= 2.0**61:
        return out + _large_radix(x, radix, max_out)
    if x >= 1:
        if bits:
            hi, _ = _words(x)
            e2 = ((hi & 0x7FF00000) >> 20) - 0x3FF
            we = e2 // bits if e2 >= 0 else -((-e2) // bits)
            e2 = we * bits
            den = _from_words(((0x3FF + e2) << 20) & _MASK, 0)
            sig = abs(we) + 1
        else:
            sig = 1
            den = 1.0
            while True:
                t = den * radix
                if t > x:
                    break
                den = t
                sig += 1
        for _ in range(sig):
            d = int(x / den)
            if d >= radix:
                d = radix - 1
            out += _to_digit(d)
            x -= d * den
            den /= radix
    else:
        out += "0"
        sig = 0
    if x != 0 and sig < max_out:
        out += "."
        while True:
            x *= radix
            d = int(x)
            if d >= radix:
                d = radix - 1
            x -= d
            out += _to_digit(d)
            if d != 0 or sig != 0:
                sig += 1
            if x == 0 or sig >= max_out:
                break
    return out
