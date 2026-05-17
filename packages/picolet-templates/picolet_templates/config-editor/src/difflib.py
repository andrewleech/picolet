"""difflib.py — minimal unified_diff extraction for MicroPython.

Vendored single-function subset for the picolet config-editor example.
Only unified_diff() is implemented. Produces output compatible with the
standard unified diff format.

Licence: MIT (Picolet project). Algorithm based on the CPython difflib module
(PSF Licence) — adapted for MicroPython compatibility.
"""
from __future__ import annotations


def unified_diff(
    a: list,
    b: list,
    fromfile: str = '',
    tofile: str = '',
    fromfiledate: str = '',
    tofiledate: str = '',
    n: int = 3,
    lineterm: str = '\n',
) -> list:
    """Compare two sequences of lines; generate the delta as a unified diff.

    Returns a list of strings. Arguments match the CPython difflib interface.
    """
    result = []
    groups = _grouped_opcodes(_get_opcodes(a, b), n)
    first = True
    for group in groups:
        if first:
            from_header = fromfile + ('\t' + fromfiledate if fromfiledate else '')
            to_header = tofile + ('\t' + tofiledate if tofiledate else '')
            lt = lineterm if lineterm else ''
            result.append(f'--- {from_header}{lt}')
            result.append(f'+++ {to_header}{lt}')
            first = False

        # Calculate hunk header extents.
        i1, i2, j1, j2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
        a_count = i2 - i1
        b_count = j2 - j1
        lt = lineterm if lineterm else ''
        result.append(f'@@ -{i1 + 1},{a_count} +{j1 + 1},{b_count} @@{lt}')

        for tag, oi1, oi2, oj1, oj2 in group:
            if tag == 'equal':
                for line in a[oi1:oi2]:
                    s = line.rstrip('\n') if lineterm == '' and line.endswith('\n') else line
                    result.append(f' {s}')
            if tag in ('replace', 'delete'):
                for line in a[oi1:oi2]:
                    s = line.rstrip('\n') if lineterm == '' and line.endswith('\n') else line
                    result.append(f'-{s}')
            if tag in ('replace', 'insert'):
                for line in b[oj1:oj2]:
                    s = line.rstrip('\n') if lineterm == '' and line.endswith('\n') else line
                    result.append(f'+{s}')

    return result


def _grouped_opcodes(opcodes: list, n: int) -> list:
    """Group opcodes into hunks with n context lines on each side."""
    groups = []
    # Pad with context 'equal' blocks.
    padded = []
    if opcodes:
        tag, i1, i2, j1, j2 = opcodes[0]
        if tag == 'equal':
            # Trim leading context to at most n lines.
            padded.append(('equal', max(i1, i2 - n), i2, max(j1, j2 - n), j2))
        else:
            padded.append(opcodes[0])
        for op in opcodes[1:-1]:
            padded.append(op)
        if len(opcodes) > 1:
            tag, i1, i2, j1, j2 = opcodes[-1]
            if tag == 'equal':
                padded.append(('equal', i1, min(i2, i1 + n), j1, min(j2, j1 + n)))
            else:
                padded.append(opcodes[-1])
    else:
        return []

    group: list = []
    for tag, i1, i2, j1, j2 in padded:
        if tag == 'equal' and i2 - i1 > 2 * n:
            # Large equal block — split: keep n lines at end of current group,
            # start new group with n lines.
            group.append(('equal', i1, min(i2, i1 + n), j1, min(j2, j1 + n)))
            if group:
                groups.append(group)
            group = [('equal', max(i1, i2 - n), i2, max(j1, j2 - n), j2)]
        else:
            group.append((tag, i1, i2, j1, j2))
    if group:
        # Only emit groups that contain at least one non-equal opcode.
        if any(t != 'equal' for t, *_ in group):
            groups.append(group)

    return groups


# ---------------------------------------------------------------------------
# Sequence matcher — DP LCS
# ---------------------------------------------------------------------------

def _get_opcodes(a: list, b: list) -> list:
    """Return list of (tag, i1, i2, j1, j2) tuples describing how to transform a → b."""
    n, m = len(a), len(b)

    if n == 0 and m == 0:
        return []
    if n == 0:
        return [('insert', 0, 0, 0, m)]
    if m == 0:
        return [('delete', 0, n, 0, 0)]

    # Build LCS table (O(n*m)).
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    # Backtrack.
    raw: list = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1]:
            raw.append(('equal', i - 1, i, j - 1, j))
            i -= 1
            j -= 1
        elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
            raw.append(('insert', i, i, j - 1, j))
            j -= 1
        else:
            raw.append(('delete', i - 1, i, j, j))
            i -= 1
    raw.reverse()

    # Merge adjacent same-tag blocks.
    merged: list = []
    for op in raw:
        if merged and merged[-1][0] == op[0]:
            p = merged[-1]
            if p[2] == op[1] and p[4] == op[3]:
                merged[-1] = (p[0], p[1], op[2], p[3], op[4])
                continue
        merged.append(list(op))

    # Convert adjacent delete+insert → replace.
    final: list = []
    k = 0
    while k < len(merged):
        cur = merged[k]
        if (k + 1 < len(merged)
                and cur[0] == 'delete'
                and merged[k + 1][0] == 'insert'
                and cur[2] == merged[k + 1][1]):
            nxt = merged[k + 1]
            final.append(('replace', cur[1], cur[2], nxt[3], nxt[4]))
            k += 2
        else:
            final.append(tuple(cur))
            k += 1

    return final
