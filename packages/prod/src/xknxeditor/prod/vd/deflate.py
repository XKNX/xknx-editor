"""The raw deflate stream of the zlib port in DotNetZip (``Ionic.Zlib.DeflateStream`` at its
default level), byte for byte.

The converter compresses the ex-factory image of a Hardware2Program with it, and the image is
part of the hashed content, so the bytes must match. zlib itself differs: this port also ends a
block early after 8192 symbols when the block compresses well (the ``TRUNCATE_BLOCK`` heuristic
of old zlib versions), so large images would come out differently.
"""

from __future__ import annotations

# level 6: lazy matching
_GOOD_LENGTH = 8
_MAX_LAZY = 16
_NICE_LENGTH = 128
_MAX_CHAIN = 128

_W_BITS = 15
_W_SIZE = 1 << _W_BITS
_W_MASK = _W_SIZE - 1
_WINDOW_SIZE = 2 * _W_SIZE
_HASH_BITS = 8 + 7
_HASH_SIZE = 1 << _HASH_BITS
_HASH_MASK = _HASH_SIZE - 1
_HASH_SHIFT = (_HASH_BITS + 3 - 1) // 3
_LIT_BUFSIZE = 1 << (8 + 6)

_MIN_MATCH = 3
_MAX_MATCH = 258
_MIN_LOOKAHEAD = _MAX_MATCH + _MIN_MATCH + 1
_MAX_DIST = _W_SIZE - _MIN_LOOKAHEAD
_TOO_FAR = 4096

_MAX_BITS = 15
_MAX_BL_BITS = 7
_LITERALS = 256
_LENGTH_CODES = 29
_L_CODES = _LITERALS + 1 + _LENGTH_CODES
_D_CODES = 30
_BL_CODES = 19
_HEAP_SIZE = 2 * _L_CODES + 1
_END_BLOCK = 256
_REP_3_6 = 16
_REPZ_3_10 = 17
_REPZ_11_138 = 18

# Stream.CopyTo hands the input over in pieces of this size
_COPY_BUFFER = 81920

_EXTRA_LBITS = (0,) * 8 + (1,) * 4 + (2,) * 4 + (3,) * 4 + (4,) * 4 + (5,) * 4 + (0,)
_EXTRA_DBITS = tuple(max(0, i // 2 - 1) for i in range(_D_CODES))
_EXTRA_BLBITS = (0,) * 16 + (2, 3, 7)
_BL_ORDER = (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15)


def _code_tables() -> tuple[list[int], list[int], list[int], list[int]]:
    length_code = [0] * 256
    base_length = [0] * _LENGTH_CODES
    length = 0
    for code in range(_LENGTH_CODES - 1):
        base_length[code] = length
        for _ in range(1 << _EXTRA_LBITS[code]):
            length_code[length] = code
            length += 1
    # length 258 has a code of its own
    length_code[length - 1] = _LENGTH_CODES - 1
    dist_code = [0] * 512
    base_dist = [0] * _D_CODES
    dist = 0
    for code in range(16):
        base_dist[code] = dist
        for _ in range(1 << _EXTRA_DBITS[code]):
            dist_code[dist] = code
            dist += 1
    dist >>= 7
    for code in range(16, _D_CODES):
        base_dist[code] = dist << 7
        for _ in range(1 << (_EXTRA_DBITS[code] - 7)):
            dist_code[256 + dist] = code
            dist += 1
    return length_code, base_length, dist_code, base_dist


_LENGTH_CODE, _BASE_LENGTH, _DIST_CODE, _BASE_DIST = _code_tables()


def _d_code(dist: int) -> int:
    return _DIST_CODE[dist] if dist < 256 else _DIST_CODE[256 + (dist >> 7)]


def _bi_reverse(code: int, length: int) -> int:
    result = 0
    for _ in range(length):
        result = (result << 1) | (code & 1)
        code >>= 1
    return result


def _gen_codes(lengths: list[int], max_code: int, bl_count: list[int]) -> list[int]:
    next_code = [0] * (_MAX_BITS + 1)
    code = 0
    for bits in range(1, _MAX_BITS + 1):
        code = (code + bl_count[bits - 1]) << 1
        next_code[bits] = code
    codes = [0] * len(lengths)
    for n in range(max_code + 1):
        length = lengths[n]
        if length:
            codes[n] = _bi_reverse(next_code[length], length)
            next_code[length] += 1
    return codes


def _static_trees() -> tuple[list[int], list[int], list[int], list[int]]:
    lengths = [8] * 144 + [9] * 112 + [7] * 24 + [8] * 8
    counts = [0] * (_MAX_BITS + 1)
    for length in lengths:
        counts[length] += 1
    codes = _gen_codes(lengths, _L_CODES + 1, counts)
    return codes, lengths, [_bi_reverse(n, 5) for n in range(_D_CODES)], [5] * _D_CODES


_STATIC_LCODES, _STATIC_LLENS, _STATIC_DCODES, _STATIC_DLENS = _static_trees()


class _Tree:
    """A dynamic tree: frequencies in, code lengths and codes out (``build_tree``)."""

    def __init__(
        self,
        elems: int,
        static_lens: list[int] | None,
        extra: tuple[int, ...],
        extra_base: int,
        max_length: int,
    ) -> None:
        self.freq = [0] * (2 * elems + 1)
        self.dad = [0] * (2 * elems + 1)
        self.length = [0] * (2 * elems + 1)
        self.code = [0] * (2 * elems + 1)
        self.elems = elems
        self.static_lens = static_lens
        self.extra = extra
        self.extra_base = extra_base
        self.max_length = max_length
        self.max_code = 0


class _Deflater:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.next_in = 0
        self.avail_end = 0
        self.out = bytearray()
        self.bit_buf = 0
        self.bit_count = 0
        self.window = bytearray(_WINDOW_SIZE)
        self.prev = [0] * _W_SIZE
        self.head = [0] * _HASH_SIZE
        self.ins_h = 0
        self.strstart = 0
        self.block_start = 0
        self.lookahead = 0
        self.match_length = _MIN_MATCH - 1
        self.prev_length = _MIN_MATCH - 1
        self.match_start = 0
        self.match_available = False
        self.ltree = _Tree(
            _L_CODES, _STATIC_LLENS, _EXTRA_LBITS, _LITERALS + 1, _MAX_BITS
        )
        self.dtree = _Tree(_D_CODES, _STATIC_DLENS, _EXTRA_DBITS, 0, _MAX_BITS)
        self.bltree = _Tree(_BL_CODES, None, _EXTRA_BLBITS, 0, _MAX_BL_BITS)
        self.heap = [0] * _HEAP_SIZE
        self.heap_len = 0
        self.heap_max = 0
        self.depth = [0] * _HEAP_SIZE
        self.bl_count = [0] * (_MAX_BITS + 1)
        self.symbols: list[tuple[int, int]] = []
        self.matches = 0
        self.opt_len = 0
        self.static_len = 0
        self._init_block()

    # ---- bit output

    def send_bits(self, value: int, length: int) -> None:
        self.bit_buf |= value << self.bit_count
        self.bit_count += length
        while self.bit_count >= 8:
            self.out.append(self.bit_buf & 0xFF)
            self.bit_buf >>= 8
            self.bit_count -= 8

    def bi_windup(self) -> None:
        if self.bit_count > 0:
            self.out.append(self.bit_buf & 0xFF)
        self.bit_buf = 0
        self.bit_count = 0

    # ---- input

    def fill_window(self) -> None:
        window = self.window
        while True:
            more = _WINDOW_SIZE - self.lookahead - self.strstart
            if self.strstart >= _W_SIZE + _MAX_DIST:
                window[:_W_SIZE] = window[_W_SIZE:]
                self.match_start -= _W_SIZE
                self.strstart -= _W_SIZE
                self.block_start -= _W_SIZE
                self.head = [h - _W_SIZE if h >= _W_SIZE else 0 for h in self.head]
                self.prev = [p - _W_SIZE if p >= _W_SIZE else 0 for p in self.prev]
                more += _W_SIZE
            available = self.avail_end - self.next_in
            if available == 0:
                return
            n = min(available, more)
            if n:
                at = self.strstart + self.lookahead
                window[at : at + n] = self.data[self.next_in : self.next_in + n]
                self.next_in += n
                self.lookahead += n
            if self.lookahead >= _MIN_MATCH:
                self.ins_h = (
                    (window[self.strstart] << _HASH_SHIFT) ^ window[self.strstart + 1]
                ) & _HASH_MASK
            if not (self.lookahead < _MIN_LOOKAHEAD and self.avail_end > self.next_in):
                return

    def insert_string(self, pos: int) -> int:
        self.ins_h = (
            (self.ins_h << _HASH_SHIFT) ^ self.window[pos + _MIN_MATCH - 1]
        ) & _HASH_MASK
        hash_head = self.head[self.ins_h]
        self.prev[pos & _W_MASK] = hash_head
        self.head[self.ins_h] = pos
        return hash_head

    # ---- matching

    def longest_match(self, cur_match: int) -> int:
        window = self.window
        chain = _MAX_CHAIN
        scan = self.strstart
        best_len = self.prev_length
        limit = scan - _MAX_DIST if scan > _MAX_DIST else 0
        nice = _NICE_LENGTH
        scan_end1 = window[scan + best_len - 1]
        scan_end = window[scan + best_len]
        if self.prev_length >= _GOOD_LENGTH:
            chain >>= 2
        nice = min(nice, self.lookahead)
        while True:
            match = cur_match
            if (
                window[match + best_len] == scan_end
                and window[match + best_len - 1] == scan_end1
                and window[match] == window[scan]
                and window[match + 1] == window[scan + 1]
            ):
                # the third bytes are equal by the hash; the scan stops at a difference
                length = 3
                if (
                    window[scan + 3 : scan + _MAX_MATCH]
                    == window[match + 3 : match + _MAX_MATCH]
                ):
                    length = _MAX_MATCH
                else:
                    while window[scan + length] == window[match + length]:
                        length += 1
                if length > best_len:
                    self.match_start = cur_match
                    best_len = length
                    if length >= nice:
                        break
                    scan_end1 = window[scan + best_len - 1]
                    scan_end = window[scan + best_len]
            cur_match = self.prev[cur_match & _W_MASK]
            if cur_match <= limit:
                break
            chain -= 1
            if chain == 0:
                break
        return min(best_len, self.lookahead)

    def tally(self, dist: int, lc: int) -> bool:
        """Records a literal (``dist`` 0) or a match; whether the block ends here."""
        self.symbols.append((dist, lc))
        last_lit = len(self.symbols)
        if dist == 0:
            self.ltree.freq[lc] += 1
        else:
            self.matches += 1
            self.ltree.freq[_LENGTH_CODE[lc] + _LITERALS + 1] += 1
            self.dtree.freq[_d_code(dist - 1)] += 1
        if last_lit & 0x1FFF == 0:
            out_length = last_lit << 3
            in_length = self.strstart - self.block_start
            for code in range(_D_CODES):
                out_length += self.dtree.freq[code] * (5 + _EXTRA_DBITS[code])
            out_length >>= 3
            if self.matches < last_lit // 2 and out_length < in_length // 2:
                return True
        return last_lit in (_LIT_BUFSIZE - 1, _LIT_BUFSIZE)

    def deflate(self) -> bytes:
        finishing = False
        self.avail_end = min(len(self.data), _COPY_BUFFER)
        while True:
            if self.lookahead < _MIN_LOOKAHEAD:
                self.fill_window()
                if self.lookahead < _MIN_LOOKAHEAD and not finishing:
                    if self.avail_end < len(self.data):
                        self.avail_end = min(
                            len(self.data), self.avail_end + _COPY_BUFFER
                        )
                    else:
                        finishing = True
                    continue
                if self.lookahead == 0:
                    break
            hash_head = 0
            if self.lookahead >= _MIN_MATCH:
                hash_head = self.insert_string(self.strstart)
            self.prev_length = self.match_length
            prev_match = self.match_start
            self.match_length = _MIN_MATCH - 1
            if (
                hash_head != 0
                and self.prev_length < _MAX_LAZY
                and ((self.strstart - hash_head) & 0xFFFF) <= _MAX_DIST
            ):
                self.match_length = self.longest_match(hash_head)
                if (
                    self.match_length == _MIN_MATCH
                    and self.strstart - self.match_start > _TOO_FAR
                ):
                    self.match_length = _MIN_MATCH - 1
            if self.prev_length >= _MIN_MATCH and self.match_length <= self.prev_length:
                max_insert = self.strstart + self.lookahead - _MIN_MATCH
                done = self.tally(
                    self.strstart - 1 - prev_match, self.prev_length - _MIN_MATCH
                )
                self.lookahead -= self.prev_length - 1
                for _ in range(self.prev_length - 2):
                    self.strstart += 1
                    if self.strstart <= max_insert:
                        self.insert_string(self.strstart)
                self.match_available = False
                self.match_length = _MIN_MATCH - 1
                self.strstart += 1
                if done:
                    self.flush_block(eof=False)
            elif self.match_available:
                if self.tally(0, self.window[self.strstart - 1]):
                    self.flush_block(eof=False)
                self.strstart += 1
                self.lookahead -= 1
            else:
                self.match_available = True
                self.strstart += 1
                self.lookahead -= 1
        if self.match_available:
            self.tally(0, self.window[self.strstart - 1])
            self.match_available = False
        self.flush_block(eof=True)
        return bytes(self.out)

    # ---- blocks

    def _init_block(self) -> None:
        for tree in (self.ltree, self.dtree, self.bltree):
            for n in range(tree.elems):
                tree.freq[n] = 0
        self.ltree.freq[_END_BLOCK] = 1
        self.opt_len = 0
        self.static_len = 0
        self.symbols = []
        self.matches = 0

    def flush_block(self, *, eof: bool) -> None:
        stored_len = self.strstart - self.block_start
        self.build_tree(self.ltree)
        self.build_tree(self.dtree)
        max_blindex = self.build_bl_tree()
        opt_lenb = (self.opt_len + 3 + 7) >> 3
        static_lenb = (self.static_len + 3 + 7) >> 3
        opt_lenb = min(opt_lenb, static_lenb)
        if stored_len + 4 <= opt_lenb and self.block_start >= 0:
            self.send_bits(int(eof), 3)
            self.bi_windup()
            self.out += stored_len.to_bytes(2, "little")
            self.out += (~stored_len & 0xFFFF).to_bytes(2, "little")
            start = self.block_start
            self.out += self.window[start : start + stored_len]
        elif static_lenb == opt_lenb:
            self.send_bits((1 << 1) + int(eof), 3)
            self.compress_block(
                _STATIC_LCODES, _STATIC_LLENS, _STATIC_DCODES, _STATIC_DLENS
            )
        else:
            self.send_bits((2 << 1) + int(eof), 3)
            self.send_all_trees(
                self.ltree.max_code + 1, self.dtree.max_code + 1, max_blindex + 1
            )
            self.compress_block(
                self.ltree.code, self.ltree.length, self.dtree.code, self.dtree.length
            )
        self._init_block()
        if eof:
            self.bi_windup()
        self.block_start = self.strstart

    def compress_block(
        self, lcodes: list[int], llens: list[int], dcodes: list[int], dlens: list[int]
    ) -> None:
        for dist, lc in self.symbols:
            if dist == 0:
                self.send_bits(lcodes[lc], llens[lc])
                continue
            code = _LENGTH_CODE[lc]
            self.send_bits(lcodes[code + _LITERALS + 1], llens[code + _LITERALS + 1])
            extra = _EXTRA_LBITS[code]
            if extra:
                self.send_bits(lc - _BASE_LENGTH[code], extra)
            dist -= 1
            code = _d_code(dist)
            self.send_bits(dcodes[code], dlens[code])
            extra = _EXTRA_DBITS[code]
            if extra:
                self.send_bits(dist - _BASE_DIST[code], extra)
        self.send_bits(lcodes[_END_BLOCK], llens[_END_BLOCK])

    # ---- trees

    def _smaller(self, tree: _Tree, n: int, m: int) -> bool:
        return tree.freq[n] < tree.freq[m] or (
            tree.freq[n] == tree.freq[m] and self.depth[n] <= self.depth[m]
        )

    def pqdownheap(self, tree: _Tree, k: int) -> None:
        heap = self.heap
        v = heap[k]
        j = k << 1
        while j <= self.heap_len:
            if j < self.heap_len and self._smaller(tree, heap[j + 1], heap[j]):
                j += 1
            if self._smaller(tree, v, heap[j]):
                break
            heap[k] = heap[j]
            k = j
            j <<= 1
        heap[k] = v

    def build_tree(self, tree: _Tree) -> None:
        heap = self.heap
        freq = tree.freq
        max_code = -1
        self.heap_len = 0
        self.heap_max = _HEAP_SIZE
        for n in range(tree.elems):
            if freq[n] != 0:
                self.heap_len += 1
                heap[self.heap_len] = max_code = n
                self.depth[n] = 0
            else:
                tree.length[n] = 0
        while self.heap_len < 2:
            if max_code < 2:
                max_code += 1
                node = max_code
            else:
                node = 0
            self.heap_len += 1
            heap[self.heap_len] = node
            freq[node] = 1
            self.depth[node] = 0
            self.opt_len -= 1
            if tree.static_lens is not None:
                self.static_len -= tree.static_lens[node]
        tree.max_code = max_code
        for n in range(self.heap_len // 2, 0, -1):
            self.pqdownheap(tree, n)
        node = tree.elems
        while True:
            n = heap[1]
            heap[1] = heap[self.heap_len]
            self.heap_len -= 1
            self.pqdownheap(tree, 1)
            m = heap[1]
            self.heap_max -= 1
            heap[self.heap_max] = n
            self.heap_max -= 1
            heap[self.heap_max] = m
            freq[node] = freq[n] + freq[m]
            self.depth[node] = max(self.depth[n], self.depth[m]) + 1
            tree.dad[n] = tree.dad[m] = node
            heap[1] = node
            node += 1
            self.pqdownheap(tree, 1)
            if self.heap_len < 2:
                break
        self.heap_max -= 1
        heap[self.heap_max] = heap[1]
        self.gen_bitlen(tree)
        tree.code = _gen_codes(tree.length, max_code, self.bl_count)

    def gen_bitlen(self, tree: _Tree) -> None:
        heap = self.heap
        bl_count = self.bl_count
        for bits in range(_MAX_BITS + 1):
            bl_count[bits] = 0
        # the dads (Len) of the nodes, from the root down
        tree.length[heap[self.heap_max]] = 0
        overflow = 0
        h = self.heap_max + 1
        while h < _HEAP_SIZE:
            n = heap[h]
            bits = tree.length[tree.dad[n]] + 1
            if bits > tree.max_length:
                bits = tree.max_length
                overflow += 1
            tree.length[n] = bits
            if n <= tree.max_code:
                bl_count[bits] += 1
                xbits = tree.extra[n - tree.extra_base] if n >= tree.extra_base else 0
                f = tree.freq[n]
                self.opt_len += f * (bits + xbits)
                if tree.static_lens is not None:
                    self.static_len += f * (tree.static_lens[n] + xbits)
            h += 1
        if overflow == 0:
            return
        while True:
            bits = tree.max_length - 1
            while bl_count[bits] == 0:
                bits -= 1
            bl_count[bits] -= 1
            bl_count[bits + 1] += 2
            bl_count[tree.max_length] -= 1
            overflow -= 2
            if overflow <= 0:
                break
        for bits in range(tree.max_length, 0, -1):
            n = bl_count[bits]
            while n != 0:
                h -= 1
                m = heap[h]
                if m > tree.max_code:
                    continue
                if tree.length[m] != bits:
                    self.opt_len += (bits - tree.length[m]) * tree.freq[m]
                    tree.length[m] = bits
                n -= 1

    def scan_tree(self, tree: _Tree, max_code: int) -> None:
        bl = self.bltree.freq
        lengths = [*tree.length[: max_code + 1], 0x7FFF]
        prevlen = -1
        nextlen = lengths[0]
        count = 0
        max_count, min_count = (138, 3) if nextlen == 0 else (7, 4)
        for n in range(max_code + 1):
            curlen = nextlen
            nextlen = lengths[n + 1]
            count += 1
            if count < max_count and curlen == nextlen:
                continue
            if count < min_count:
                bl[curlen] += count
            elif curlen != 0:
                if curlen != prevlen:
                    bl[curlen] += 1
                bl[_REP_3_6] += 1
            elif count <= 10:
                bl[_REPZ_3_10] += 1
            else:
                bl[_REPZ_11_138] += 1
            count = 0
            prevlen = curlen
            if nextlen == 0:
                max_count, min_count = 138, 3
            elif curlen == nextlen:
                max_count, min_count = 6, 3
            else:
                max_count, min_count = 7, 4

    def send_tree(self, tree: _Tree, max_code: int) -> None:
        codes = self.bltree.code
        lens = self.bltree.length
        lengths = [*tree.length[: max_code + 1], 0x7FFF]
        prevlen = -1
        nextlen = lengths[0]
        count = 0
        max_count, min_count = (138, 3) if nextlen == 0 else (7, 4)
        for n in range(max_code + 1):
            curlen = nextlen
            nextlen = lengths[n + 1]
            count += 1
            if count < max_count and curlen == nextlen:
                continue
            if count < min_count:
                for _ in range(count):
                    self.send_bits(codes[curlen], lens[curlen])
            elif curlen != 0:
                if curlen != prevlen:
                    self.send_bits(codes[curlen], lens[curlen])
                    count -= 1
                self.send_bits(codes[_REP_3_6], lens[_REP_3_6])
                self.send_bits(count - 3, 2)
            elif count <= 10:
                self.send_bits(codes[_REPZ_3_10], lens[_REPZ_3_10])
                self.send_bits(count - 3, 3)
            else:
                self.send_bits(codes[_REPZ_11_138], lens[_REPZ_11_138])
                self.send_bits(count - 11, 7)
            count = 0
            prevlen = curlen
            if nextlen == 0:
                max_count, min_count = 138, 3
            elif curlen == nextlen:
                max_count, min_count = 6, 3
            else:
                max_count, min_count = 7, 4

    def build_bl_tree(self) -> int:
        self.scan_tree(self.ltree, self.ltree.max_code)
        self.scan_tree(self.dtree, self.dtree.max_code)
        self.build_tree(self.bltree)
        max_blindex = _BL_CODES - 1
        while max_blindex >= 3 and self.bltree.length[_BL_ORDER[max_blindex]] == 0:
            max_blindex -= 1
        self.opt_len += 3 * (max_blindex + 1) + 5 + 5 + 4
        return max_blindex

    def send_all_trees(self, lcodes: int, dcodes: int, blcodes: int) -> None:
        self.send_bits(lcodes - 257, 5)
        self.send_bits(dcodes - 1, 5)
        self.send_bits(blcodes - 4, 4)
        for rank in range(blcodes):
            self.send_bits(self.bltree.length[_BL_ORDER[rank]], 3)
        self.send_tree(self.ltree, lcodes - 1)
        self.send_tree(self.dtree, dcodes - 1)


def deflate(data: bytes) -> bytes:
    """``data`` compressed as a raw deflate stream; no input gives no output at all."""
    if not data:
        return b""
    return _Deflater(data).deflate()
