import marshal, dis, difflib, sys, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_pyc(path):
    with open(path, 'rb') as fh:
        return marshal.loads(fh.read()[16:])


def flat(code, out):
    out.append(code)
    for k in code.co_consts:
        if hasattr(k, 'co_name'):
            flat(k, out)
    return out


def instrs(code):
    r = []
    for i in dis.get_instructions(code):
        if i.opname == 'EXTENDED_ARG':
            continue
        av = i.argval
        if hasattr(av, 'co_name'):
            av = '<code %s>' % av.co_qualname
        r.append(i.opname + ' ' + repr(av))
    return r


def main():
    mod = sys.argv[1].replace('.', os.sep)
    pyc = os.path.join(ROOT, 'recover', 'app.exe_extracted', 'PYZ.pyz_extracted', mod + '.pyc')
    src = os.path.join(ROOT, 'classpoints-py', mod + '.py')
    want = {c.co_qualname: c for c in flat(load_pyc(pyc), [])}
    got = {c.co_qualname: c for c in flat(compile(open(src, encoding='utf-8').read(), src, 'exec', dont_inherit=True), [])}
    only = sys.argv[2] if len(sys.argv) > 2 else None
    for name in sorted(set(want) | set(got)):
        if only and only not in name:
            continue
        if name not in want:
            print('EXTRA', name)
            continue
        if name not in got:
            print('MISSING', name)
            continue
        a, b = instrs(want[name]), instrs(got[name])
        if a != b:
            print('=====', name, len(a), len(b))
            for line in difflib.unified_diff(a, b, 'orig', 'new', lineterm='', n=3):
                print(line)
        elif want[name].co_code != got[name].co_code:
            print('--- semantically same but bytes differ:', name)


main()
