# SecureFastAPI 安全主框架：运行说明与安全架构说明

## 一、项目结构
- 根目录：C:/my/workspace/website/fastapi_framework
- main.py：uvicorn 启动入口（0.0.0.0:8000，proxy_headers）
- app/__init__.py：应用工厂 create_app()（安全默认配置+中间件+注册机制+资源管控）
- app/core/config.py：SecureDefaultConfig（DEBUG 强制关闭、SECRET_KEY 外置、CORS 白名单、请求限长、敏感路径清单）
- app/core/middleware.py：全局安全中间件（request_id / Host 白名单 / CORS Origin / 敏感路径拦截 / 请求限长 / 鉴权钩子）
- app/core/errors.py：统一 JSON 错误处理（404/405/400/403/413/500，不泄露路径）
- app/core/security.py：安全响应头（nosniff / frame-deny / CSP / HSTS / referrer）
- app/core/static.py：静态资源白名单托管（防路径穿越）
- app/core/resources.py：两类资源管控（framework 与 subsites，登记即放行、未登记 404、敏感硬排除）
- app/registry/registry.py：SubsiteRegistry 子站点注册机制（前缀/子域名双映射、冲突检测、持久化、静态挂载）
- app/registry/admin_api.py：子站点管理 API /api/_internal/subsites（管理员鉴权）
- app/api/：子站点路由包（sweetmido / agent / ai / demo_site / sample）
- registry.json：子站点注册表（持久化）
- secure_root/：主安全目录（framework/ 主框架资源、subsites/ 子站点资源、resource_manifest.json 登记清单）
- .env.example：环境变量示例

## 二、启动方式
1. 安装依赖：pip install -r requirements.txt（fastapi、uvicorn[standard]、pydantic）
2. 配置环境：复制 .env.example 为 .env，设置 SFA_SECRET_KEY（必填，缺失拒绝启动）、SFA_ADMIN_TOKEN（子站点管理令牌）、SFA_ALLOWED_HOSTS（Host 白名单）
3. 启动服务：python main.py（监听 0.0.0.0:8000）
4. 验证：GET http://127.0.0.1:8000/api/health 返回 status ok

## 三、子站点注册流程
- 管理 API：/api/_internal/subsites（需 X-Admin-Token 或 Bearer 匹配 SFA_ADMIN_TOKEN）
- 支持操作：POST 注册、PATCH {site_id} 启停、DELETE {site_id} 注销、GET 列表
- 注册示例（demo 子站点）：site_id=demo、url_prefix=/demo-site、blueprints=app.api.demo_site:router（name=demo_router）
- URL 前缀与子域名双映射：Host 子域名命中优先（如 shop.sweetmido.asia 指向 sweetmido），否则最长前缀匹配（/api/agent 优先于 /api）
- 冲突检测：前缀/子域名/蓝图名全局唯一，父子前缀须显式声明 parent_prefix
- 状态守卫：disabled/removed 站点路由统一返回 404，启停即时生效；静态挂载为生产默认（REGISTRY_HOT_MOUNT 默认关闭）

## 四、URL 安全管控机制
- 显式路由白名单：仅注册表登记的子站点路由可访问，未注册 URL 统一 JSON 404，不泄露路径
- 静态白名单：仅 /static/public 前缀可访问，其余 404
- 敏感路径硬排除：/.well-known、/wx_v3、/log、/debug_logs、/executor_logs、/TMP、/.git、/.env 及 *.pem/*.p12/*.key/*.log/*.db 等一律 404
- 全局安全中间件：request_id → Host 白名单 → CORS Origin 白名单 → 敏感路径拦截 → 请求限长（10MB）→ 鉴权钩子
- 统一 JSON 错误：404/405/400/403/413/429/500 均返回 {code,message,request_id,timestamp}
- 安全响应头：nosniff、X-Frame-Options DENY、CSP、HSTS、Referrer-Policy、Cache-Control no-store

## 五、资源管控机制
- 两类资源统一管理：framework（主框架 URL 可触及资源）与 subsites（子站点自身资源）
- 主安全目录 secure_root/：framework/<res_name> 与 subsites/<site_id>，resource_manifest.json 登记清单
- 登记即放行、未登记 404：仅登记过的资源名可访问
- 路径穿越防护：realpath + os.path.commonpath 双重校验
- 敏感硬排除：.well-known、wx_v3、log、tmp、backup、.git、.env、secret 等路径片段及 .pem/.key/.log/.db/.zip 等后缀一律 404
- demo 子站点资源示例：secure_root/subsites/demo/（welcome.txt、feature.txt），经 /resources/subsites/demo/ 访问
