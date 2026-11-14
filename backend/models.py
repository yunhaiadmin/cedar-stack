"""
统一数据模型
整合用户端、管理后台和主项目的所有数据模型
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, BigInteger, DateTime, Date, Text, Numeric, Index, ForeignKey, JSON, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from backend.database import Base


# ==================== 系统管理相关 ====================

class AdminUser(Base):
    """管理员用户表"""
    __tablename__ = 'admin_users'

    __table_args__ = (
        Index('idx_admin_username', 'username'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    email = Column(String(255))
    role = Column(String(50), default='admin')  # super_admin, admin, operator
    permissions = Column(JSON)  # 权限列表
    is_active = Column(Boolean, default=True)
    failed_login_count = Column(Integer, default=0)
    locked_until = Column(DateTime)
    last_login_at = Column(DateTime)
    last_login_ip = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AdminRole(Base):
    """管理员角色表"""
    __tablename__ = 'admin_roles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(String(255))
    permissions = Column(JSON)  # 权限列表
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class AdminLog(Base):
    """管理员操作日志表"""
    __tablename__ = 'admin_logs'

    __table_args__ = (
        Index('idx_admin_log_user', 'admin_user_id'),
        Index('idx_admin_log_action', 'action'),
        Index('idx_admin_log_time', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_user_id = Column(Integer, ForeignKey('admin_users.id'))
    action = Column(String(100), nullable=False)  # 操作类型
    target_type = Column(String(50))  # 操作目标类型
    target_id = Column(Integer)  # 操作目标ID
    details = Column(JSON)  # 操作详情
    ip_address = Column(String(50))
    user_agent = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)

    admin_user = relationship("AdminUser")


class SystemConfig(Base):
    """系统配置表"""
    __tablename__ = 'system_configs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(50), unique=True, nullable=False, index=True)
    value = Column(Text)
    description = Column(String(255))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ServerRealm(Base):
    """服（一个可独立运营的服务单元）—— 面板可以同时运营多个服

    一个服 = 一套独立对外提供播放的服务：

    - **内容**：该服的媒体库（``emby_libraries.realm_id``）与存储挂载（``storage_mounts.realm_id``）；
    - **卖什么**：该服的套餐（``subscription_plans.realm_id``）；
    - **卖给谁**：该服的订阅（``user_subscriptions.realm_id``）——同一个用户可以在多个服各有一份订阅；
    - **谁来放**：该服的服务器记录（``remote_servers.realm_id``，kind=ea）。
      同一个服可以部署到多台机器上（每台一个 EA，各有自己的 ``node_key``）同时出流。

    面板顶部的「当前服」决定后台各页默认在看哪个服（``SystemConfig["active_realm_id"]``）。
    升级上来的老部署会自动回填出一个默认服（``slug='main'``），行为与单服时完全一致。

    **接入方式（``access_mode``）**：一个面板可以同时运营两种服——

    - ``paid``（付费服，缺省）：需要生效中的订阅才能播放，卖套餐、走付费墙；
    - ``free``（公益服）：免费开放，**不需要订阅**即可播放全库内容。公益服一般靠
      注册/邀请引流，用 ``access_note`` 写清规则（限设备、禁止下载、禁止转卖…），
      并用 ``allow_download`` 关掉下载以保护资源与带宽。

    两种服的判定统一收口在 ``backend/subscriptions.py``（``can_play`` /
    ``ensure_download_allowed``）：升级上来的老服没有这个字段值时按 ``paid`` 处理，
    行为与升级前完全一致。
    """

    __tablename__ = 'server_realms'

    __table_args__ = (
        Index('idx_realm_slug', 'slug'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(80), unique=True, nullable=False)
    # 稳定标识：写进 EA 的 REALM 环境变量、也用于排查（改名不影响它）
    slug = Column(String(40), unique=True, nullable=False)
    # 该服对外的 Emby 地址（用户端账号卡用；留空则回退全局 EMBY_PUBLIC_URL）
    url = Column(String(500), default='')
    description = Column(String(300), default='')
    # v2.7.0 公益服：paid（需要订阅）/ free（免费开放）；空值按 paid 处理
    access_mode = Column(String(10), default='paid')
    # 公益服规则/说明（用户端展示，也作为付费墙与下载拦截的提示文案）
    access_note = Column(String(500), default='')
    # 该服下载策略：None=跟随全局（公益服默认禁止）/ True=允许 / False=禁止
    allow_download = Column(Boolean, nullable=True, default=None)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class RemoteServer(Base):
    """已添加的服务器（面板可以加多台，每类里挑一台作为「当前使用」）

    四类：

    - ``ea``：本项目自带的 Emby API（EA，分离部署的后端服）。激活后同步写回
      ``emby_managed_*`` 配置，现有的网关闸门 / 挂载体检 / 客户端指引全部照旧生效。
    - ``emby``：已有的第三方 Emby / Jellyfin。激活后同步写回 ``emby_external_*``。
    - ``moviepilot``：MoviePilot。求片批准后可一键提交为它的订阅（它自己去搜索与下载）。
    - ``qbittorrent``：qBittorrent。有磁力/种子链接时可直接交给它下载。

    ``config`` 存类型相关字段（JSON 文本），密钥类字段永不出接口（见 ``servers.mask_config``）。
    同一类型只能有一行 ``is_active``：由激活接口保证，避免出现「谁是当前入口」的歧义。
    **同一类型 + 同一个服** 只能有一行 ``is_active``（见 ``servers.activate``）。
    """

    __tablename__ = 'remote_servers'

    __table_args__ = (
        Index('idx_remote_server_kind', 'kind'),
        Index('idx_remote_server_realm', 'realm_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(80), unique=True, nullable=False)
    kind = Column(String(20), nullable=False)  # ea / emby / moviepilot / qbittorrent
    # 属于哪个服：每台 EA 只服务它所属服的内容与订阅（见 backend/realms.py）
    realm_id = Column(Integer, ForeignKey('server_realms.id'), nullable=True)
    # 节点标识：EA 启动时用环境变量 NODE_KEY 认领这条记录（见 emby_server/nodes.py）。
    # 只有 EA 需要它——它决定「这台机器负责哪些媒体库、只提供哪些内容」。
    node_key = Column(String(60), unique=True, nullable=True)
    url = Column(String(500), nullable=False)
    # 类型相关配置（JSON 文本）：api_key / username / password / savepath 等
    config = Column(Text, default='{}')
    is_enabled = Column(Boolean, default=True)
    # 同一类型里的「当前使用」：EA / Emby 会同步到 emby_active_mode 等旧配置键
    is_active = Column(Boolean, default=False)
    remark = Column(String(300), default='')
    # 最近一次连接测试结果（仅展示，不参与鉴权判断）
    last_checked_at = Column(DateTime)
    last_check_ok = Column(Boolean)
    last_check_message = Column(String(300))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class NotificationHistory(Base):
    """通知历史表"""
    __tablename__ = 'notification_history'

    __table_args__ = (
        Index('idx_notif_type', 'notification_type'),
        Index('idx_notif_time', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_type = Column(String(50), nullable=False)  # telegram, email, system
    target = Column(String(255))  # 接收者
    title = Column(String(255))
    content = Column(Text)
    status = Column(String(20), default='pending')  # pending, sent, failed
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    sent_at = Column(DateTime)


class StationMessage(Base):
    """站内消息表 - 前后台联动核心"""
    __tablename__ = 'station_messages'

    __table_args__ = (
        Index('idx_station_from', 'from_user_id'),
        Index('idx_station_to', 'to_user_id'),
        Index('idx_station_read', 'is_read'),
        Index('idx_station_type', 'message_type'),
        Index('idx_station_time', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_user_id = Column(Integer, ForeignKey('web_users.id'))  # 发送者（管理员 WebUser.id），系统消息为空
    to_user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)  # 接收者
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    message_type = Column(String(20), default='system')  # system, ticket, announcement, subscription, media_seek
    related_id = Column(Integer)  # 关联ID（如工单ID、公告ID）
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

    from_user = relationship("WebUser", foreign_keys=[from_user_id])
    to_user = relationship("WebUser", backref="station_messages", foreign_keys=[to_user_id])


# ==================== 用户相关 ====================

class WebUser(Base):
    """Web 用户表（网站登录用户）"""
    __tablename__ = 'web_users'

    __table_args__ = (
        Index('idx_web_username', 'username'),
        Index('idx_web_telegram_id', 'telegram_id'),
        Index('idx_web_email', 'email'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    email = Column(String(255))
    telegram_id = Column(BigInteger, unique=True)
    is_active = Column(Boolean, default=True)
    is_staff = Column(Boolean, default=False)
    # 管理员角色（v2.26.0，见 backend/admin_roles.py）：super / operator / viewer
    # 空值 = 升级前的老管理员 → 按 super 处理（不改权、也不会「没人进得去后台」）
    admin_role = Column(String(20), nullable=True)
    # 发言奖励小数累计遗留列（2026-10-31）：发言积分已随货币下线
    # （backend/points.py 已删、points 列已摘），三列不再读写。
    # chat_day（上海日期）+ chat_day_cents 记当日已累计（含已结算部分）。
    chat_frac_cents = Column(Integer, default=0, nullable=False, server_default="0")
    chat_day_cents = Column(Integer, default=0, nullable=False, server_default="0")
    chat_day = Column(String(10), nullable=True)
    # 注册渠道（v2.44.0 归因）：admin=管理员创建 / code=卡密注册 /
    # invitation=邀请码注册 / open=开放注册。
    # 空值 = 升级前的存量用户（当时没记，不硬猜），后台显示为「未记录」。
    register_channel = Column(String(20), index=True)
    # P2/P3 修复（审查）：token 版本号。改密码 / 管理员踢下线时 +1，
    # JWT payload 里带 tv，鉴权时比对——旧版本 token 全部失效（改密后旧 token 立即作废）。
    token_version = Column(Integer, default=0, nullable=False, server_default="0")

    # 自建 Emby 凭据（完全自建模式下，Emby 客户端用此账号密码登录）
    emby_username = Column(String(64), unique=True, nullable=True)
    emby_password = Column(String(128), nullable=True)
    # 公益服身份与资格四列（is_welfare / welfare_expires_at /
    # welfare_grant_channel / welfare_granted_at）已随福利服役族于
    # 2026-11-14 删除：资格已全量折算成订阅时间（折算执行档案在
    # 生产 /data/welfare_conversion/，防重标记 welfare_conversion_
    # executed_at 保留），不再有任何裁决读这四列。
    # 连签断签保护用量（2026-11-09 上线、同日随保号改活力口径回滚）：
    # streak_forgive_ym / streak_forgive_used 两列保留不用——不再有任何
    # 读写点（economy._do_checkin_core 已撤 forgiven 分支），不写 drop
    # 列迁移；老库已有数值原样留着，无副作用。
    streak_forgive_ym = Column(String(6), nullable=True)
    streak_forgive_used = Column(Integer, default=0, nullable=False, server_default="0")
    # 观影碑时长榜隐私：True=允许上榜（默认），False=不上榜
    show_on_leaderboard = Column(Boolean, default=True, nullable=False, server_default="1")
    # TG 私聊通知偏好（2026-11-08 私聊通报优化批）：四类各一个开关，
    # 只管 Telegram 那一份，站内信不受影响。默认 True=收。
    # 语义见 backend/notify_prefs.py。
    tg_notify_seek = Column(Boolean, default=True, nullable=False, server_default="1")
    tg_notify_expiry = Column(Boolean, default=True, nullable=False, server_default="1")
    tg_notify_lottery = Column(Boolean, default=True, nullable=False, server_default="1")
    tg_notify_pool_digest = Column(Boolean, default=True, nullable=False, server_default="1")
    last_login_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class UserPlayLine(Base):
    """用户播放线路偏好（线路选择功能）

    line 取值：direct（直连线路，默认：video_stream 先试 Google 直链 302，
              客户端直连 Google，VPS 不中转）
              relay（中转线路：跳过一切 302，直接走服务器代理转发，
              流量过 VPS，适合直连 Google 不通的用户）
    无记录 = direct（老用户默认直连，保持现有行为不变）。
    """
    __tablename__ = 'user_play_lines'

    user_id = Column(Integer, ForeignKey('web_users.id'), primary_key=True)
    line = Column(String(16), default='direct', nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    user = relationship("WebUser", backref="play_line_pref")


# ==================== 订阅和支付相关 ====================

class SubscriptionPlan(Base):
    """订阅套餐表"""
    __tablename__ = 'subscription_plans'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    price = Column(Numeric(10, 2), nullable=False)
    # 积分价（P2 货币体系双轨）：NULL=不支持积分购买；用户端可二选一（人民币 price 或积分 points_price）
    points_price = Column(Numeric(10, 2), nullable=True)
    duration_days = Column(Integer, nullable=False)
    features = Column(JSON)  # 特性列表
    # 订阅分档权益表：{权益键: 值}，NULL/缺键 = 该项跟随全站默认（判定单口见 backend/entitlements.py）。
    # 注意：JSON 列原地改键不触发脏标记，写路径必须整对象重新赋值，不许 plan.entitlements[key] = v。
    entitlements = Column(JSON)
    # 属于哪个服：套餐一个服一个（见 ServerRealm）。同一套餐只卖给该服的用户。
    realm_id = Column(Integer, ForeignKey('server_realms.id'), nullable=True)
    is_active = Column(Boolean, default=True)
    is_popular = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    realm = relationship("ServerRealm")


class UserSubscription(Base):
    """用户订阅表"""
    __tablename__ = 'user_subscriptions'

    __table_args__ = (
        Index('idx_sub_user', 'user_id'),
        Index('idx_sub_status', 'status'),
        Index('idx_sub_expiry', 'end_date'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    plan_id = Column(Integer, ForeignKey('subscription_plans.id'), nullable=False)
    # 订阅属于哪个服：决定它能在哪个服的 EA 上播放（见 backend/subscriptions.py）。
    # 同一个用户可以在多个服各有一份互不影响的订阅。
    realm_id = Column(Integer, ForeignKey('server_realms.id'), nullable=True)
    start_date = Column(DateTime, default=datetime.now)
    end_date = Column(DateTime, nullable=False)
    status = Column(String(20), default='active')  # active, expired, cancelled
    auto_renew = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    plan = relationship("SubscriptionPlan")
    user = relationship("WebUser")
    realm = relationship("ServerRealm")


class SubscriptionReminder(Base):
    """订阅到期提醒的发送记录（去重表）

    「到期前 7/3/1 天提醒续费」这个任务会周期性重复执行，**没有这张表就会反复提醒同一个人**。
    每条（订阅 × 档位）只允许一行：``kind`` 是 ``7d`` / ``3d`` / ``1d``，
    以及到期那一刻的一次 ``expired``。写入成功即代表已发过，不再重发。
    """
    __tablename__ = 'subscription_reminders'

    __table_args__ = (
        UniqueConstraint('subscription_id', 'kind', name='uq_sub_reminder_kind'),
        Index('idx_sub_reminder_sub', 'subscription_id'),
        Index('idx_sub_reminder_time', 'sent_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscription_id = Column(Integer, ForeignKey('user_subscriptions.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    # 冗余存一份归属服：结算/排查时不必再回查订阅行
    realm_id = Column(Integer, ForeignKey('server_realms.id'), nullable=True)
    kind = Column(String(20), nullable=False)  # 7d / 3d / 1d / expired
    sent_at = Column(DateTime, default=datetime.now)


# WelfareReminder（welfare_reminders 去重表）已随福利服役族于
# 2026-11-14 删除：福利到期提醒轨下线，订阅轨提醒不受影响。


class SubscriptionOrder(Base):
    """订阅订单表"""
    __tablename__ = 'subscription_orders'

    __table_args__ = (
        Index('idx_suborder_user', 'user_id'),
        Index('idx_suborder_status', 'status'),
        Index('idx_suborder_order_id', 'order_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    plan_id = Column(Integer, ForeignKey('subscription_plans.id'), nullable=False)
    item_name = Column(String(255))
    amount = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String(50))
    status = Column(String(20), default='pending')  # pending / paid / refunded / closed
    payment_url = Column(String(500))
    paid_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    # 履约时记下「这条订单开出了哪份订阅、发了多少天」：退款要按这笔回滚天数。
    # 不记就只能猜（用户可能同时有别的来源的天数），退款就会多扣或少扣。
    subscription_id = Column(Integer, ForeignKey('user_subscriptions.id'), nullable=True)
    days_granted = Column(Integer, default=0)
    closed_at = Column(DateTime)
    refunded_at = Column(DateTime)
    refund_reason = Column(String(255))
    list_price = Column(Numeric(10, 2), default=0)
    discount_amount = Column(Numeric(10, 2), default=0)
    coupon_usage_id = Column(Integer, ForeignKey('coupon_usages.id'), nullable=True)

    plan = relationship("SubscriptionPlan")
    user = relationship("WebUser")


# ==================== Emby 相关 ====================

class EmbyServer(Base):
    """Emby 服务器表"""
    __tablename__ = 'emby_servers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    url = Column(String(255), nullable=False)
    api_key = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    max_users = Column(Integer, default=0)
    current_users = Column(Integer, default=0)
    priority = Column(Integer, default=0)  # 优先级
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 监控数据
    last_check_at = Column(DateTime)
    status = Column(String(20), default='unknown')  # online, offline, unknown
    response_time = Column(Integer)  # 响应时间（毫秒）


class PlanServerRelation(Base):
    """套餐服务器关联表（负载均衡）"""
    __tablename__ = 'plan_server_relations'

    __table_args__ = (
        Index('idx_psr_plan', 'plan_id'),
        Index('idx_psr_server', 'server_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey('subscription_plans.id'), nullable=False)
    server_id = Column(Integer, ForeignKey('emby_servers.id'), nullable=False)
    weight = Column(Integer, default=1)  # 权重
    created_at = Column(DateTime, default=datetime.now)

    plan = relationship("SubscriptionPlan")
    server = relationship("EmbyServer")


class UserEmbyAccount(Base):
    """用户 Emby 账号表"""
    __tablename__ = 'user_emby_accounts'

    __table_args__ = (
        Index('idx_emby_user', 'user_id'),
        Index('idx_emby_server', 'server_id'),
        Index('idx_emby_sub', 'subscription_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    server_id = Column(Integer, ForeignKey('emby_servers.id'), nullable=False)
    subscription_id = Column(Integer, ForeignKey('user_subscriptions.id'), nullable=False)
    emby_user_id = Column(String(100))
    username = Column(String(100))
    password = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)
    expires_at = Column(DateTime)
    is_active = Column(Boolean, default=True)

    server = relationship("EmbyServer")
    user = relationship("WebUser")
    subscription = relationship("UserSubscription")


class EmbySession(Base):
    """Emby 会话监控表"""
    __tablename__ = 'emby_sessions'

    __table_args__ = (
        Index('idx_session_user', 'user_id'),
        Index('idx_session_server', 'server_id'),
        Index('idx_session_time', 'start_time'),
    )

    id = Column(String(100), primary_key=True)  # Emby Session ID
    server_id = Column(Integer, ForeignKey('emby_servers.id'))
    user_id = Column(Integer, ForeignKey('web_users.id'))
    username = Column(String(100))
    item_name = Column(String(255))
    item_type = Column(String(50))  # movie, series, episode
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    play_duration = Column(Integer, default=0)  # 播放时长（秒）
    device_name = Column(String(100))
    client_name = Column(String(100))

    server = relationship("EmbyServer")
    user = relationship("WebUser")


class MovieBookmark(Base):
    """电影收藏表"""
    __tablename__ = 'movie_bookmarks'

    __table_args__ = (
        Index('idx_bookmark_user', 'user_id'),
        Index('idx_bookmark_item', 'item_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    item_id = Column(String, nullable=False)
    item_name = Column(String)
    item_type = Column(String)
    bookmark_type = Column(String, default='favorite')  # favorite, watchlist
    rating = Column(Integer)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


# ==================== 工单系统 ====================

class Ticket(Base):
    """工单表"""
    __tablename__ = 'tickets'

    __table_args__ = (
        Index('idx_ticket_user', 'user_id'),
        Index('idx_ticket_status', 'status'),
        Index('idx_ticket_priority', 'priority'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    title = Column(String(200), nullable=False)
    category = Column(String(50), default='other')
    priority = Column(String(20), default='medium')
    status = Column(String(20), default='open')
    admin_id = Column(Integer, ForeignKey('admin_users.id'))  # 处理管理员
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    user = relationship("WebUser")
    admin = relationship("AdminUser")


class TicketMessage(Base):
    """工单消息表"""
    __tablename__ = 'ticket_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey('tickets.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    admin_id = Column(Integer, ForeignKey('admin_users.id'), nullable=True)
    message = Column(Text, nullable=False)
    attachments = Column(JSON)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)

    ticket = relationship("Ticket")
    user = relationship("WebUser")
    admin = relationship("AdminUser")


# ==================== 公告和活动 ====================

class Announcement(Base):
    """公告表"""
    __tablename__ = 'announcements'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    type = Column(String(20), default='system')
    is_active = Column(Boolean, default=True)
    is_pinned = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ThemeActivity(Base):
    """主题观影活动表"""
    __tablename__ = 'theme_activities'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    activity_type = Column(String(50), nullable=False)
    filter_genre = Column(String)
    filter_director = Column(String)
    filter_series = Column(String)
    target_count = Column(Integer)
    reward_mp = Column(Integer)
    reward_title = Column(String)
    is_active = Column(Boolean, default=True)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)


class ThemeActivityProgress(Base):
    """活动进度表"""
    __tablename__ = 'theme_activity_progress'

    __table_args__ = (
        Index('idx_actprog_user', 'user_id'),
        Index('idx_actprog_activity', 'activity_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(Integer, ForeignKey('theme_activities.id'), nullable=False)
    user_id = Column(BigInteger, nullable=False)
    progress = Column(Integer, default=0)
    watched_items = Column(Text, default='')
    completed = Column(Boolean, default=False)
    reward_claimed = Column(Boolean, default=False)
    claimed_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ==================== 经济系统（积分/兑换码） ====================

class EmbyViewUnlock(Base):
    """Emby 账号/线路查看权限存量行（公益服）

    付费服的查看权限来自有效订阅，不走这张表。公益服历史上靠花积分
    购买在这里留行，另有资格码打包赠送与管理员赠送两条写口；2026-11-09
    资格与凭证合并后购买写口退役，查看改为随公益资格自动开通，本表
    存量行继续有效到期为止、作过渡放行依据（见 subscriptions.view_grant）。
    同一用户同一服只保留一条，靠唯一约束防并发重复发放。
    """
    __tablename__ = 'emby_view_unlocks'

    __table_args__ = (
        UniqueConstraint('user_id', 'realm_id', name='uq_emby_view_unlock_user_realm'),
        Index('idx_emby_view_unlock_user', 'user_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    realm_id = Column(Integer, ForeignKey('server_realms.id'), nullable=False)
    unlocked_at = Column(DateTime, default=datetime.now)
    expires_at = Column(DateTime, nullable=True)  # NULL = 永久有效
    points_spent = Column(Integer, default=0)  # 本次解锁花费的积分

    user = relationship("WebUser")


class ExchangeCode(Base):
    """兑换码表（积分兑换 / 订阅兑换 / 折扣兑换）"""
    __tablename__ = 'exchange_codes'

    __table_args__ = (
        Index('idx_exchange_code', 'code'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    type = Column(String(20), default='points')  # points 积分 / subscription 订阅 / discount 折扣
    points_value = Column(Integer, default=0)  # points 型：兑换积分数
    plan_id = Column(Integer, ForeignKey('subscription_plans.id'), nullable=True)  # subscription 型：套餐
    duration_days = Column(Integer, default=0)  # subscription 型：时长
    discount_pct = Column(Integer, default=0)  # discount 型：实付百分比，85=八五折
    max_uses = Column(Integer, default=1)
    use_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    note = Column(String(255))
    used_by = Column(String(500))  # 逗号分隔的 WebUser.id 审计
    expires_at = Column(DateTime)
    created_by = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    plan = relationship("SubscriptionPlan")

class ExchangeDiscountCredit(Base):
    """兑换码折扣权益：用户核销 discount 型兑换码后获得一张，下次购买订阅时自动抵扣。

    一张兑换码每人限核销一次（走 CodeRedemption 唯一约束的老逻辑），一张权益只用一次。
    不与优惠券叠加：下单时填了 coupon_code 就走优惠券，否则用这里最优的一张。
    """
    __tablename__ = 'exchange_discount_credits'

    __table_args__ = (
        Index('idx_exdisc_user', 'user_id'),
        Index('idx_exdisc_status', 'status'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    # R32-F1：来源可能是统一码系统的 registration_codes（此时记 0，不是
    # exchange_codes 的行），去掉外键与 NOT NULL。SQLite 本就不强制 FK，
    # PG 老库由 backend/database.py 的 _migrate_exchange_discount_credit_fk 迁移。
    # 该列全仓只写不读（消费端只按 user_id/status 查），无语义影响。
    exchange_code_id = Column(Integer, nullable=True)
    discount_pct = Column(Integer, default=0)  # 实付百分比，85=八五折
    status = Column(String(20), default='unused')  # unused 未用 / used 已用
    used_order_id = Column(String(64))
    expires_at = Column(DateTime)  # 继承兑换码的 expires_at，可空
    created_at = Column(DateTime, default=datetime.now)


# ==================== 邀请系统 ====================

class CodeRedemption(Base):
    """每人每码一次的核销记录（安全修复 H5）

    兑换码 / 卡码的 ``max_uses`` 是**总**次数：此前「100 人份」的公共码同一个账号能一直兑换。
    这里用 ``(code_kind, code_id, user_id)`` 唯一约束兜底：核销时在**同一事务**里先 INSERT，
    并发的第二个请求在唯一索引上冲突（PostgreSQL 等第一个提交后报 IntegrityError），
    直接拒绝，不会发两份奖励。``code_kind``：``exchange`` = ExchangeCode，``reg`` = RegistrationCode。
    """
    __tablename__ = 'code_redemptions'
    __table_args__ = (
        UniqueConstraint('code_kind', 'code_id', 'user_id', name='uq_code_redemption_user'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code_kind = Column(String(20), nullable=False)
    code_id = Column(Integer, nullable=False)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now)


class RegistrationCode(Base):
    """卡码表（注册码 / 续期码 / 白名单码 / 诱饵码 / 指名码）

    借鉴 twilight-kotomi 的 RegCode：一份数据结构靠 code_type 区分用途，
    days 表示授予或叠加的会员天数，target_username 为指名码。
    诱饵码（蜜罐）不建列：靠 HONEY- 码前缀识别（见 backend/codes.is_honeypot），
    零数据库表结构改动。
    本项目的会员口径以 UserSubscription(end_date) 为单一事实来源，
    因此卡码的「天数」最终落到订阅上（无生效订阅则新建，有则叠加）。
    """
    __tablename__ = 'registration_codes'

    __table_args__ = (
        Index('idx_reg_code', 'code'),
        Index('idx_reg_code_type', 'code_type'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    max_uses = Column(Integer, default=1)
    use_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    note = Column(String(255))
    used_by = Column(String(500))  # 逗号分隔的 WebUser.id 审计
    expires_at = Column(DateTime)
    created_by = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    # 属于哪个服：这张卡码开出来的是那个服的会员（见 economy.redeem_registration_code）
    realm_id = Column(Integer, ForeignKey('server_realms.id'), nullable=True)
    # ===== 卡码体系（借鉴 twilight-kotomi 的 RegCode）=====
    code_type = Column(Integer, default=1)  # 1 注册码 / 2 续期码 / 3 白名单码
    days = Column(Integer, default=30)  # 授予或叠加的会员天数；-1 表示永久
    target_username = Column(String(50))  # 指名码：非空时仅限该用户名使用
    source = Column(String(20), default='admin')  # admin 管理员发放 / invite 邀请体系生成
    # ===== M1 统一码系统：并入兑换码的奖励类型 =====
    # reward_type: subscription 会员天数（默认，原卡码行为）/ points 积分 / discount 折扣权益
    # / welfare 公益资格（2026-10-30 G1：卡码直发资格，见 codes.redeem_code）
    reward_type = Column(String(20), default='subscription')
    points_value = Column(Integer, default=0)  # points 型：兑换积分数
    balance_value = Column(Numeric(12, 2), default=0)  # balance 型：兑换入账金额（元）
    discount_pct = Column(Integer, default=0)  # discount 型：实付百分比，85=八五折
    # welfare 型：核销时是否打包首张查看行（EmbyViewUnlock，同天数、points_spent=0）。
    # 老数据补列默认 1（打包），与生成表单默认开一致。
    welfare_include_view = Column(Boolean, default=True)
    # ===== 代理分销（2026-11-06）：代理进货生成的码带出码代理标记 =====
    # 非空 = 这张码是代理用进货余额买下的（source='agent')，核销成功时
    # 按 agent_purchase_id 的进货快照落销售归属（见 backend/agents.py）。
    # 管理员/邀请生成的码两列均为 NULL，行为与此前完全一致。
    agent_id = Column(Integer, ForeignKey('agents.id'), nullable=True)
    agent_purchase_id = Column(Integer, ForeignKey('agent_purchases.id'), nullable=True)

    # 按代理查码（代理中心「我的卡码」）；老库由 database._LEGACY_INDEXES
    # 同名 spec 补建，两边同名保证幂等跳过、不重复建。
    __table_args__ = (
        Index('idx_reg_code_agent', 'agent_id'),
    )


class WhitelistSerial(Base):
    """白名单创世编号（独一无二体验包·身份组，2026-11-02）

    编号按「成为白名单的先后」分配（核销痕迹时间，分配逻辑见
    codes.ensure_whitelist_serials），一旦分配永不变更、永不复用：
    账号被回收也只下线展示（判定走 codes.is_whitelist_account），
    编号行保留占位，新人不得占走已分配的号。
    user_id / serial 双唯一约束是并发分配的兜底。
    """
    __tablename__ = 'whitelist_serials'

    __table_args__ = (
        UniqueConstraint('user_id', name='uq_whitelist_serial_user'),
        UniqueConstraint('serial', name='uq_whitelist_serial_no'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False, index=True)
    serial = Column(Integer, nullable=False)
    assigned_at = Column(DateTime, default=datetime.now)


# ==================== 代理分销（2026-11-06 用户亲点完整版） ====================
# 语义口径见 backend/agents.py：等级/折扣是管理员可配数据、不写死；
# 代理余额是独立账本（agents.balance + agent_balance_logs 只增不改），
# 不与积分钱包（web_users.points / points_logs）混账。

class AgentLevel(Base):
    """代理等级（管理员可配：名称 / 进货折扣率 / 排序 / 启停）

    discount_rate 是进货折扣率：进货单价 = 面值 × discount_rate
    （0.8 = 按面值八折进货）。取值 (0, 1]，校验在管理 API 层。
    """
    __tablename__ = 'agent_levels'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)
    discount_rate = Column(Numeric(5, 4), nullable=False)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Agent(Base):
    """代理（管理员把某个站内用户指定为代理并选等级；取消 = is_active 置假）

    balance 是进货余额（独立账本的当前值），每次变动必须同步写一条
    agent_balance_logs 流水（只增不改），扣减走条件 UPDATE 防扣成负数。
    """
    __tablename__ = 'agents'

    __table_args__ = (
        UniqueConstraint('user_id', name='uq_agent_user'),
        Index('idx_agent_level', 'level_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    level_id = Column(Integer, ForeignKey('agent_levels.id'), nullable=False)
    balance = Column(Numeric(12, 2), nullable=False, default=0)
    is_active = Column(Boolean, default=True)
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    user = relationship("WebUser")
    level = relationship("AgentLevel")


class AgentBalanceLog(Base):
    """代理进货余额流水（只增不改：所有余额变动的唯一留痕）

    kind：admin_adjust 管理员手工增减 / purchase 代理进货扣款。
    amount 带符号（正入负出），balance_after 是变动后的余额快照。
    """
    __tablename__ = 'agent_balance_logs'

    __table_args__ = (
        Index('idx_agent_log_agent', 'agent_id'),
        Index('idx_agent_log_time', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey('agents.id'), nullable=False)
    kind = Column(String(20), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    balance_after = Column(Numeric(12, 2), nullable=False)
    note = Column(String(255))
    operator_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    ref_id = Column(Integer, nullable=True)  # purchase 时为 agent_purchases.id
    created_at = Column(DateTime, default=datetime.now)


class UserWalletBalance(Base):
    """用户钱包余额账本（一人一行，现金余额，全站唯一货币）

    balance 是人民币元（Numeric(12,2)），绝不为负：扣款走条件 UPDATE
    （balance >= amount 才扣），见 backend/wallet.py。不复用 web_users.points
    （旧积分钱包已下线，混账是事故之源，与代理余额「不混账」同口径）。
    每次变动必须同步写一条 wallet_ledger 流水（只增不改）。
    """
    __tablename__ = 'user_wallet_balances'

    user_id = Column(Integer, ForeignKey('web_users.id'), primary_key=True)
    balance = Column(Numeric(12, 2), nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    user = relationship("WebUser")


class WalletLedger(Base):
    """钱包收支流水（只增不改：所有余额变动的唯一留痕，错账用冲正行纠）

    kind：recharge 在线充值 / admin_adjust 管理员手工增减 / gift_code
    兑换码入账 / rebate 邀请返利 / subscription_purchase 订阅购买扣款 /
    traffic_purchase 购流量扣款 / refund 退款回余额 / rebate_reversal
    返利撤回。amount 带符号（正入负出），balance_after 是变动后快照。
    idempotency_key 唯一（可空）：扣款/入账重复提交时原样回放，不重复成交。
    prev_hash / record_hash 照旧积分台账的链式审计（按用户串链）。
    """
    __tablename__ = 'wallet_ledger'

    __table_args__ = (
        Index('idx_wallet_ledger_user', 'user_id'),
        Index('idx_wallet_ledger_kind', 'kind'),
        Index('idx_wallet_ledger_time', 'created_at'),
        UniqueConstraint('idempotency_key', name='uq_wallet_ledger_idem'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    kind = Column(String(30), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    balance_after = Column(Numeric(12, 2), nullable=False)
    ref_id = Column(String(64), nullable=True, index=True)
    idempotency_key = Column(String(64), nullable=True)
    note = Column(String(255))
    operator_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    prev_hash = Column(String(64), nullable=True)  # 上一条该用户流水的 record_hash（链头为空字符串）
    record_hash = Column(String(64), nullable=True)  # 本条记录的 hash
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("WebUser", foreign_keys=[user_id])


class AgentPurchase(Base):
    """代理进货单（一次进货 = 一批同套餐的卡码）

    面值 / 折扣率 / 单价在进货时点快照：之后管理员改套餐价或等级折扣，
    历史进货与销售归属的金额口径不跟着变。
    """
    __tablename__ = 'agent_purchases'

    __table_args__ = (
        Index('idx_agent_purchase_agent', 'agent_id'),
        Index('idx_agent_purchase_time', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey('agents.id'), nullable=False)
    plan_id = Column(Integer, ForeignKey('subscription_plans.id'), nullable=False)
    plan_name = Column(String(100))  # 套餐名快照
    realm_id = Column(Integer, ForeignKey('server_realms.id'), nullable=True)
    code_type = Column(Integer, default=1)  # 1 注册码 / 2 续期码
    quantity = Column(Integer, nullable=False)
    face_value = Column(Numeric(10, 2), nullable=False)  # 单张面值（套餐价快照）
    discount_rate = Column(Numeric(5, 4), nullable=False)  # 等级折扣率快照
    unit_cost = Column(Numeric(12, 2), nullable=False)  # 单张进货价
    total_cost = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class AgentSale(Base):
    """代理销售记录（代理码被终端用户核销时落一行，归属到出码代理）

    在 codes.redeem_code 的核销事务里写入：核销回滚时归属一并撤销。
    face_value / unit_cost 取进货单快照，销售毛利 = face_value - unit_cost。
    """
    __tablename__ = 'agent_sales'

    __table_args__ = (
        Index('idx_agent_sale_agent', 'agent_id'),
        Index('idx_agent_sale_time', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey('agents.id'), nullable=False)
    code_id = Column(Integer, ForeignKey('registration_codes.id'), nullable=False)
    purchase_id = Column(Integer, ForeignKey('agent_purchases.id'), nullable=True)
    buyer_user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    face_value = Column(Numeric(10, 2), nullable=False)
    unit_cost = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class UserDevice(Base):
    """用户设备（第三方播放器登录设备，用于设备上限与设备审查）

    借鉴 twilight-kotomi 的设备限制：按 user_id + device_id 唯一，
    记录客户端名称/版本/IP 与首末次出现时间，超限时可拒绝新设备或踢最久未使用者。
    """
    __tablename__ = 'user_devices'

    __table_args__ = (
        UniqueConstraint('user_id', 'device_id', name='uq_device_user_device'),
        Index('idx_device_user', 'user_id'),
        Index('idx_device_last_seen', 'last_seen_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    device_id = Column(String(128), nullable=False)
    name = Column(String(100))  # 设备名（X-Emby-Authorization Device）
    client = Column(String(100))  # 客户端（Infuse / Forward / SenPlayer ...）
    app_version = Column(String(50))
    ip = Column(String(64))
    first_seen_at = Column(DateTime, default=datetime.now)
    last_seen_at = Column(DateTime, default=datetime.now)
    is_blocked = Column(Boolean, default=False)

    user = relationship("WebUser")


class LoginLog(Base):
    """登录与账号安全日志

    记录登录成功/失败、设备超限被拒、诱饵码触发封禁等事件，
    供管理后台风控审查（reason 区分事件类型）。
    """
    __tablename__ = 'login_logs'

    __table_args__ = (
        Index('idx_loginlog_user', 'user_id'),
        Index('idx_loginlog_time', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    username = Column(String(64))
    ip = Column(String(64))
    user_agent = Column(String(300))
    success = Column(Boolean, default=True)
    reason = Column(String(100))  # portal_login / emby_login / device_limit / decoy_code ...
    detail = Column(String(255))
    # IP 归属地（能力：IP 与地理位置）；未配置提供方时为空——由 authlog 在写入时填
    region = Column(String(100))
    created_at = Column(DateTime, default=datetime.now)


class PasswordResetToken(Base):
    """邮箱找回密码令牌（2026-11-13 用户亲点补邮箱找回流程）

    原始令牌只出现在发给用户的邮件链接里，库里只存 SHA256 十六进制
    （与 emby_api_tokens 同口径）；30 分钟过期、单次使用，用户重新申请时
    旧的未用令牌一律作废（used_at 置为作废时刻）。
    """
    __tablename__ = 'password_reset_tokens'

    __table_args__ = (
        Index('idx_pwreset_user', 'user_id'),
        Index('idx_pwreset_hash', 'token_hash'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    token_hash = Column(String(64), nullable=False)  # SHA256 hex，非明文
    created_at = Column(DateTime, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)  # 已消费或被新申请作废时落值
    request_ip = Column(String(64))


class ShareGuardEvent(Base):
    """防共享事件（v2.43.0）：跨城市轨迹 + 同播检测的判定结果

    为什么单独一张表而不是复用 ``login_logs``：两者的**查询口径**不一样。
    ``login_logs`` 是「登录与安全事件的流水」，按 ``reason`` 筛选；这里是
    「一个账号在时间轴上的位置与并发情况」，要按 ``(user_id, kind, created_at)``
    往前翻最近一次在哪、上一次并发了几路。两张表混在一起会让登录日志页
    既慢又难读。

    **两种 kind 共用一张表**：它们是同一件事的两面（账号被多人共用），
    后台要在一个地方按时间看完整轨迹，而不是翻两个页签。

    行是**有界**的：见 ``backend/share_guard.py`` 的写入节流（同一区域每隔
    ``travel_window_minutes`` 最多一行 / 城市一变立刻一行），再加按保留天数清理，
    所以开着也不会无限长。
    """

    __tablename__ = 'share_guard_events'

    __table_args__ = (
        Index('idx_shareguard_user_kind', 'user_id', 'kind', 'created_at'),
        Index('idx_shareguard_time', 'created_at'),
        Index('idx_shareguard_kind', 'kind'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    username = Column(String(64))
    # travel = 跨城市轨迹；concurrent = 同播检测
    kind = Column(String(20), nullable=False, index=True)
    # 基线行（该账号当前所在的域市）不是一次判定。它要落库——下一次的比对就是拿它当
    # 「上一次在哪」——但混在事件流水里会让「判出来过什么」失真，所以单独标出来。
    is_baseline = Column(Boolean, default=False, nullable=False, index=True)
    # 实际生效的档位：record（只记录）/ alert（记录 + 通知管理员）/ enforce（记录 + 处置）
    # off 不会产生任何行——「关闭」的意思就是不写库，而不是「写一行表示关闭」
    action = Column(String(20), default='record', nullable=False)
    ip = Column(String(64))
    # 归属地串（能力：IP 与地理位置）。查不到就是空串，空串**不参与**城市比对
    region = Column(String(100))
    # travel：上一次的城市（空 = 首次或上次查不到）；concurrent：本次判定时的并发数
    prev_region = Column(String(100))
    sessions = Column(Integer, default=0)
    # 人看的说明（已含城市、窗口、并发数等具体数字）
    detail = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, index=True)


class AiUsage(Base):
    """AI 助手每日用量（能力：AI 模型设置）

    原先是进程内字典计数：多进程部署下每个 worker 各记一份，配额会被放大到「上限 × 进程数」，
    重启还会归零。改成落库 + 唯一约束（user_id, day），配额在多进程、多台机器上都是同一份；
    占用额度用「条件 UPDATE + 唯一约束兼底」实现，并发下不会两个请求都读到 limit-1。
    """
    __tablename__ = 'ai_usage'

    __table_args__ = (
        UniqueConstraint('user_id', 'day', name='uq_ai_usage_user_day'),
        Index('idx_ai_usage_day', 'day'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    day = Column(String(10), nullable=False)   # 本地日期 YYYY-MM-DD
    count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class InvitationCode(Base):
    """邀请码表"""
    __tablename__ = 'invitation_codes'

    __table_args__ = (
        Index('idx_inv_code', 'code'),
        Index('idx_inv_user', 'user_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    max_uses = Column(Integer, default=100)
    use_count = Column(Integer, default=0)
    reward_points = Column(Integer, default=0)
    expires_at = Column(DateTime)
    is_active = Column(Boolean, default=True)
    # 白名单（可选）：逗号分隔的用户名。非空时**只有这些人**能拿这个码注册，
    # 用完即无效——与卡码的 target_username 同一个思路（内测码 / 渠道码）。
    # 空 = 不限（与升级前行为一致）。
    whitelist = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("WebUser")


# WelfareGrantLog（welfare_grant_logs 发放总账）已随福利服役族于
# 2026-11-14 删除：折算对账档案另存生产 /data/welfare_conversion/。


class InvitationRecord(Base):
    """邀请记录表"""
    __tablename__ = 'invitation_records'

    __table_args__ = (
        Index('idx_inv_inviter', 'inviter_id'),
        # 一个被邀请人只能有一条关系记录：并发重复发奖的最后一道门
        Index('idx_inv_invitee', 'invitee_id', unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    inviter_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    invitee_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    code_id = Column(Integer, ForeignKey('invitation_codes.id'), nullable=False)
    reward_points = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)

    inviter = relationship("WebUser", foreign_keys=[inviter_id])
    invitee = relationship("WebUser", foreign_keys=[invitee_id])
    code = relationship("InvitationCode")


class PromotionReward(Base):
    """推广奖励记录（v2.44.0 第一阶段）

    邀请成功后**另发**的一笔奖励，与既有的双向积分奖励并列、互不干扰：
    那边是「邀请这件事本身的回报」，这边是「本期推广激励」，所以走独立的
    开关与阈值（``promotion_reward_*``），默认关闭。

    奖励类型两选一（值全部来自 SystemConfig，不在代码里写死）：

    - ``balance``：加积分余额（``reward_value`` = 积分数）；
    - ``days``：加会员有效期（``reward_value`` = 天数，``realm_id`` 记归属服）。

    一行一条，用户在「我的」里看到的明细就是这张表；``config_snapshot``
    记下发时的开关与阈值，否则日后改了配置就再也说不清「当时到底按多少发的」。
    """
    __tablename__ = 'promotion_rewards'

    __table_args__ = (
        # 一个被邀请人只能给邀请人发一笔（与 invitation_records 的唯一索引同一道门）
        Index('idx_promo_invitee', 'invitee_id', unique=True),
        Index('idx_promo_inviter', 'inviter_id'),
        Index('idx_promo_time', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    inviter_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    invitee_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    code_id = Column(Integer, ForeignKey('invitation_codes.id'))
    # balance / days（见 promotion.py 的 REWARD_TYPES）
    reward_type = Column(String(16), nullable=False)
    reward_value = Column(Integer, nullable=False, default=0)
    # reward_type=days 时的归属服（空 = 当前服，与 grant_membership_days 同口径）
    realm_id = Column(Integer)
    # 下发时的配置快照（JSON），事后能还原「按哪一档发的」
    config_snapshot = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)

    inviter = relationship("WebUser", foreign_keys=[inviter_id])
    invitee = relationship("WebUser", foreign_keys=[invitee_id])
    code = relationship("InvitationCode")


class InviteRebateAccrual(Base):
    """邀请返利计提（钱包时代，2026-11-14）：被邀请人每笔真实付费产生一笔。

    与旧积分返利（旧积分台账 type=rebate，已随货币下线冻结）分开记账：
    本表是「应返多少钱」的计提台账，金额一律人民币元 Numeric(12,2)。

    - ``source`` + ``source_ref`` 唯一：一笔付费只计提一次，支付回调
      重放 / 履约重试都不会重复返利（幂等的第一道门；钱包入账侧再用
      idempotency_key 兜第二道）。
    - ``status``：pending = 已计提未入账（钱包未上线/被关停时挂账，
      不丢账）；credited = 已入邀请人钱包（``wallet_ledger_id`` 指回
      钱包流水行）。退款撤回由钱包批 2 按 (source, source_ref) 精确
      定位本行处理，本表不改金额，只随状态流转。
    - 撤回（钱包批 2）：退款时从邀请人钱包扣回返利成功 → status 转
      ``reversed``（``reversed_at`` 留痕）；邀请人余额不足扣不回 →
      不转态，置 ``reversal_owed=True``（仍是 credited，钱还在邀请
      人钱包里），待管理员在钱包管理端重试/豁免（见
      backend/api/admin_wallet 的 rebate-reversals 端点）。
    - ``percent`` 记计提时的比例快照，事后改配置不影响已计提的账。
    """
    __tablename__ = 'invite_rebate_accruals'

    __table_args__ = (
        UniqueConstraint('source', 'source_ref', name='uq_invite_rebate_source'),
        Index('idx_invite_rebate_inviter', 'inviter_id'),
        Index('idx_invite_rebate_invitee', 'invitee_id'),
        Index('idx_invite_rebate_status', 'status'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    inviter_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    invitee_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    # subscription_order = 网关订阅订单（source_ref = order_id）；
    # wallet_topup / wallet_purchase 预留给钱包批 2/3 的付费入口
    source = Column(String(30), nullable=False)
    source_ref = Column(String(64), nullable=False)
    paid_amount = Column(Numeric(12, 2), nullable=False)    # 被邀请人实付（元）
    rebate_amount = Column(Numeric(12, 2), nullable=False)  # 应返金额（元）
    percent = Column(Integer, nullable=False)               # 计提时比例快照 %
    status = Column(String(20), default='pending', nullable=False)
    wallet_ledger_id = Column(Integer)  # credited 后回填钱包流水行 id
    created_at = Column(DateTime, default=datetime.now)
    credited_at = Column(DateTime)
    # 钱包批 2 撤回留痕：reversal_owed=True = 退款时邀请人余额不足、
    # 返利没扣回来（仍是 credited，待重试/豁免）；reversed_at = 扣回时间。
    reversal_owed = Column(Boolean, default=False)
    reversed_at = Column(DateTime, nullable=True)

    inviter = relationship("WebUser", foreign_keys=[inviter_id])
    invitee = relationship("WebUser", foreign_keys=[invitee_id])


class InviteTrialGrant(Base):
    """被邀请人见面礼（试用订阅）发放留痕：一个被邀请人只发一次。

    邀请关系建立时按 ``invite_trial_days`` 配置给被邀请人发会员天数
    （体验权益、不可变现，与返利「只挂真实付费」不冲突）。本表既是
    明细也是防重复领取的那道门（invitee_id 唯一索引兜底），发放与
    邀请关系同一事务落盘。
    """
    __tablename__ = 'invite_trial_grants'

    __table_args__ = (
        Index('idx_invite_trial_invitee', 'invitee_id', unique=True),
        Index('idx_invite_trial_inviter', 'inviter_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    inviter_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    invitee_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    days = Column(Integer, nullable=False, default=0)
    realm_id = Column(Integer)  # 天数落到的服（与 grant_membership_days 同口径）
    created_at = Column(DateTime, default=datetime.now)

    inviter = relationship("WebUser", foreign_keys=[inviter_id])
    invitee = relationship("WebUser", foreign_keys=[invitee_id])


# ==================== 优惠券 ====================

class CouponCode(Base):
    """优惠券（购买时抵扣，v2.10.0）

    与兑换码（`ExchangeCode`）不是一回事：兑换码是「不花钱直接拿东西」，
    优惠券是「付费时打折 / 减钱」——钱还是要走支付网关，只是单价变了。

    **使用次数是预订制的**：下单时就占额度（`CouponUsage.status='reserved'`），
    支付成功转 `consumed`，订单被关掉/退款则 `released` 并把额度还回去。
    只在付款成功时计数的话，一串未支付订单会同时绕过限额，付完款全都拿到折扣。
    """
    __tablename__ = 'coupon_codes'

    __table_args__ = (
        UniqueConstraint('code', name='uq_coupon_code'),
        Index('idx_coupon_kind', 'kind'),
        Index('idx_coupon_active', 'is_active'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(32), unique=True, nullable=False, index=True)
    # 适用范围：subscription 只能买会员 / recharge 只能充值 / all 两者皆可
    kind = Column(String(20), default='all')
    # percent 百分比（value=85 即 85 折、实付 85%）；fixed 直接减免（value=单位元）
    discount_type = Column(String(20), default='percent')
    value = Column(Integer, default=0)
    # 满多少可用（0 = 不设门槛）；百分比券的封顶减免（0 = 不封顶）
    min_amount = Column(Numeric(10, 2), default=0)
    max_discount = Column(Numeric(10, 2), default=0)
    # 只对某个服的套餐可用（多服运营下避免 A 服的券在 B 服用）
    realm_id = Column(Integer, ForeignKey('server_realms.id'), nullable=True)
    max_uses = Column(Integer, default=0)      # 总次数上限，0 = 不限
    use_count = Column(Integer, default=0)     # 当前占用（reserved + consumed）
    per_user_limit = Column(Integer, default=1)  # 每人最多用几次，0 = 不限
    valid_from = Column(DateTime)
    valid_until = Column(DateTime)
    is_active = Column(Boolean, default=True)
    note = Column(String(255))
    created_by = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    realm = relationship("ServerRealm")


class CouponUsage(Base):
    """优惠券核销记录：一笔优惠订单一行（预订 → 消费 / 释放）

    `paid_amount` / `list_price` 快照下来：套餐改价后，历史订单仍要能解释「当时到底按多少钱算的」。
    """
    __tablename__ = 'coupon_usages'

    __table_args__ = (
        UniqueConstraint('order_id', name='uq_coupon_usage_order'),
        Index('idx_coupon_usage_user', 'user_id'),
        Index('idx_coupon_usage_coupon', 'coupon_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    coupon_id = Column(Integer, ForeignKey('coupon_codes.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    order_id = Column(String(64), nullable=False)
    kind = Column(String(20))
    # reserved 占了额度但还没付 / consumed 已付款 / released 订单关掉或退款、额度已还
    status = Column(String(20), default='reserved')
    list_price = Column(Numeric(10, 2), default=0)
    discount_amount = Column(Numeric(10, 2), default=0)
    paid_amount = Column(Numeric(10, 2), default=0)
    created_at = Column(DateTime, default=datetime.now)
    closed_at = Column(DateTime)

    coupon = relationship("CouponCode")
    user = relationship("WebUser")


# ==================== 求片系统 ====================

class MovieRequest(Base):
    """求片请求表"""
    __tablename__ = 'movie_requests'

    __table_args__ = (
        Index('idx_req_user', 'user_id'),
        Index('idx_req_status', 'status'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    movie_name = Column(String(255), nullable=False)
    year = Column(String(10))
    type = Column(String(50))
    # TMDB 候选带过来的 id：管理端「标记已入库」按它精确匹配（手填片名时为空）
    tmdb_id = Column(String(20))
    # 剧集按整季申请：存 "1,2" 或 "all"（全季）；电影/其它类型为空
    season = Column(String(50))
    note = Column(Text)
    status = Column(String(20), default='pending')
    admin_note = Column(Text)
    emby_item_id = Column(String(100))
    # 求的是哪个服的片：入库后进的是该服的媒体库
    realm_id = Column(Integer, ForeignKey('server_realms.id'), nullable=True)
    # 转交外部服务的结果：moviepilot（已提交订阅）/ qbittorrent（已交给下载器加种）
    push_target = Column(String(20))
    push_status = Column(String(20))  # ok / failed
    push_message = Column(String(300))
    pushed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    # v2 求片附议数：用户可对 pending 的求片附议（+1），管理端按热度排序
    vote_count = Column(Integer, default=0, nullable=False)
    # 选片投愿（2026-11-09）：原名投下（没绑 TMDB 候选）的兜底行标记。
    # 这类行只有片名、对不上号，须人工确认后才处理，管理端据此区分；
    # 选片行（带 tmdb_id）恒为 False。老库补列默认 0（见 database.py）。
    title_only = Column(Boolean, default=False, nullable=False, server_default="0")
    # 缺集全套（2026-11-10）：求片分类——seek＝整片求片（存量与
    # 默认）；missing_episodes＝补缺集求片（剧已在库、只缺若干集，
    # 由许愿池「求补这些集」落单，仍走求片主口的额度/附议/完成
    # 通知同一套）。老库补列默认 'seek'（见 database.py）。
    request_kind = Column(String(20), default="seek", nullable=False,
                          server_default="seek")
    # 补缺集求片的缺集明细快照 JSON（结构见 backend/missing_episodes
    # 的 build_request_detail）；整片求片恒为空。工人核算发现部分
    # 补齐时只刷新这份快照、不发通知；全部补齐才置 completed。
    request_detail = Column(Text)

    user = relationship("WebUser")


class MovieRequestVote(Base):
    """求片附议表（v2）：一个用户对一条求片只能附议一次"""
    __tablename__ = 'movie_request_votes'

    __table_args__ = (
        UniqueConstraint('request_id', 'user_id', name='uq_vote_request_user'),
        Index('idx_vote_request', 'request_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(Integer, ForeignKey('movie_requests.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    # Step 1 加权附议：投票时刻投票人的等级权重快照（Lv.1–Lv.6 → 1–6，封顶 6；
    # 仅公益服按等级，付费服恒为 1）。升降级不追溯历史票；老库补列默认 1
    # （历史票权重，与升级前“每票恒＝1”一致）。聚合时空值按 1 计。
    weight_snapshot = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class GovernanceReport(Base):
    """治理举报（等级公益服活动治理 Step 2：举报＋复核＋管理员终审）。

    v1 仅针对求片条目（target_type='media_seek'，target_id=MovieRequest.id）。
    状态机：pending（待复核）→ escalated（已升级终审队列）
           → accepted（终审受理）/ rejected（终审驳回）；
           复核不同意达 quorum＋阈值时 → review_rejected（复核关闭，不进终审，
           为避免无结论报告永久挂起而设的对称出口，设计稿未明文，PR body 有说明）。
    举报行不删改求片本身；终审受理也不自动隐藏／删除求片（v1 必须人工终审，
    自动处理误伤不可逆，处理动作由管理员另行决定）。
    同一用户对同一对象终身只能举报 1 次（uq 约束）。
    终审结果通过列表／详情接口对举报人与复核员可见（v1 不另建推送通道）。
    """
    __tablename__ = 'governance_reports'

    __table_args__ = (
        UniqueConstraint('reporter_id', 'target_type', 'target_id',
                         name='uq_gov_report_reporter_target'),
        Index('idx_gov_report_status', 'status'),
        Index('idx_gov_report_target', 'target_type', 'target_id'),
        Index('idx_gov_report_reporter', 'reporter_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    reporter_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    target_type = Column(String(20), nullable=False, default='media_seek')
    target_id = Column(Integer, nullable=False)
    reason = Column(String(20), nullable=False)  # spam / duplicate / violation
    detail = Column(Text)
    status = Column(String(20), default='pending', nullable=False)
    admin_note = Column(Text)
    admin_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    escalated_at = Column(DateTime)
    decided_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class GovernanceReviewVote(Base):
    """复核票：复核员对举报行的表决，权重为投票时刻等级快照，不随升降级漂移。"""
    __tablename__ = 'governance_review_votes'

    __table_args__ = (
        UniqueConstraint('report_id', 'reviewer_id',
                         name='uq_gov_review_report_reviewer'),
        Index('idx_gov_review_report', 'report_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(Integer, ForeignKey('governance_reports.id'), nullable=False)
    reviewer_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    decision = Column(String(20), nullable=False)  # agree / disagree
    weight = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.now)


class GovernanceSuspension(Base):
    """治理停权：冻结治理特权（举报／复核），不扣经验、不降级。

    生效 ⟺ revoked_at IS NULL AND now < ends_at。
    自动停权（reason='auto_abuse'）由终审驳回计数触发；手动停权
    （reason='manual'）由管理员操作，可撤销（revoked_at）。
    降级（扣经验）不在本表：仅管理员手动走 add_xp 负数，另案工具。
    """
    __tablename__ = 'governance_suspensions'

    __table_args__ = (
        Index('idx_gov_susp_user', 'user_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    reason = Column(String(20), nullable=False)  # auto_abuse / manual
    note = Column(Text)
    created_by_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    starts_at = Column(DateTime, default=datetime.now, nullable=False)
    ends_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime)
    revoked_by_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


# ==================== 系统监控 ====================

class SystemMetric(Base):
    """系统指标表"""
    __tablename__ = 'system_metrics'

    __table_args__ = (
        Index('idx_metric_name', 'metric_name'),
        Index('idx_metric_time', 'timestamp'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_name = Column(String(100), nullable=False)
    metric_value = Column(Float)
    tags = Column(JSON)  # 标签，如 {server_id: 1}
    timestamp = Column(DateTime, default=datetime.now)


class AlertRule(Base):
    """告警规则表

    两种规则形态（二选一）：
    - 事件型（event_type 非空）：系统事件告警（probe_queue_high / enrich_stalled /
      disk_full / sa_quota_low / scan_failed），由 backend/system_alerts.py 的
      定时检查触发。阈值/渠道/接收人/冷却全部可配。
    - 指标型（event_type 为空，metric_name 非空）：遗留的通用指标告警形态。
    """
    __tablename__ = 'alert_rules'

    # R16 P2-2：event_type 唯一（PG/SQLite 的 UNIQUE 都允许多个 NULL，
    # legacy 指标型规则 event_type 为空不受影响）；防 ensure_default_rules
    # "先查后插"竞态下播种出重复规则
    __table_args__ = (
        UniqueConstraint("event_type", name="uq_alert_rules_event_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    metric_name = Column(String(100), nullable=False)
    condition = Column(String(20))  # gt, lt, eq
    threshold = Column(Float)
    severity = Column(String(20), default='warning')  # info, warning, critical
    notification_channels = Column(JSON)  # ['telegram', 'email']
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    # --- 系统告警（事件型）扩展列，见 backend/system_alerts.py ---
    # 事件类型：probe_queue_high / enrich_stalled / disk_full / sa_quota_low /
    # scan_failed / api_error_high；为空表示 legacy 指标型规则
    event_type = Column(String(50), nullable=True)
    # 接收人 JSON：{"staff": true}（默认，全部启用中的管理员）或
    # {"user_ids": [1, 2]}（指定用户）
    recipients = Column(Text, nullable=True)
    # 同一规则冷却秒数：冷却期内重复越线不重复打扰（防轰炸）
    cooldown_sec = Column(Integer, default=3600)


class Alert(Base):
    """告警记录表"""
    __tablename__ = 'alerts'

    __table_args__ = (
        Index('idx_alert_rule', 'rule_id'),
        Index('idx_alert_status', 'status'),
        Index('idx_alert_time', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey('alert_rules.id'))
    severity = Column(String(20), default='warning')
    title = Column(String(255))
    message = Column(Text)
    status = Column(String(20), default='open')  # open, acknowledged, resolved
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    # R16 P1-2：是否至少一个渠道送达。去重只认 notified_ok=True 的记录，
    # 发送失败的不进入冷却，下轮继续重试（一次投递失败不再导致整冷却期失声）
    notified_ok = Column(Boolean, default=False)
    # R16 P2-3：管理员确认的时间（ack_alert 写入，供审计"谁何时确认的"）
    acknowledged_at = Column(DateTime, nullable=True)

    rule = relationship("AlertRule")


# ==================== 导出所有模型 ====================
__all__ = [
    # 系统管理
    "AdminUser", "AdminRole", "AdminLog", "SystemConfig", "NotificationHistory", "StationMessage",
    # 多服运营
    "ServerRealm",
    # 用户
    "WebUser",
    # 订阅和支付
    "SubscriptionPlan", "UserSubscription", "SubscriptionReminder",
    "SubscriptionOrder",
    # Emby
    "EmbyServer", "PlanServerRelation", "UserEmbyAccount", "EmbySession", "MovieBookmark",
    # 工单
    "Ticket", "TicketMessage",
    # 公告和活动
    "Announcement", "ThemeActivity", "ThemeActivityProgress",
    # 经济系统
    "ExchangeCode",
    # 邀请
    "InvitationCode", "InvitationRecord",
    # 优惠券
    "CouponCode", "CouponUsage",
    # 求片
    "MovieRequest",
    # 监控
    "SystemMetric", "AlertRule", "Alert",
]


# ==================== 娱乐板块（公益服模块3） ====================

class MediaRequest(Base):
    """求片请求表（用户端娱乐板块）

    .. deprecated::
        求片已统一走 movie_requests（MediaSeek 流程）。本表不再写入，
        保留模型仅为兼容历史数据。
    """
    __tablename__ = 'media_requests'

    __table_args__ = (
        Index('idx_mreq_user', 'user_id'),
        Index('idx_mreq_status', 'status'),
        Index('idx_mreq_tmdb', 'tmdb_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    tmdb_id = Column(String(20), nullable=False)
    media_type = Column(String(10), nullable=False, default='movie')  # movie / tv
    title = Column(String(255), nullable=False)
    status = Column(String(20), default='pending')  # pending/approved/rejected/done
    admin_note = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    user = relationship("WebUser")


class LotteryRound(Base):
    """群抽奖活动轮次：一期抽奖"""
    __tablename__ = 'lottery_rounds'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    chat_id = Column(BigInteger, nullable=False)  # 群组 chat_id
    status = Column(String(20), nullable=False, default='open')  # open/drawing/done/cancelled
    start_at = Column(DateTime, default=datetime.now)
    draw_at = Column(DateTime, nullable=True)      # 计划开奖时间，空=手动开奖
    drawn_at = Column(DateTime, nullable=True)
    drawing_at = Column(DateTime, nullable=True)  # 认领开奖时间戳：drawing 僵尸回收用
    max_participants = Column(Integer, nullable=False, default=0)  # 0=不限
    # 抽奖类型：button=按钮抽奖 / password=口令抽奖
    lottery_type = Column(String(20), nullable=False, default='button', server_default='button')
    password_keyword = Column(String(100), nullable=True)  # 口令抽奖时的关键词，button 类型时为 NULL
    seed_hash = Column(String(64), nullable=True)  # 开奖前公布的承诺
    seed = Column(String(64), nullable=True)       # 开奖后揭示
    # 三重门·公信门：drand 公开随机信标（开奖时抓取，与 seed 混合成最终种子）
    drand_round = Column(BigInteger, nullable=True)      # drand 信标轮次
    drand_randomness = Column(String(128), nullable=True)  # drand 信标随机值（hex）
    # 公告落库（2026-10-31 根治）：公告消息编号/发送状态/失败原因/是否置顶，
    # 没有编号就无法置顶、无法编辑人数、失败也无从补发（round 4 实证）。
    announce_message_id = Column(BigInteger, nullable=True)  # 群公告 message_id
    announce_status = Column(String(20), nullable=True)      # sent/failed；NULL=未发送
    announce_error = Column(String(500), nullable=True)      # 最近一次发送失败原因
    announce_pinned = Column(Boolean, nullable=False, default=False, server_default='0')
    announce_last_edit_at = Column(DateTime, nullable=True)  # 人数编辑节流时间戳
    # 人数提醒防重（worker 轮询）：已提醒到的最高里程碑人数 / 开奖前提醒已发
    reminder_milestone = Column(Integer, nullable=False, default=0, server_default='0')
    final_call_sent = Column(Boolean, nullable=False, default=False, server_default='0')
    created_by = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class LotteryRoundPrize(Base):
    """群抽奖奖品：挂在某一轮下面"""
    __tablename__ = 'lottery_round_prizes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey('lottery_rounds.id', ondelete='CASCADE'), nullable=False)
    name = Column(String(100), nullable=False)
    type = Column(String(20), nullable=False)  # days=公益天数 / points=积分 / whitelist=白名单(永久)
    value = Column(Integer, nullable=False, default=0)
    quantity = Column(Integer, nullable=False, default=1)
    sort = Column(Integer, nullable=False, default=0)


class LotteryRoundEntry(Base):
    """群抽奖参与记录：一人一期只能参加一次"""
    __tablename__ = 'lottery_round_entries'

    __table_args__ = (
        UniqueConstraint('round_id', 'user_id', name='uq_lottery_round_entry'),
        Index('idx_lottery_entry_round', 'round_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey('lottery_rounds.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    telegram_id = Column(BigInteger, nullable=False)
    joined_at = Column(DateTime, default=datetime.now)


class LotteryRoundWinner(Base):
    """群抽奖中奖记录"""
    __tablename__ = 'lottery_round_winners'

    id = Column(Integer, primary_key=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey('lottery_rounds.id', ondelete='CASCADE'), nullable=False)
    entry_id = Column(Integer, ForeignKey('lottery_round_entries.id'), nullable=False)
    prize_id = Column(Integer, ForeignKey('lottery_round_prizes.id'), nullable=False)
    distributed = Column(Boolean, nullable=False, default=False, server_default='0')
    distributed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class LotteryActivityProposal(Base):
    """等级治理 Step 3：用户活动提案。

    Lv.2+ 用户提交活动提案，管理员批准后才建轮次、拒绝则不产生任何东西。
    本行本身就是审计记录：谁（user_id）、何时（created_at / reviewed_at）、
    什么活动（title / chat_id / prizes_json / draw_at）、谁审批（reviewer_id）、
    结果（status / review_note / round_id）。提案动作不产生经验。
    新表由 init_db 的 create_all 建出，无需迁移文件（与 DriveOAuthState 同口径）。
    """
    __tablename__ = 'lottery_activity_proposals'

    __table_args__ = (
        Index('idx_lottery_proposal_user_created', 'user_id', 'created_at'),
        Index('idx_lottery_proposal_status', 'status'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id', ondelete='CASCADE'), nullable=False)
    title = Column(String(200), nullable=False)
    # 群组 chat_id：与 lottery_rounds.chat_id 同为 BigInteger（TG 群 id 为整数）。
    chat_id = Column(BigInteger, nullable=False)
    lottery_type = Column(String(20), nullable=False, default='button')
    password_keyword = Column(String(100), nullable=True)
    # 奖品清单 JSON：[{name, type(days/points/whitelist), value, quantity}]，与
    # 管理端 RoundCreateRequest.PrizeItem 同形，批准时原样交给 lottery.create_round。
    prizes_json = Column(Text, nullable=False, default='[]')
    draw_at = Column(DateTime, nullable=True)
    max_participants = Column(Integer, nullable=False, default=0)
    # 提交时刻的等级快照：审批与审计以它为准，不随用户后续升级/降级回改。
    level_snapshot = Column(Integer, nullable=False)
    # pending / approved / rejected
    status = Column(String(20), nullable=False, default='pending')
    reviewer_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(String(500), nullable=True)
    round_id = Column(Integer, ForeignKey('lottery_rounds.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class LotteryDirectLaunch(Base):
    """等级治理 Step 3：用户直接发起活动记录（事后审计）。

    Lv.5+ 用户不经审批直接建轮次；本行记录谁、何时、建了哪个轮次，
    管理员事后可凭它审计，滥用时用现有的取消端点（/welfare/lottery/rounds/{id}/cancel）
    取消轮次。直接发起动作不产生经验。
    """
    __tablename__ = 'lottery_direct_launches'

    __table_args__ = (
        Index('idx_lottery_direct_user_created', 'user_id', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id', ondelete='CASCADE'), nullable=False)
    round_id = Column(Integer, ForeignKey('lottery_rounds.id', ondelete='SET NULL'), nullable=True)
    level_snapshot = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class LotteryBlacklist(Base):
    """群抽奖黑名单：三重门·资格门。

    被拉黑的用户无法参加任何群抽奖。由管理后台维护。
    """
    __tablename__ = 'lottery_blacklist'

    __table_args__ = (
        UniqueConstraint('user_id', name='uq_lottery_blacklist_user'),
        Index('idx_lottery_blacklist_user', 'user_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id', ondelete='CASCADE'), nullable=False)
    reason = Column(String(200), nullable=True)  # 拉黑原因
    created_by = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class Review(Base):
    """影评表"""
    __tablename__ = 'reviews'

    __table_args__ = (
        Index('idx_review_guid', 'item_guid'),
        Index('idx_review_user', 'user_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    item_guid = Column(String(64), nullable=False)
    rating = Column(Integer, nullable=False, default=5)  # 1-5
    content = Column(Text, nullable=False)
    likes = Column(Integer, default=0, nullable=False, server_default="0")
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("WebUser")


class GovernanceAuditLog(Base):
    """治理审计日志（等级公益服活动治理 Step 4）

    只追加、不改不删：治理动作（停权／解封／手动降级，Step 1–3 的附议／
    举报／复核／提案／发起后续也经 ``backend.governance.record_governance_audit``
    写这里）的时间、操作人、对象与结果。Lv.6 与管理员只读可见。
    """

    __tablename__ = 'governance_audit_logs'

    __table_args__ = (
        Index('idx_gov_audit_created', 'created_at'),
        Index('idx_gov_audit_actor', 'actor_user_id'),
        Index('idx_gov_audit_target', 'target_user_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(40), nullable=False)  # freeze/release/downgrade/vote/report/review/propose/launch…
    actor_user_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)  # 操作人，空=系统
    target_user_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)  # 被作用的用户，空=非针对人
    target_ref = Column(String(100), nullable=True)  # 关联对象，如 seek:123 / lottery:5
    detail = Column(Text, nullable=True)  # JSON 文本：理由、数值、等级前后等
    created_at = Column(DateTime, default=datetime.now)


class GovernanceFreeze(Base):
    """治理停权记录（等级公益服活动治理 Step 4）

    管理员冻结指定用户的治理特权（权重／提案／复核／举报），保留基础附议
    1 票与等级展示，不影响播放／查看／基础求片额度，不扣经验。
    一行即一次停权：released_at 为空且未过期 = 生效中；解封或过期即失效，
    历史行保留备查。Step 1–3 的治理入口以 ``is_governance_frozen`` 判定。
    """

    __tablename__ = 'governance_freezes'

    __table_args__ = (
        Index('idx_gov_freeze_user', 'user_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False, index=True)
    reason = Column(String(200), nullable=False)  # 停权理由，必填
    expires_at = Column(DateTime, nullable=True)  # 空=不限期，需管理员手动解封
    created_by = Column(Integer, ForeignKey('web_users.id'), nullable=False)  # 操作管理员
    created_at = Column(DateTime, default=datetime.now)
    released_at = Column(DateTime, nullable=True)
    released_by = Column(Integer, ForeignKey('web_users.id'), nullable=True)


class TgBindCode(Base):
    """TG 绑定码表（B1：bot 绑定基础设施）

    支持两种绑定流程：
    - 网页发起：user_id 已填，telegram_id 为空 → 用户给 bot 发码验证
    - Bot 发起：telegram_id 已填，user_id 为空 → 用户在网页输入码验证
    """
    __tablename__ = 'tg_bind_codes'

    __table_args__ = (
        Index('idx_tgbind_code', 'code'),
        Index('idx_tgbind_user', 'user_id'),
        Index('idx_tgbind_tgid', 'telegram_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=True, index=True)
    telegram_id = Column(BigInteger, nullable=True, index=True)
    code = Column(String(6), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    # 本码生效期间全站累计的「猜错绑定码」次数；达到上限即作废（防多 TG 账号分布式枚举）
    fail_count = Column(Integer, default=0)


class TgUpdateDigestSent(Base):
    """TG「更新入库」播报已播记录（防重复播报，tg_bot.updatedigest 专用）。

    item_id = emby_items 行 id：电影记顶层行 id，新集记集行 id。
    kind：movie / episode = 已实际播报；baseline = 上线首跑的基线种子
    （功能上线前已在货架上的条目不翻旧账，只播上线后新入库的）。
    """
    __tablename__ = 'tg_update_digest_sent'

    __table_args__ = (
        UniqueConstraint('item_id', name='uq_tg_digest_sent_item'),
        Index('idx_tg_digest_sent_kind', 'kind'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, nullable=False, index=True)
    kind = Column(String(16), nullable=False, default='movie')
    sent_at = Column(DateTime, default=datetime.now)


class TgProactiveNotice(Base):
    """Telegram 主动私聊提醒的已处理记录（求片到货 / 转存终态）。

    一行 = 一个（事件, 用户）。唯一约束挡住重复扫描、重复回调、
    多进程和重启后再插一行。状态：
    - pending：已入队，等合并窗口到点再发；
    - inflight：某个进程已领走、正在发；
    - sent：已发出；
    - blocked：对方屏蔽了机器人或聊不了，不再重试；
    - gave_up：可重试的失败用完次数，不再重试；
    - muted：发送时用户关了「求片到货类」TG 偏好，静默丢弃、不补发。
    开关关着时不写本表。新表由 init_db 的 create_all 建出，
    不走补列（老库升级同样靠 create_all 补新表）。
    """

    __tablename__ = "tg_proactive_notices"

    __table_args__ = (
        UniqueConstraint(
            "kind", "subject_id", "user_id",
            name="uq_tg_proactive_notice",
        ),
        Index("ix_tg_proactive_user_status", "user_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # store_arrival / transfer_ok / transfer_fail
    kind = Column(String(32), nullable=False)
    # 求片行 id 或转存任务 id
    subject_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False, default="")
    # 只存已经说成人话的失败原因；成功/到货为空。不存异常、路径、凭据。
    detail = Column(String(200), nullable=False, default="")
    status = Column(String(20), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class RevokedJwt(Base):
    """JWT 吊销表（P2 修复：jti 生成了但从未校验/存储，无吊销能力）。

    登出 / 改密码 / 管理员踢下线时把 jti 写进来；鉴权时查表，已吊销则 401。
    expires_at 过期后由定时任务清理（token 本来也过期了，留着无意义）。
    """
    __tablename__ = 'revoked_jwt'

    jti = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False, index=True)
    reason = Column(String(50), nullable=False, default="logout")
    revoked_at = Column(DateTime, default=datetime.now, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)


class ApiKey(Base):
    """API Key / Service Token 表（服务端对服务端认证）

    - 明文格式：``ak_`` + 48 位 URL 安全随机串，创建时返回一次，库里只存
      ``SHA256(明文 + pepper)``（见 backend/api_key.py）
    - ``key_id`` 是明文前 12 字符的公开标识，可安全展示、作查询前缀
    - scopes：['read', 'write', 'admin']，层级 admin > write > read
    """
    __tablename__ = 'api_keys'

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_id = Column(String(32), unique=True, nullable=False, index=True)
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    scopes = Column(JSON, default=list)
    expires_at = Column(DateTime, nullable=True)  # None = 永不过期
    is_active = Column(Boolean, default=True, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey('web_users.id'), nullable=True)  # 创建人 admin id
    created_at = Column(DateTime, default=datetime.now, nullable=False)


# ==================== 账号安全三件套（TOTP / Passkey / 平台 API Key） ====================
# 逻辑在 backend/account_security.py，端点在 backend/api/account_security.py。
# 三张都是新表（create_all 建），不给 web_users 加列、不动服务间 api_keys 表。

class UserTotp(Base):
    """用户 TOTP 动态验证码（每人至多一行）。

    - ``secret_sealed`` 只存 Fernet 密封值（密钥派生自 SECRET_KEY），明文密钥永不落库；
    - ``enabled=False`` = 已生成待确认（setup 已发密钥、用户还没输对一次码），
      登录校验只认 enabled=True 的行；
    - ``last_used_counter`` 记最近一次登录成功用掉的计数器步，防同一码在
      有效窗口内被重放。
    """
    __tablename__ = 'user_totp'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), unique=True, nullable=False, index=True)
    secret_sealed = Column(Text, nullable=False)
    enabled = Column(Boolean, default=False, nullable=False)
    last_used_counter = Column(BigInteger, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class UserPasskey(Base):
    """用户 Passkey（WebAuthn 凭据，每人多枚，上限走配置 passkey_max_per_user）。

    - ``credential_id`` 为 base64url 文本（公开标识，唯一索引）；
    - ``public_key`` 为 COSE 公钥字节的 base64url 文本（公钥非机密，可明文存）；
    - ``sign_count`` 每次断言校验后回写，防凭据克隆。
    """
    __tablename__ = 'user_passkeys'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False, index=True)
    credential_id = Column(String(512), unique=True, nullable=False, index=True)
    public_key = Column(Text, nullable=False)
    sign_count = Column(BigInteger, default=0, nullable=False)
    transports = Column(String(255), nullable=True)  # 逗号分隔，如 internal,hybrid
    device_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    last_used_at = Column(DateTime, nullable=True)


class UserPlatformApiKey(Base):
    """用户平台 API Key（用户在账号安全页自助创建，与服务间 api_keys 分域）。

    - 明文格式 ``pak_`` + 随机串，只在创建时返回一次，库里只存带 pepper 的
      SHA256（见 backend/account_security.py，与 api_key.py 同派生口径、不同域标签）；
    - ``key_id`` 是明文前 12 字符的公开标识；
    - 创建前置：账号已启用 TOTP 或已绑 Passkey（端点硬校验）；
    - 到期固定按配置 platform_api_key_validity_days（默认 365 天）。
    """
    __tablename__ = 'user_platform_api_keys'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False, index=True)
    key_id = Column(String(32), unique=True, nullable=False, index=True)
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


# ==================== v4 探险家日志面板 ====================

class PanelActivity(Base):
    """后台活动 feed（供面板脉搏文案 + 发现页实时动态流）。

    保留策略见 backend/panel_feed.py：每服分区最新 500 条，写入时裁剪。
    """
    __tablename__ = 'panel_activity'

    __table_args__ = (
        Index('idx_panel_activity_time', 'created_at'),
        Index('idx_panel_activity_realm_time', 'realm_id', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(String(300), nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    # 事件类型：scan / enrich / play / system（GET /api/panel/feed 的 type 字段）
    event_type = Column(String(20), default='system', nullable=False)
    # 库名标签（系统事件可为 NULL）
    library = Column(String(200))
    # 归属服（NULL = 全局事件，所有服可见）
    realm_id = Column(Integer)


class Team(Base):
    """团队（照 UHDNow 团队形态：团长建团、邀请码拉人、成员共享团队套餐）。

    套餐不在建团时生效：走 TeamApplication 申请→管理端审核开通，
    通过后把 plan_* 写到团队行（plan_status='active'）。
    plan_traffic_bytes 只是套餐登记值——本站没有流量额度模型，
    流量计量/扣减未接入，登记值供页面展示与后续计量接入时读取。
    """
    __tablename__ = 'teams'

    __table_args__ = (
        Index('idx_team_owner', 'owner_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    owner_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    invite_code = Column(String(20), unique=True, nullable=False, index=True)
    status = Column(String(20), default='active', nullable=False)  # active / dissolved
    # 团队套餐（审核通过后写入；none=未开通 pending=审核中 active=已开通）
    plan_status = Column(String(20), default='none', nullable=False)
    plan_line = Column(String(100), nullable=True)
    plan_traffic_bytes = Column(BigInteger, nullable=True)
    plan_member_limit = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    owner = relationship("WebUser", foreign_keys=[owner_id])


class TeamMember(Base):
    """团队成员（一人同时只能在一个团队：user_id 唯一索引兜底）"""
    __tablename__ = 'team_members'

    __table_args__ = (
        Index('idx_team_member_user', 'user_id', unique=True),
        Index('idx_team_member_team', 'team_id'),
        UniqueConstraint('team_id', 'user_id', name='uq_team_member_pair'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    role = Column(String(20), default='member', nullable=False)  # owner / member
    joined_at = Column(DateTime, default=datetime.now)

    team = relationship("Team")
    user = relationship("WebUser")


class TeamApplication(Base):
    """团队套餐申请意向单（用户提交 → 管理端审核 → 通过即开通）。

    team_id 为空 = 申请人还没有团队，通过时按 team_name 新建团队并设其为团长；
    非空 = 给已有团队开通/变更套餐。总流量与子账号数在提交时按 SystemConfig
    （team 家族）校验起订与上限，审核通过后原样落到团队 plan_* 字段。
    本单不接支付：approved 只代表套餐开通登记，付款走客服人工流程
    （与友站「下单前联系客服审核、买后联系客服开通」的人工形态一致）。
    """
    __tablename__ = 'team_applications'

    __table_args__ = (
        Index('idx_team_app_user', 'user_id'),
        Index('idx_team_app_status', 'status'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('web_users.id'), nullable=False)
    team_id = Column(Integer, ForeignKey('teams.id'), nullable=True)
    team_name = Column(String(100), nullable=True)
    line = Column(String(100), nullable=True)
    traffic_bytes = Column(BigInteger, nullable=False)
    member_limit = Column(Integer, nullable=False)
    note = Column(Text, default="")
    status = Column(String(20), default='pending', nullable=False)  # pending / approved / rejected
    reviewer_id = Column(Integer, ForeignKey('web_users.id'), nullable=True)
    review_note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    reviewed_at = Column(DateTime, nullable=True)

    applicant = relationship("WebUser", foreign_keys=[user_id])
