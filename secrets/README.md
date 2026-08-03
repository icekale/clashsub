# Secret 文件

在此目录按启用的订阅源创建无扩展名 UTF-8 文本文件，每个文件只放一个值：

- `airport_email` 与 `airport_password`：可选的官方客户端 API 登录凭据，必须同时配置；
- `upstream_url`：可选的手工订阅 URL 备用源；禁用时保留为空文件；
- `admin_username`：首次启动时创建的管理员用户名；
- `admin_password`：首次启动时创建的管理员密码。

管理员文件和 `upstream_url` 文件必需，协议源与手工 URL 至少启用一个；
`upstream_url` 的内容可以为空。fallback-only 模式不需要创建机场文件，Compose
会用空的 `/dev/null` 代替。创建文件后设置权限：

```sh
sudo chown 10001:10001 secrets/upstream_url secrets/admin_username secrets/admin_password
chmod 600 secrets/upstream_url secrets/admin_username secrets/admin_password
```

启用协议源时，还要为两个机场文件执行相同的 `chown`/`chmod`，并设置
`AIRPORT_API_BASE_URL`、`AIRPORT_EMAIL_SECRET_FILE` 和
`AIRPORT_PASSWORD_SECRET_FILE`。后三者只包含 API 地址和宿主机文件路径，不包含
凭据本身。

Compose 会直接挂载宿主机文件，容器以 UID/GID `10001:10001` 运行；仅设置
`root:root 600` 会导致容器无法读取 Secret。

管理员创建后，以 WebUI 中保存的账号为准；以后修改管理员 Secret 不会
覆盖已有管理员。机场凭据和备用 URL 在容器启动时读取，更换后应重新
创建容器。`auth_data` 和实际订阅 URL 只在单次刷新的内存中存在。

不要在问题、日志、截图、Compose 环境变量或 Git 提交中粘贴真实值。
