"""Preserve line and block comments by anchoring source trivia to corresponding tokens."""
from bisect import bisect_left, bisect_right
from collections import Counter
from difflib import SequenceMatcher
from lexer.Lexer import Lexer


def _scan_comments(source):
    """Collect (start_line, start_col, end_line, text) tuples outside string literals."""
    comments = []
    n = len(source)
    i = 0
    line = 1
    col = 1
    quoted = False
    escaped = False
    while i < n:
        ch = source[i]
        if quoted:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                quoted = False
            if ch == '\n':
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue
        if ch == '"':
            quoted = True
            i += 1
            col += 1
            continue
        if ch == '#':
            start = i
            s_line, s_col = line, col
            while i < n and source[i] not in ('\n', '\r'):
                i += 1
                col += 1
            comments.append((s_line, s_col, s_line, source[start:i].rstrip()))
            continue
        if ch == '/' and i + 1 < n and source[i + 1] == '*':
            start = i
            s_line, s_col = line, col
            depth = 1
            i += 2
            col += 2
            while i < n and depth > 0:
                if source[i] == '/' and i + 1 < n and source[i + 1] == '*':
                    depth += 1
                    i += 2
                    col += 2
                elif source[i] == '*' and i + 1 < n and source[i + 1] == '/':
                    depth -= 1
                    i += 2
                    col += 2
                elif source[i] == '\n':
                    line += 1
                    col = 1
                    i += 1
                else:
                    i += 1
                    col += 1
            comments.append((s_line, s_col, line, source[start:i].rstrip()))
            continue
        if ch == '\n':
            line += 1
            col = 1
        else:
            col += 1
        i += 1
    return comments


def _patience_anchors(a, b, lo_a, hi_a, lo_b, hi_b):
    """Return strictly increasing unique-token anchor pairs via O(U log U) Patience LIS."""
    counts_a = Counter(a[lo_a:hi_a])
    counts_b = Counter(b[lo_b:hi_b])
    pos_b = {}
    for j in range(lo_b, hi_b):
        key = b[j]
        if counts_b[key] == 1 and counts_a.get(key) == 1:
            pos_b[key] = j
    if not pos_b:
        return []
    pairs = [(i, pos_b[a[i]]) for i in range(lo_a, hi_a) if a[i] in pos_b]
    piles = []
    pile_indices = []
    backrefs = [-1] * len(pairs)
    for idx, (_, b_idx) in enumerate(pairs):
        pos = bisect_left(piles, b_idx)
        if pos == len(piles):
            piles.append(b_idx)
            pile_indices.append(idx)
        else:
            piles[pos] = b_idx
            pile_indices[pos] = idx
        backrefs[idx] = pile_indices[pos - 1] if pos > 0 else -1
    if not pile_indices:
        return []
    anchors = []
    cur = pile_indices[-1]
    while cur != -1:
        anchors.append(pairs[cur])
        cur = backrefs[cur]
    anchors.reverse()
    return anchors


def _align_tokens(a, b):
    """Map matching token indices from `a` to `b` without quadratic SequenceMatcher stalls."""
    mapping = {}
    stack = [(0, len(a), 0, len(b))]
    while stack:
        la, ha, lb, hb = stack.pop()
        while la < ha and lb < hb and a[la] == b[lb]:
            mapping[la] = lb
            la += 1
            lb += 1
        while la < ha and lb < hb and a[ha - 1] == b[hb - 1]:
            ha -= 1
            hb -= 1
            mapping[ha] = hb
        if la >= ha or lb >= hb:
            continue
        if (ha - la) <= 2000 and (hb - lb) <= 2000:
            matcher = SequenceMatcher(None, a[la:ha], b[lb:hb], autojunk=False)
            for ia, ib, size in matcher.get_matching_blocks():
                for offset in range(size):
                    mapping[la + ia + offset] = lb + ib + offset
            continue
        anchors = _patience_anchors(a, b, la, ha, lb, hb)
        if not anchors:
            matcher = SequenceMatcher(None, a[la:ha], b[lb:hb], autojunk=True)
            for ia, ib, size in matcher.get_matching_blocks():
                for offset in range(size):
                    mapping[la + ia + offset] = lb + ib + offset
            continue
        cur_a, cur_b = la, lb
        sub_slices = []
        for anchor_a, anchor_b in anchors:
            if anchor_a > cur_a or anchor_b > cur_b:
                sub_slices.append((cur_a, anchor_a, cur_b, anchor_b))
            mapping[anchor_a] = anchor_b
            cur_a = anchor_a + 1
            cur_b = anchor_b + 1
        if cur_a < ha or cur_b < hb:
            sub_slices.append((cur_a, ha, cur_b, hb))
        stack.extend(reversed(sub_slices))
    return mapping


def preserve_comments(source, formatted):
    comments = _scan_comments(source)
    if not comments:
        return formatted
    original = Lexer(source).tokenize()[:-1]
    output = Lexer(formatted).tokenize()[:-1]

    def key(token):
        return (token.type.name, str(token.value))

    mapping = _align_tokens([key(t) for t in original], [key(t) for t in output])
    orig_positions = [(token.line, token.col) for token in original]
    mapped_indices = sorted(mapping)
    mapped_positions = [orig_positions[i] for i in mapped_indices]
    mapped_non_nl = [i for i in mapped_indices if original[i].type.name != 'NEWLINE']
    mapped_non_nl_positions = [orig_positions[i] for i in mapped_non_nl]
    lines = formatted.splitlines()
    before, inline = {}, {}
    for line, column, end_line, text in comments:
        prev_pos = bisect_left(orig_positions, (line, column)) - 1
        previous = prev_pos if prev_pos >= 0 else None
        non_nl_pos = bisect_right(mapped_non_nl_positions, (line, column))
        first_following = mapped_non_nl[non_nl_pos] if non_nl_pos < len(mapped_non_nl) else None
        has_same_line_after = bool(first_following is not None and original[first_following].line == end_line)
        if ('\n' not in text and previous is not None and original[previous].line == line
                and previous in mapping and not has_same_line_after):
            target = output[mapping[previous]].line - 1
            inline.setdefault(target, []).append(text)
        else:
            all_pos = bisect_right(mapped_positions, (line, column))
            target = output[mapping[mapped_indices[all_pos]]].line - 1 if all_pos < len(mapped_indices) else len(lines)
            before.setdefault(target, []).append(text)
    rendered = []
    for index in range(len(lines) + 1):
        indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())] if index < len(lines) else ''
        for text in before.get(index, []):
            if '\n' in text:
                part_lines = text.splitlines()
                rendered.append(indent + part_lines[0])
                for extra in part_lines[1:]:
                    rendered.append(extra)
            else:
                rendered.append(indent + text)
        if index < len(lines):
            rendered.append(lines[index] + (' ' + ' '.join(inline[index]) if index in inline else ''))
    return '\n'.join(rendered) + '\n'
