# 重建资产清单

2026-11-21 旧服务器数据全失、空库重建的教训：不少关键资产只存在于一台机器的
磁盘或一个数据库里，机器/库没了就没有第二落点。本清单逐项写清每件资产的
落点、丢失时会怎样、怎么恢复。新增「只存一处」的资产时，先在这里登记并补第二落点。

| 资产 | 落点 | 机器/库丢失时会怎样 | 恢复路径 |
| --- | --- | --- | --- |
| CF Origin 证书 + 私钥 | 服务器磁盘 `/opt/aetrix-tls/certs/` | 源站 TLS 无证书可用，Cloudflare 回源失败、全站不可达 | 机器丢了无法找回，只能由用户在 Cloudflare 面板重新签发 Origin 证书，再把证书+私钥落回 `/opt/aetrix-tls/certs/` |
| Cloudflare 侧配置（橙云代理、SSL 模式 Full (Strict)、缓存规则） | 只在 Cloudflare 面板里（无本地副本） | 机器重建不影响 CF 配置；CF 账号侧配置丢失/被改则要逐项按本行记录重设 | 按本行记录在 CF 面板逐项重设：DNS 橙云代理开启、SSL/TLS 加密模式 Full (Strict)、缓存规则按现行配置恢复 |
| SA 池 100 个 json | 服务器磁盘 `/root/sa-accounts`（目录 700、文件 600） | Drive 供给全灭：100 个服务账号凭据无第二落点 | 只能从 Google Cloud 项目逐个重新下载/生成服务账号密钥 json，重新落回该目录并恢复 700/600 权限 |
| rclone.conf | 服务器侧文件 `portal/rclone/rclone.conf` | 挂载（含 paul 盘）全部不可用 | 按现行挂载配置重写 rclone.conf（各 remote 的凭据需重新授权/填写） |
| rcd systemd 单元（`rclone-rcd` / `rclone-rc-paul`）与 5572/5574 防火墙规则 | 系统层：systemd 单元文件 + iptables 规则（iptables-persistent 持久化） | 重装机器后 rcd 不自启、端口不通；paul 单元丢 `--rc-serve` 会全库「来源错误」（文件 404） | 重装机器要重落：重建两个 systemd 单元（paul 的 ExecStart 必须带 `--rc-serve`）、重开 5572/5574 防火墙规则并确认 iptables-persistent 已持久化 |
| `.env` 与 `SETUP_BOOTSTRAP_TOKEN` | portal 目录服务器侧文件 | `.env` 缺失时启动自愈会按 env.example 补注释/默认项，但 `SETUP_BOOTSTRAP_TOKEN` 等部署专属值无默认可补 | 按 env.example 说明重写 `.env`；`SETUP_BOOTSTRAP_TOKEN` 重新生成并同步给部署侧 |
| `docker-compose.override.yml` | portal 目录服务器侧文件 | 项目名、端口收紧、extra_hosts、build 网络（`network: host`）全在这份 override 里，丢了会起错项目名/网络、构建解析失败 | 按本文记录重建：项目名 `aetrix-portal`、端口收紧、extra_hosts、三个服务的 `build: {network: host}` 都要写回 |
| 集成钥匙（TMDB / TG 机器人 / 支付三键 / SMTP / OMDb / TVDB / Frodo） | **双落点**：DB `system_configs`（日常真源，管理面板维护）+ 服务器侧 `secrets.env`（持久副本，不进仓库） | 只丢 DB：启动自愈从 secrets.env 回填（见下）；只丢机器：secrets.env 随机器丢失，需从 DB 导出或向各服务商重新申请 | 空库重建时，后端启动自愈（`backend/config_self_heal.py` 的 `heal_secrets_from_env`）在 DB 该键为空时从 env 回填，已有值不覆盖；Frodo 例外——它不落 DB，代码直接读 env `DOUBAN_FRODO_APIKEY`，secrets.env 写上即生效 |

## 已知问题（Cloudflare 缓存规则）

- `/emby/Videos/` 带后缀的取流地址被 CF 当作可缓存静态资源，整文件回源，
  导致首播 Range 失效（响应带源站没发的 `cache-control: max-age=14400`）。
- 修法：给该路径加一条 Cache Bypass 规则。**待用户提供 CF 操作入口后再处理；
  已向用户提请，勿重复发问。**

## 待用户补的钥匙清单

下列钥匙在 2026-11-21 数据丢失后至今缺失，补齐后写进服务器侧 `secrets.env`
即完成双落点（后端下次启动自愈回填 DB，Frodo 写上即生效）：

- OMDb key → `META_SOURCE_KEYS_OMDB`
- TheTVDB key → `META_SOURCE_KEYS_TVDB`
- 豆瓣 Frodo key → `DOUBAN_FRODO_APIKEY`
- SMTP 全套（host / user / password / from）→ `EMAIL_ENABLED`、
  `EMAIL_SMTP_HOST`、`EMAIL_SMTP_PORT`、`EMAIL_SMTP_USER`、
  `EMAIL_SMTP_PASSWORD`、`EMAIL_FROM_NAME`、`EMAIL_FROM_EMAIL`
  （SMTP 缺失期间找回密码发不出邮件）
