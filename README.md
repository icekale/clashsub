# Clash Subscription Cache

面向 OpenClash/Mihomo 的订阅缓存与分享服务。它按需拉取并验证上游订阅，
失败时继续提供最后一次有效缓存；支持带到期、撤销和轮换能力的独立分享链接，
可输出 Raw / Clash / Surge / Loon 格式，并提供与 OpenClash 的自动联动
（刷新推送、节点健康检查、大面积故障自动重拉）。管理 WebUI 默认仅局域网可访问。

## 功能

- **订阅缓存**：V2Board 协议源（登录官方客户端 API 获取临时订阅地址，使用客户端
  User-Agent 下载）与静态 URL 备用源；两者都失败时继续提供最后有效缓存。
- **按需刷新**：客户端请求且缓存超过间隔时才刷新（默认 60 分钟，可配置），另有每日兜底。
- **分享链接**：每条记录可独立设置有效期，支持撤销、轮换、续期，密钥只在创建时完整显示。
- **多格式输出**：`/raw`、`/clash`、`/surge`、`/loon` 以及仅含健康节点的 `/clash-ha`。
- **OpenClash 联动**：上游刷新成功后自动推送 provider 重拉；定期探测节点连通性；
  在线比例低于阈值时自动重新拉取上游缓存并再次推送。
- **安全默认**：Secret 文件挂载、只读容器、最小权限、脱敏日志、公网模式默认关闭。

## 部署（Docker Compose）

### 1. 准备 Secret 文件

在项目目录下创建 `secrets/`（已被 Git 忽略），每个文件只放一个值：

| 文件 | 必填 | 说明 |
| --- | --- | --- |
| `admin_username` | 是 | 首次启动时创建的管理员用户名 |
| `admin_password` | 是 | 首次启动时创建的管理员密码 |
| `upstream_url` | 否 | 手工订阅 URL 备用源；不需要时留空文件 |
| `encryption_key` | 是 | AES-256 主密钥，用于加密存储机场凭据与恢复分享链接 |
| `airport_email` / `airport_password` | 否 | V2Board 协议源登录凭据，必须成对配置 |

```bash
install -m 700 -d secrets
umask 077
read -r -p '管理员用户名: ' admin_username
read -r -s -p '管理员密码: ' admin_password; printf '\n'
read -r -s -p '备用订阅 URL（可留空）: ' upstream_url; printf '\n'
printf '%s' "$admin_username" > secrets/admin_username
printf '%s' "$admin_password" > secrets/admin_password
printf '%s' "$upstream_url" > secrets/upstream_url
openssl rand -base64 32 > secrets/encryption_key
unset admin_username admin_password upstream_url
sudo chown 10001:10001 secrets/*
chmod 600 secrets/*
```

启用协议源时再创建两个机场凭据文件，并确保 `AIRPORT_API_BASE_URL` 指向
机场官方客户端 API 的 V2Board 地址。

### 2. 准备数据目录

容器以 UID/GID `10001:10001` 运行：

```bash
mkdir -p data
sudo chown 10001:10001 data
chmod 700 data
```

`/data` 必须位于可靠的本地文件系统（SQLite 使用 WAL），不要放在 NFS 或锁语义
不可靠的挂载上。

### 3. 配置环境变量

可通过 Compose 文件、`.env` 或 shell 导出配置，常用变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATA_DIR` | `/data` | 持久化目录 |
| `TZ` | `Asia/Shanghai` | 时区 |
| `AIRPORT_API_BASE_URL` | 空 | V2Board 协议源 API 基地址；留空关闭协议源 |
| `AIRPORT_EMAIL_SECRET_FILE` | - | 机场登录邮箱 Secret 路径 |
| `AIRPORT_PASSWORD_SECRET_FILE` | - | 机场登录密码 Secret 路径 |
| `UPSTREAM_URL_SECRET_FILE` | `./secrets/upstream_url` | 备用订阅 URL Secret 路径 |
| `ENCRYPTION_KEY_SECRET_FILE` | `./secrets/encryption_key` | 主密钥 Secret 路径 |
| `TRUSTED_PROXY_CIDRS` | 空 | 反向代理的精确 CIDR，用于信任转发头 |
| `DOWNLOAD_ALLOWED_CIDRS` | 空 | OpenClash Fake-IP 环境设为 `198.18.0.0/15` |
| `CONVERTER_BASE_URL` | `http://127.0.0.1:25500` | 转换服务地址（与主应用同容器） |
| `CONVERTER_SOURCE_BASE_URL` | `http://127.0.0.1:8080` | 转换服务回源地址 |

### 4. 构建并启动

```bash
docker compose up -d --build
```

已发布镜像：`ghcr.io/icekale/clashsub:0.1.0`（`linux/amd64`）。

打开 `http://NAS_IP:18080/app/` 登录，在“设置”中把“局域网 Base URL”设为客户端
实际可访问的地址（例如 `http://NAS_IP:18080`）。

## Unraid 与公网发布

Unraid 部署要点（appdata 目录、权限、命名卷）以及通过 Lucky 配置域名、HTTPS 和
反向代理的方法见 [Unraid 与 Lucky 部署说明](docs/unraid-lucky.md)。

公网模式只应通过 HTTPS 发布；开启前先阅读该文档，并确保 Lucky access log 对
分享链接路径脱敏。

## 使用

### 分享链接

在 WebUI“分享链接”页面新建记录并复制链接，支持：

- `/raw/<token>`：原始订阅字节（OpenClash 直接使用）；
- `/clash/<token>`：包含 `proxy-providers` 的 Mihomo/OpenClash 配置；
- `/surge/<token>`、`/loon/<token>`：对应格式的转换订阅；
- `/clash-ha/<token>`：仅包含最近健康检查通过的节点的 Clash 配置。

转换订阅由镜像内置的 SubConverter-Extended 生成，与主应用运行在同一个容器内
（仅监听回环 `127.0.0.1:25500`），不需要外部在线服务。

### OpenClash 联动

在“设置 -> OpenClash 联动与节点健康”中：

1. 填写 OpenClash API 地址（默认 `http://192.168.1.1:9090`）、Provider 名称并保存
   API 密钥；
2. 开启自动推送：每次上游刷新成功后立即通知 OpenClash 重新拉取 provider；
3. 开启节点健康检查：按间隔探测节点连通性（TCP/TLS 握手）；
4. 开启“不可用时自动刷新缓存”：在线比例低于阈值时自动重新拉取上游缓存并再次推送，
   冷却时间防止频繁请求机场。

### 备份

`scripts/backup-and-verify.sh` 会备份 SQLite 数据库与 Compose 文件并执行健康检查，
可放入 Unraid User Scripts 定时运行。

## 开发验证

```bash
uv run pytest -q
npm --prefix frontend test
npm --prefix frontend run build
docker compose config --quiet
docker build -t clashsub:test .
```

## 安全提醒

- 不要提交 `secrets/`、`data/`、`.env` 或真实订阅 URL；
- 每位朋友使用独立分享记录，遗失时轮换，不要共用管理员凭据；
- `TRUSTED_PROXY_CIDRS` 只填写反向代理的精确 CIDR，不要填写 `0.0.0.0/0`；
- 协议源出现交互式验证（`captcha_required`）时，服务会回退到备用源或最后有效
  缓存，不会绕过验证；
- 管理员账号首次初始化后可在 WebUI 修改，修改会撤销全部旧会话。

## License

MIT，第三方归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
