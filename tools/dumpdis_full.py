import marshal, dis, sys

def load(p):
    f = open(p, 'rb'); f.read(16); return marshal.load(f)

def flat(c, out):
    out.append(c)
    for k in c.co_consts:
        if hasattr(k, 'co_name'):
            flat(k, out)
    return out

mod = sys.argv[1].replace('.', '/')
out = sys.argv[2]
co = load('recover/app.exe_extracted/PYZ.pyz_extracted/%s.pyc' % mod)
A = flat(co, [])
with open(out, 'w') as f:
    for c in A:
        f.write('# %s L%d args=%d/%d/%d flags=0x%x\n' % (c.co_qualname, c.co_firstlineno, c.co_posonlyargcount, c.co_argcount, c.co_kwonlyargcount, c.co_flags))
        f.write('  varnames=%s freevars=%s cellvars=%s\n' % (c.co_varnames, c.co_freevars, c.co_cellvars))
        f.write('  names=%s\n' % str(c.co_names))
        cs = []
        for k in c.co_consts:
            cs.append('<code %s>' % k.co_qualname if hasattr(k, 'co_name') else repr(k))
        f.write('  consts=%s\n' % cs)
        for i in dis.get_instructions(c):
            if i.opname == 'EXTENDED_ARG':
                continue
            av = i.argval
            if hasattr(av, 'co_name'):
                av = '<code %s>' % av.co_qualname
            ln = i.positions.lineno if i.positions else ''
            f.write('  %4d %-32s %s L%s\n' % (i.offset, i.opname, repr(av), ln))
        f.write('\n')
print("wrote", out, len(A), "objects")
