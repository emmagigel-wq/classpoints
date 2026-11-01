"""Compare a candidate function source against the original pyc code object.

用法: python3.14 tools/cmpfn.py <mod.dotted> <qualname> <candidate.py>
candidate.py 里应定义同名函数（源码片段）。
"""
import marshal
import os
import sys
import dis

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_pyc(mod):
    p = os.path.join(ROOT, 'recover', 'app.exe_extracted', 'PYZ.pyz_extracted',
                     mod.replace('.', '/') + '.pyc')
    with open(p, 'rb') as f:
        f.read(16)
        return marshal.load(f)


def flat(c, out):
    out.append(c)
    for k in c.co_consts:
        if hasattr(k, 'co_name'):
            flat(k, out)
    return out


def seq(code):
    out = []
    for i in dis.get_instructions(code):
        if i.opname in ('CACHE', 'RESUME'):
            continue
        av = i.argval
        if hasattr(av, 'co_code'):
            av = ('CODE', av.co_qualname)
        out.append((i.opname, av))
    return out


def main():
    mod, qual, cand = sys.argv[1], sys.argv[2], sys.argv[3]
    src = open(cand, encoding='utf-8').read()
    co = compile(src, 'cand.py', 'exec', dont_inherit=True)
    got = None
    for c in flat(co, []):
        if c.co_qualname == qual:
            got = c
    if got is None:
        cands = [c for c in flat(co, []) if c.co_name != '<module>']
        if len(cands) == 1:
            got = cands[0]
        else:
            print('候选里找不到', qual, '（可选：', [c.co_qualname for c in cands], '）')
            return 1
    want = None
    for c in flat(load_pyc(mod), []):
        if c.co_qualname == qual or c.co_qualname.endswith('.' + qual):
            want = c
    if want is None:
        print('原版里找不到', qual)
        return 1
    a, b = seq(want), seq(got)
    print(f'{qual}: 原版 {len(a)} 条, 候选 {len(b)} 条')
    if a == b:
        print('  ✓ 指令序列完全一致')
    else:
        import difflib
        wa = [f'{o} {x!r}' for o, x in a]
        wb = [f'{o} {x!r}' for o, x in b]
        n = 0
        for line in difflib.unified_diff(wa, wb, 'orig', 'cand', lineterm='', n=4):
            print('  ' + line)
            n += 1
            if n > 120:
                print('  ...(截断)')
                break
    for attr in ('co_argcount', 'co_kwonlyargcount', 'co_posonlyargcount',
                 'co_flags', 'co_names', 'co_varnames', 'co_freevars', 'co_cellvars'):
        w, g = getattr(want, attr), getattr(got, attr)
        if w != g:
            print(f'  ! {attr}: {w} vs {g}')
    print('  EXC orig:', dis._parse_exception_table(want))
    print('  EXC cand:', dis._parse_exception_table(got))
    return 0


if __name__ == '__main__':
    sys.exit(main())
