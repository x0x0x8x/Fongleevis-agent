import os,py_compile,io,re
out=[]
OUTABS=r'C:/my/workspace/Fongleevis-agent/_final_verify_out.txt'
# I1: verify line 2993 content
p='fastapi_framework/app/agent/api/session/goal_orchestrator.py'
lines=io.open(p,encoding='utf-8').read().splitlines()
line2993=lines[2992]
out.append('I1_LINE2993: '+line2993.strip())
if "new_history['content']" in line2993 and 'new_history["content"]' not in line2993:
    out.append('I1_PASS')
else:
    out.append('I1_FAIL')
# I2: scan all f-string nested same-quote
base='fastapi_framework/app/agent'
pat_q=re.compile(r'f"((?:[^"\]|\.)*)\{([^}]*)\}')  
pat_s=re.compile(r"f'((?:[^'\]|\.)*)\{([^}]*)\}")
issues=[]
for r,d,fs in os.walk(base):
    for f in fs:
        if f.endswith('.py'):
            fp=os.path.join(r,f)
            try:
                txt=io.open(fp,encoding='utf-8').read()
            except Exception:
                continue
            for i,line in enumerate(txt.splitlines(),1):
                for m in pat_q.finditer(line):
                    if chr(34) in m.group(2):
                        issues.append((fp,i,'dq',line.strip()))
                for m in pat_s.finditer(line):
                    if chr(39) in m.group(2):
                        issues.append((fp,i,'sq',line.strip()))
out.append('I2_ISSUE_COUNT='+str(len(issues)))
for fp,i,k,ln in issues:
    out.append('I2_ISSUE:'+fp+':'+str(i)+'['+k+']:'+ln)
if len(issues)==0:
    out.append('I2_PASS')
else:
    out.append('I2_FAIL')
# I3: compile whole agent tree
bad=[]
for r,d,fs in os.walk(base):
    for f in fs:
        if f.endswith('.py'):
            fp=os.path.join(r,f)
            try:
                py_compile.compile(fp,doraise=True)
            except Exception as e:
                bad.append((fp,str(e)))
out.append('I3_BAD_COUNT='+str(len(bad)))
for fp,e in bad:
    out.append('ERROR:'+fp+'::'+e)
if len(bad)==0:
    out.append('I3_PASS')
else:
    out.append('I3_FAIL')
io.open(OUTABS,'w',encoding='utf-8').write('\n'.join(out))
print('DONE')
