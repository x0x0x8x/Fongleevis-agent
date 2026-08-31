import os,io,re
base='fastapi_framework/app/agent'
pat_q=re.compile(r'f"((?:[^"\]|\.)*)\{([^}]*)\}')  
pat_s=re.compile(r"f'((?:[^'\]|\.)*)\{([^}]*)\}")
for root,dirs,files in os.walk(base):
    for fn in files:
        if not fn.endswith('.py'):
            continue
        p=os.path.join(root,fn)
        try:
            txt=io.open(p,encoding='utf-8').read()
        except Exception:
            continue
        for i,line in enumerate(txt.splitlines(),1):
            for m in pat_q.finditer(line):
                if chr(34) in m.group(2):
                    print(p+':'+str(i)+' [dq] '+line.strip())
            for m in pat_s.finditer(line):
                if chr(39) in m.group(2):
                    print(p+':'+str(i)+' [sq] '+line.strip())
