# -*- coding: utf-8 -*-  
# Secure resource control core: secure_root + two-class whitelist registry + access validation. 
# secure_root/ 
#   framework<res_name>  class1: main-framework URL-reachable resources (must be registered) 
#   subsites<site_id>    class2: subsite own resources (must be registered) 
#   resource_manifest.json  persistent registry manifest 
# Rules: unregistered denied; sensitive paths (.well-known/keys/logs/backups) unreachable. 
import json 
import os 
import time 
from dataclasses import dataclass, field, asdict 
from pathlib import Path 
from typing import List 
from fastapi import APIRouter, HTTPException, Request 
from fastapi.responses import FileResponse 
class ResourceError(Exception): 
    pass 
class ResourceValidationError(ResourceError): 
    pass 
class ResourceConflictError(ResourceError): 
    pass 
 
 
# Sensitive path fragments: denied regardless of existence 
SENSITIVE_FRAGMENTS = ( 
    '.well-known', 'wx_v3', 'log', 'debug_logs', 'executor_logs', 
    'tmp', 'backup', 'bak', '.git', '.env', 'secret', 'private', 
    '__pycache__', 'node_modules', 'site-packages', 'venv', '.venv', 
) 
# Sensitive file suffixes: denied regardless of existence 
SENSITIVE_SUFFIXES = ( 
    '.pem', '.p12', '.pfx', '.key', '.log', '.sqlite3', '.sqlite', '.db', 
    '.env', '.pyc', '.pyo', '.exe', '.dll', '.so', '.dylib', 
    '.bak', '.tmp', '.temp', '.old', '.orig', '.swp', '.7z', '.zip', '.rar', 
) 
 
 
def _now(): 
    t = time.localtime() 
    return '{0:04d}-{1:02d}-{2:02d}T{3:02d}:{4:02d}:{5:02d}'.format( 
        t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec) 
 
 
@dataclass 
class ResourceEntry: 
    scope: str = 'subsite' 
    name: str = '' 
    disk_dir: str = '' 
    url_prefix: str = '' 
    allow_suffixes: List[str] = field(default_factory=list) 
    auth_required: bool = False 
    status: str = 'registered' 
    created_at: str = field(default_factory=_now) 
    updated_at: str = field(default_factory=_now) 
 
    def to_dict(self): 
        return asdict(self) 
 
    @classmethod 
    def from_dict(cls, data): 
        data = dict(data) 
        return cls(**data) 
 
 
class ResourceManager: 
    def __init__(self, secure_root, manifest_path=None): 
        self.secure_root = Path(secure_root).resolve() 
        self.framework_dir = self.secure_root / 'framework' 
        self.subsites_dir = self.secure_root / 'subsites' 
        self._entries = {} 
        self._manifest_path = Path(manifest_path) if manifest_path else self.secure_root / 'resource_manifest.json' 
 
    def init_dirs(self): 
        self.framework_dir.mkdir(parents=True, exist_ok=True) 
        self.subsites_dir.mkdir(parents=True, exist_ok=True) 
 
    def subsite_dir(self, site_id): 
        return self.subsites_dir / site_id 
 
    def _validate(self, entry): 
        if entry.scope not in ('framework', 'subsite'): 
            raise ResourceValidationError('scope must be framework or subsite') 
        if not entry.name: 
            raise ResourceValidationError('name is required') 
        key = (entry.scope, entry.name) 
        if key in self._entries: 
            raise ResourceConflictError('resource already registered') 
        disk = Path(entry.disk_dir).resolve() 
        try: 
            os.path.commonpath([str(disk), str(self.secure_root)]) 
        except ValueError: 
            raise ResourceValidationError('disk_dir must be inside secure_root') 
        if not (str(disk) == str(self.secure_root) or str(disk).startswith(str(self.secure_root) + os.sep)): 
            raise ResourceValidationError('disk_dir must be inside secure_root') 
        entry.disk_dir = str(disk) 
 
    def register(self, entry, create_dir=False): 
        self._validate(entry) 
        if create_dir: 
            Path(entry.disk_dir).mkdir(parents=True, exist_ok=True) 
        self._entries[(entry.scope, entry.name)] = entry 
        self.persist() 
        return entry 
 
    def unregister(self, scope, name): 
        key = (scope, name) 
        if key not in self._entries: 
            raise ResourceValidationError('resource not found') 
        self._entries.pop(key) 
        self.persist() 
 
    def get(self, scope, name): 
        e = self._entries.get((scope, name)) 
        return e if e and e.status == 'registered' else None 
 
    def list_all(self): 
        return [e.to_dict() for e in self._entries.values()] 
 
 
    # ---------- persistence ---------- 
    def persist(self): 
        data = {'version': 1, 'secure_root': str(self.secure_root), 'updated_at': _now(), 'entries': [e.to_dict() for e in self._entries.values()]} 
        tmp = self._manifest_path.with_suffix('.json.tmp') 
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8') 
        os.replace(tmp, self._manifest_path) 
 
    def load(self): 
        if not self._manifest_path.exists(): 
            return False 
        data = json.loads(self._manifest_path.read_text(encoding='utf-8')) 
        self._entries = {} 
        for item in data.get('entries', []): 
            e = ResourceEntry.from_dict(item) 
            self._entries[(e.scope, e.name)] = e 
        return True 
 
 
    # ---------- sensitive hard exclusion (ind4/ind5) ---------- 
    @staticmethod 
    def is_sensitive(relative): 
        lowered = (relative or '').lower() 
        parts = lowered.replace(chr(92), '/').split('/') 
        for frag in SENSITIVE_FRAGMENTS: 
            if frag in parts: 
                return True 
        for suffix in SENSITIVE_SUFFIXES: 
            if lowered.endswith(suffix): 
                return True 
        return False 
 
    # ---------- access validation core (ind2/ind4/ind5) ---------- 
    def resolve(self, scope, name, relative_path): 
        entry = self.get(scope, name) 
        if entry is None: 
            return None 
        rel = (relative_path or '').lstrip('/').replace(chr(92), '/') 
        if not rel: 
            return None 
        if self.is_sensitive(rel): 
            return None 
        if entry.allow_suffixes: 
            low = rel.lower() 
            if not any(low.endswith(s.lower()) for s in entry.allow_suffixes): 
                return None 
        base = Path(entry.disk_dir).resolve() 
        candidate = (base / rel).resolve() 
        try: 
            os.path.commonpath([str(candidate), str(base)]) 
        except ValueError: 
            return None 
        if not (str(candidate) == str(base) or str(candidate).startswith(str(base) + os.sep)): 
            return None 
        if not candidate.is_file(): 
            return None 
        return candidate 
 
    def build_url_prefix(self, scope, name): 
        if scope == 'framework': 
            return '/resources/framework/' + name 
        return '/resources/subsites/' + name 
 
 
def create_resource_router(manager): 
    """Build FastAPI router serving registered resources with whitelist control. 
    URL patterns: 
      /resources/framework/{name}/{path:path}  - class1 main-framework resources 
      /resources/subsites/{name}/{path:path}   - class2 subsite own resources 
    Unregistered / sensitive / traversal / missing - 404 JSON. 
    """ 
    router = APIRouter(prefix="/resources", tags=["resources"]) 
 
    def _serve(scope, name, path): 
        resolved = manager.resolve(scope, name, path) 
        if resolved is None: 
            raise HTTPException(status_code=404, detail="Not Found") 
        return FileResponse(str(resolved)) 
 
    @router.get("/framework/{name}/{path:path}") 
    async def framework_resource(name: str, path: str): 
        return _serve("framework", name, path) 
 
    @router.get("/subsites/{name}/{path:path}") 
    async def subsite_resource(name: str, path: str): 
        return _serve("subsite", name, path) 
 
    return router 
