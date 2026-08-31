import os,py_compile
try:
    py_compile.compile('fastapi_framework/app/agent/api/session/goal_orchestrator.py', doraise=True)
    r1='I1_OK line2993'
except Exception as e:
    r1='I1_FAIL '+str(e)
bad=[]
base='fastapi_framework/app/agent'
for r,d,fs in os.walk(base):
    for f in fs:
        if f.endswith('.py'):
            p=os.path.join(r,f)
            try:
                py_compile.compile(p,doraise=True)
            except Exception as e:
                bad.append((p,str(e)))
with open('_verify_out.txt','w',encoding='utf-8') as fh:
    fh.write('== '+r1+' ==\n')
    fh.write('BAD_COUNT='+str(len(bad))+'\n')
    for p,e in bad:
        fh.write('ERROR_FILE:'+p+'\n  '+e+'\n')
    fh.write('SCAN_DONE\n')
print('WROTE')
