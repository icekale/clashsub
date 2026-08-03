# Unraid 与 Lucky 部署说明

Lucky 只负责域名、证书、HTTPS 终止和反向代理。本应用不会登录 Lucky、调用其管理接口或自动创建规则。

## 1. 持久化目录

把 `/data` 放在 Unraid 本机的 appdata/cache 池或 Docker 命名卷中。SQLite 使用 WAL，数据库、WAL、缓存和日志必须位于同一块可靠的本地文件系统。不要使用 NFS，也不要使用锁和原子替换语义不可靠的 FUSE 挂载。

使用仓库默认的 bind mount 时，先让目录属于容器用户：

```sh
mkdir -p data
chown 10001:10001 data
chmod 700 data
```

Compose 的 Secret 同样是宿主机文件挂载，也必须让容器用户可读，同时保持仅属主可读写：

```sh
chown 10001:10001 secrets/upstream_url secrets/admin_username secrets/admin_password
chmod 600 secrets/upstream_url secrets/admin_username secrets/admin_password
```

新增版本还需要单独的加密主密钥：

```sh
umask 077
openssl rand -base64 32 > secrets/encryption_key
chown 10001:10001 secrets/encryption_key
chmod 600 secrets/encryption_key
export ENCRYPTION_KEY_SECRET_FILE=./secrets/encryption_key
```

该文件必须单独备份。主密钥缺失不会使已有 Token 哈希链接或最后有效缓存失效，但会关闭 WebUI 链接恢复和机场凭据更新。

上述三个文件即可运行 fallback-only 模式，机场文件无需存在。启用协议源时，再
为 `airport_email` 和 `airport_password` 设置相同的属主和权限。不要删除或重建
现有 `data` 目录，直接升级会保留 SQLite、缓存、分享记录和运行设置。

也可以把 Compose 改为命名卷：

```yaml
services:
  clashsub:
    volumes:
      - clashsub-data:/data

volumes:
  clashsub-data:
```

## 2. 机场协议源

Compose 中的 `AIRPORT_API_BASE_URL` 默认留空，即协议源关闭。启用时将它设置为
官方客户端 API，并把 `AIRPORT_EMAIL_SECRET_FILE` 与
`AIRPORT_PASSWORD_SECRET_FILE` 设置为两个宿主机 Secret 路径；它不是机场网页 API。
将其设置为机场官方客户端 API 的 V2Board 地址。订阅按需拉取：客户端请求分享链接且缓存超过"按需刷新间隔"（默认 60 分钟，可在设置页调整）时才执行一次机场登录与下载；另保留每日一次的兜底刷新。刷新流程为：

1. 使用 `BBGen2UA` 登录官方 API；
2. 原样携带 `auth_data` 获取临时 `subscribe_url`；
3. 原样保留其查询串并追加 `flag=clash`；
4. 继续使用 `BBGen2UA` 下载、校验和原子发布缓存。

协议源失败时才尝试 `upstream_url`，两者均失败时继续提供最后有效缓存。
`auth_data`、机场凭据和完整订阅 URL 不会写入持久化数据。如果 WebUI 返回
`captcha_required`，说明机场当前要求交互式验证；服务不绕过该验证，会按上述顺序
回退。

升级完成后，先在 WebUI 的“设置 -> 机场订阅源”执行连接测试，再在概览页
手动刷新。概览应显示来源为 `V2Board 协议`，节点数量应与官方客户端一致。

## 3. 可信代理

只有来自 `TRUSTED_PROXY_CIDRS` 的直连来源，应用才会信任 `X-Forwarded-For` 和 `X-Forwarded-Proto`。把 Compose 中的空值改为 Lucky 所在 Docker 网络的精确 CIDR；不要填写整个家庭网络以外的宽泛网段，更不要填写 `0.0.0.0/0`。

Lucky 必须传递：

- `X-Forwarded-For`：真实客户端地址；
- `X-Forwarded-Proto`：外部协议，公网应为 `https`；
- 常规 `Host` 头可照常传递，但应用不会用它生成分享链接。

修改 Compose 后重新创建容器：

```sh
docker compose up -d
```

如果 Unraid 的 DNS 由 OpenClash Fake-IP 接管，订阅域名可能解析到
`198.18.0.0/15`。此时可在 Compose 中设置：

```yaml
DOWNLOAD_ALLOWED_CIDRS: "198.18.0.0/15"
```

该配置默认必须为空；只有确认 DNS 使用 Fake-IP 时才允许这个精确网段。下载器仍会拒绝
未显式允许的 RFC1918、回环和链路本地地址。

## 4. Lucky 反向代理

1. 先为准备好的二级域名签发并启用 HTTPS 证书。
2. 新建该域名的反向代理，上游指向 `http://UNRAID_IP:18080`。
3. 代理整个主机，而不是只代理一个前缀；必须同时覆盖 `/app`、`/api`、`/raw`、`/clash`、`/surge`、`/loon` 和 `/smart`。
4. 确认上述转发头正确，并把 Lucky 的精确来源网络加入 `TRUSTED_PROXY_CIDRS`。
5. 为这个虚拟主机关闭 access log，或配置严格脱敏。分享 bearer token 位于 `/raw/<token>`、`/clash/<token>`、`/surge/<token>`、`/loon/<token>` 与 `/smart/<token>` 路径，默认访问日志会泄露它。
6. 从外部网络确认 HTTPS 正常后，登录 WebUI，设置“公网 Base URL”为该 HTTPS origin，再切换到公网模式。

不要把 Lucky 管理账号或密码放入本项目。即使两个服务由部署者设置为相同凭据，它们仍是两个独立账号，任何一侧修改后都不会自动同步。

## 5. 关闭公网

WebUI 中关闭公网模式只会让应用恢复 LAN 来源限制，不会删除 Lucky 的域名、证书或代理规则。需要同时在 Lucky 中停用该规则，避免把安全边界依赖在单一开关上。

## 6. 验收清单

- `docker compose ps` 显示容器 healthy；
- `subconverter` 只在 Compose 内部网络监听 `25500`，宿主机不发布该端口；
- 局域网可访问 `/app/`，LAN 模式下公网请求不可用；
- Lucky 对完整主机转发，HTTPS 生效；
- WebUI 公网模式启用后，外部网络可登录并使用有效分享链接；
- Lucky access log 不记录完整分享路径；
- `/data` 位于可靠本地存储，容器用户可写；
- `TRUSTED_PROXY_CIDRS` 只包含实际 Lucky 来源网络。
- WebUI 机场协议状态完整，手动刷新来源为 `protocol` 且节点数为 49；
- 超过按需刷新间隔后再请求分享链接，事件日志出现新的 `refresh succeeded`，仍为 49 节点；
- 原始分享以及 Clash、Surge、Loon 和智能分享均保持 ClashSub 自身域名，由本地 `subconverter` 转换；
- 容器日志和 `/data` 中不包含机场密码、`auth_data` 或完整订阅 URL。
