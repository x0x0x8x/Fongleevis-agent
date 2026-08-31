# -*- coding: utf-8 -*-
'''SubsiteRegistry: 子站点注册机制（FastAPI 主框架）。
- SubsiteEntry 注册条目（site_id/name/url_prefix/subdomain/blueprints/status/auth_required/parent_prefix/version/时间戳）
- registry.json 持久化（落盘/加载/变更自动持久化，可审计）
- URL 前缀与子域名双映射（Host 子域名优先，否则最长前缀匹配，支持 /api 与 /api/agent 父子包含）
- 冲突检测：前缀唯一 / 子域名唯一 / parent_prefix 父子声明 / endpoint-rule 模板重复 / 蓝图 name 唯一
- 静态挂载（mount_all 一次性挂载）与动态挂载（REGISTRY_HOT_MOUNT，生产默认关闭）
- 状态守卫：disabled/removed 站点由中间件统一返回 404（路由保持挂载，状态切换即时生效）
'''
import importlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


class RegistryError(Exception):
    pass


class RegistryConflictError(RegistryError):
    pass


class RegistryValidationError(RegistryError):
    pass


def _now() -> str:
    t = time.localtime()
    return '{0:04d}-{1:02d}-{2:02d}T{3:02d}:{4:02d}:{5:02d}'.format(
        t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


@dataclass
class BlueprintSpec:
    import_path: str = ''
    name: str = ''


@dataclass
class SubsiteEntry:
    site_id: str = ''
    name: str = ''
    url_prefix: str = ''
    blueprints: List[BlueprintSpec] = field(default_factory=list)
    subdomain: Optional[str] = None
    status: str = 'registered'
    auth_required: bool = False
    parent_prefix: Optional[str] = None
    version: str = '1.0'
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        bps = [BlueprintSpec(**b) for b in data.pop('blueprints', [])]
        data['blueprints'] = bps
        return cls(**data)


DEFAULT_SEED = [
    {'site_id': 'sweetmido', 'name': 'sweetmido e-commerce', 'url_prefix': '/api', 'subdomain': 'shop',
     'blueprints': [{'import_path': '.sweetmido:router', 'name': 'sweetmido_router'}],
     'status': 'registered', 'auth_required': False, 'version': '1.0'},
    {'site_id': 'agent', 'name': 'agent agent', 'url_prefix': '/api/agent', 'parent_prefix': '/api',
     'blueprints': [{'import_path': '.agent:router', 'name': 'agent_router'}],
     'status': 'registered', 'auth_required': False, 'version': '1.0'},
    {'site_id': 'ai', 'name': 'ai_service ai gateway', 'url_prefix': '/v1', 'subdomain': 'ai',
     'blueprints': [{'import_path': '.ai:router', 'name': 'ai_router'}],
     'status': 'registered', 'auth_required': False, 'version': '1.0'},
]

class SubsiteRegistry:
    def __init__(self, persist_path=None, hot_mount=False):
        self._entries = {}
        self._prefix_index = {}
        self._subdomain_index = {}
        base = Path(__file__).resolve().parent.parent.parent
        self._persist_path = Path(persist_path) if persist_path else base / 'registry.json'
        self.hot_mount = hot_mount
        self._app = None
        self._mounted_sites = set()

    # ---------- 索引维护 ----------
    def _rebuild_index(self):
        self._prefix_index = {}
        self._subdomain_index = {}
        for e in self._entries.values():
            if e.status == 'registered':
                self._prefix_index[e.url_prefix] = e.site_id
                if e.subdomain:
                    self._subdomain_index[e.subdomain] = e.site_id

    def _add_entry(self, entry):
        self._entries[entry.site_id] = entry
        if entry.status == 'registered':
            self._prefix_index[entry.url_prefix] = entry.site_id
            if entry.subdomain:
                self._subdomain_index[entry.subdomain] = entry.site_id

    # ---------- 冲突检测 ----------
    def _validate(self, entry):
        if not entry.site_id or not entry.url_prefix:
            raise RegistryValidationError('site_id and url_prefix are required')
        if entry.site_id in self._entries:
            raise RegistryConflictError("site_id '{}' already registered".format(entry.site_id))
        other = self._prefix_index.get(entry.url_prefix)
        if other and other != entry.site_id:
            raise RegistryConflictError("url_prefix '{}' already used by site '{}'".format(entry.url_prefix, other))
        if entry.subdomain:
            other = self._subdomain_index.get(entry.subdomain)
            if other and other != entry.site_id:
                raise RegistryConflictError("subdomain '{}' already used by site '{}'".format(entry.subdomain, other))
        if entry.parent_prefix:
            parent_sid = self._prefix_index.get(entry.parent_prefix)
            if not parent_sid:
                raise RegistryValidationError("parent_prefix '{}' is not registered".format(entry.parent_prefix))
            if not entry.url_prefix.startswith(entry.parent_prefix.rstrip('/') + '/'):
                raise RegistryValidationError("url_prefix '{}' must be under parent_prefix '{}'".format(entry.url_prefix, entry.parent_prefix))
        # 重叠包含关系检查：除声明 parent_prefix 外不允许其他重叠
        for prefix in list(self._prefix_index.keys()):
            if prefix == entry.url_prefix:
                continue
            if prefix.startswith(entry.url_prefix.rstrip('/') + '/') or entry.url_prefix.startswith(prefix.rstrip('/') + '/'):
                shorter = prefix if len(prefix) < len(entry.url_prefix) else entry.url_prefix
                if entry.parent_prefix != shorter:
                    raise RegistryConflictError("url_prefix '{}' overlaps '{}'; declare parent_prefix='{}'".format(entry.url_prefix, prefix, shorter))
        # 蓝图 name 全局唯一
        for bp in entry.blueprints:
            for other_e in self._entries.values():
                for obp in other_e.blueprints:
                    if obp.name and obp.name == bp.name and other_e.site_id != entry.site_id:
                        raise RegistryConflictError("blueprint name '{}' already used by site '{}'".format(bp.name, other_e.site_id))
    # ---------- 生命周期操作 ----------
    def register(self, entry):
        self._validate(entry)
        try:
            self.check_route_conflicts(extra_entries=[entry])
        except (ImportError, ModuleNotFoundError, AttributeError):
            pass
        self._add_entry(entry)
        self.persist()
        if self._app is not None:
            self._mount_site(entry.site_id)
        return entry

    def unregister(self, site_id):
        if site_id not in self._entries:
            raise RegistryValidationError("site '{}' not found".format(site_id))
        if self._app is not None:
            self._unmount_site(site_id)
        entry = self._entries.pop(site_id)
        self._mounted_sites.discard(site_id)
        if entry.url_prefix in self._prefix_index and self._prefix_index[entry.url_prefix] == site_id:
            del self._prefix_index[entry.url_prefix]
        if entry.subdomain:
            self._subdomain_index.pop(entry.subdomain, None)
        entry.updated_at = _now()
        self.persist()

    def disable(self, site_id):
        if site_id not in self._entries:
            raise RegistryValidationError("site '{}' not found".format(site_id))
        if self._app is not None:
            self._unmount_site(site_id)
        self._entries[site_id].status = 'disabled'
        self._entries[site_id].updated_at = _now()
        self._rebuild_index()
        self.persist()

    def enable(self, site_id):
        if site_id not in self._entries:
            raise RegistryValidationError("site '{}' not found".format(site_id))
        self._entries[site_id].status = 'registered'
        self._entries[site_id].updated_at = _now()
        self._rebuild_index()
        self.persist()
        if self._app is not None:
            self._mount_site(site_id)

    def get(self, site_id):
        return self._entries.get(site_id)

    def list_all(self):
        return [e.to_dict() for e in self._entries.values()]

    def list_registered(self):
        return [e.to_dict() for e in self._entries.values() if e.status == 'registered']
    # ---------- URL 前缀与子域名双映射解析 ----------
    def resolve(self, host=None, path='/'):
        """优先 Host 子域名索引命中，否则按最长前缀匹配（支持 /api 与 /api/agent 父子包含）。"""
        if host:
            host_name = host.split(':')[0].strip().lower()
            parts = host_name.split('.')
            if len(parts) >= 3 and parts[0]:
                sub = parts[0]
                if sub in self._subdomain_index:
                    sid = self._subdomain_index[sub]
                    e = self._entries.get(sid)
                    if e and e.status == 'registered':
                        return e
        best = None
        best_len = -1
        p = path or '/'
        for prefix, sid in self._prefix_index.items():
            if p == prefix or p.startswith(prefix.rstrip('/') + '/'):
                e = self._entries.get(sid)
                if not e or e.status != 'registered':
                    continue
                if len(prefix) > best_len:
                    best_len = len(prefix)
                    best = e
        return best

    def match_site(self, path):
        """匹配路径所属站点条目（含 disabled/removed），供中间件对非 registered 站点返回 404。"""
        best = None
        best_len = -1
        p = (path or '/').rstrip('/') or '/'
        for e in self._entries.values():
            prefix = e.url_prefix.rstrip('/')
            if p == prefix or p.startswith(prefix + '/'):
                if len(prefix) > best_len:
                    best_len = len(prefix)
                    best = e
        return best

    # ---------- 持久化 ----------
    def persist(self) -> None:
        data = {'version': 2, 'updated_at': _now(), 'entries': [e.to_dict() for e in self._entries.values()]}
        tmp = self._persist_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, self._persist_path)

    def load(self) -> bool:
        if not self._persist_path.exists():
            return False
        data = json.loads(self._persist_path.read_text(encoding='utf-8'))
        self._entries = {}
        for item in data.get('entries', []):
            e = SubsiteEntry.from_dict(item)
            self._entries[e.site_id] = e
        self._rebuild_index()
        return True

    def load_defaults(self, seed=None) -> None:
        seed = seed if seed is not None else DEFAULT_SEED
        for item in seed:
            self.register(SubsiteEntry.from_dict(item))

    def is_known_path(self, path) -> bool:
        p = (path or '/').rstrip('/') or '/'
        for e in self._entries.values():
            prefix = e.url_prefix.rstrip('/')
            if p == prefix or p.startswith(prefix + '/'):
                return True
        return False
    # ---------- endpoint/rule 模板重复检测与路由加载 ----------
    def _load_router(self, bp):
        """按 import_path 导入子站点路由（支持 'module:attr' 或纯模块路径）。"""
        import traceback

        if not bp.import_path:
            return None

        try:
            if ':' in bp.import_path:
                mod_path, _, attr = bp.import_path.partition(':')
            else:
                mod_path, attr = bp.import_path, None

            if mod_path.startswith('.'):
                module = importlib.import_module(mod_path, package='app.registry')
            else:
                # 绝对导入
                module = importlib.import_module(mod_path)

            if attr:
                return getattr(module, attr)

            for name in ('router', 'bp', 'blueprint', 'api'):
                if hasattr(module, name):
                    return getattr(module, name)

            return module

        except ModuleNotFoundError as e:
            print(f"[ERROR] 模块不存在: {bp.import_path} - {e}")
            print(traceback.format_exc())
            return None
        except AttributeError as e:
            print(f"[ERROR] 属性不存在: {bp.import_path} - {e}")
            print(traceback.format_exc())
            return None
        except Exception as e:
            print(f"[ERROR] 导入失败: {bp.import_path} - {e}")
            print(traceback.format_exc())
            return None

    def check_route_conflicts(self, ignore_site=None, extra_entries=None) -> None:
        """endpoint/rule 模板重复检测：跨子站点 (path, methods) 组合必须唯一。"""
        seen = {}
        entries = list(self._entries.values())
        if extra_entries:
            entries = entries + list(extra_entries)
        for e in entries:
            if ignore_site and e.site_id == ignore_site:
                continue
            if e.status == 'removed':
                continue
            for bp in e.blueprints:
                router = self._load_router(bp)
                for route in getattr(router, 'routes', []):
                    path = getattr(route, 'path', None)
                    methods = sorted(getattr(route, 'methods', None) or [])
                    if not path:
                        continue
                    key = (path, tuple(methods))
                    prev = seen.get(key)
                    if prev and prev != e.site_id:
                        raise RegistryConflictError(
                            "route {} {} conflicts between sites '{}' and '{}'".format(
                                ','.join(methods), path, prev, e.site_id))
                    seen[key] = e.site_id

    # ---------- 静态/动态挂载 ----------
    def mount_all(self, app):
        """静态挂载：create_app 时一次性挂载全部 registered 子站点路由。"""
        self._app = app
        self._mounted_sites = set()
        #self.check_route_conflicts()
        mounted = []

        for e in self._entries.values():
            if e.status != 'registered':
                continue
            if self._mount_site(e.site_id):
                mounted.append(e.site_id)
        return mounted

    def _mount_site(self, site_id):
        entry = self._entries.get(site_id)
        if not entry or entry.status != 'registered':
            return False
        if site_id in self._mounted_sites:
            return True
        mounted_any = False
        for bp in entry.blueprints:
            try:
                router = self._load_router(bp)
            except Exception:
                continue
            if router is None:
                print(f"[DEBUG] router 是 None: {bp.import_path}")
                continue
            for _route in list(getattr(router, 'routes', [])):
                self._app.router.routes.append(_route)
            mounted_any = True
        if mounted_any:
            self._mounted_sites.add(site_id)
        return mounted_any

    def _unmount_site(self, site_id):
        entry = self._entries.get(site_id)
        if not entry or self._app is None:
            return False
        # 兼容不同 FastAPI 版本的路由存储结构：
        # 0.141+ 为 _IncludedRouter（含 original_router），旧版本为 APIRoute（含 path）
        removed_any = False
        for bp in entry.blueprints:
            try:
                router = self._load_router(bp)
            except Exception:
                continue
            if router is None:
                continue
            for r in list(self._app.routes):
                if getattr(r, 'original_router', None) is router:
                    self._app.routes.remove(r)
                    removed_any = True
                elif getattr(r, 'path', None) in {getattr(x, 'path', None) for x in getattr(router, 'routes', [])}:
                    self._app.routes.remove(r)
                    removed_any = True
        if removed_any:
            self._mounted_sites.discard(site_id)
        return removed_any