#!/bin/bash
# ==============================================================================
# Aetrix Portal 密钥轮换脚本
# ==============================================================================
# 用途: 轮换真正生效的密钥（SECRET_KEY、PostgreSQL 密码）
# 使用: ./rotate-secrets.sh [jwt|user|postgres|all]
#
# A-069（360° 审计）：旧版还轮换 JWT_SECRET_KEY / REDIS_PASSWORD /
# CRYPTO_KEY——三键全仓零读取（会话只认 SECRET_KEY；凭据密封键由
# SECRET_KEY 派生，见 backend/emby_server/secret_sealing.py；Redis 走
# 无密码的 REDIS_URL），轮换纯属自欺，jwt 项还谎称「所有现有会话将
# 失效」。三项已删除；redis / crypto 子命令只打印说明并响亮退出。
# ==============================================================================

set -e

# 容器 / 库 / 账号名跟着项目品牌统一，但**全部可用环境变量覆盖**：
# 沿用旧栈命名（改名前的容器 / 库 / 账号）的部署，设一下这三个就能继续用。
# 默认容器名与现行 docker-compose 的 container_name 对齐（A-070：旧默认
# 是改名前的下划线名，与现行连字符名对不上、ALTER 永远打空）。
DB_CONTAINER="${DB_CONTAINER:-aetrix-postgres}"
DB_USER="${DB_USER:-aetrix}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 生成强密钥的函数
generate_strong_secret() {
    openssl rand -base64 64 | tr -d '=+/' | cut -c1-64
}

# 备份 .env
backup_env() {
    BACKUP_FILE=".env.backup.$(date +%Y%m%d_%H%M%S)"
    cp .env "$BACKUP_FILE"
    echo -e "${GREEN}✓ 备份到 $BACKUP_FILE${NC}"
}

# 轮换 SECRET_KEY（会话与凭据密封的根密钥；jwt 与 user 是同一动作的两个名字）
rotate_user_secret() {
    echo -e "${YELLOW}轮换 SECRET_KEY...${NC}"
    NEW_SECRET=$(generate_strong_secret)
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$NEW_SECRET/" .env
    echo -e "${GREEN}✓ SECRET_KEY 已轮换${NC}"
    echo -e "${RED}⚠️  所有现有会话将失效（须重新登录）！${NC}"
    echo -e "${RED}⚠️  凭据密封键由 SECRET_KEY 派生：已密封的凭据（网盘令牌、TOTP 等）将解不开，须管理员重录！${NC}"
}

# 轮换 PostgreSQL 密码（A-070）：先在库里 ALTER 成功、再改 .env、最后才报
# 成功。旧实现默认容器名对不上现行名、ALTER 又被吞错，.env 已改写却仍报
# 成功——库内与 .env 脱节比不轮换更危险。
rotate_postgres_password() {
    echo -e "${YELLOW}轮换 PostgreSQL 密码...${NC}"
    NEW_PASSWORD=$(generate_strong_secret)

    # 容器必须真在运行（按名精确查，不用 docker ps 子串猜）
    if [ "$(docker inspect -f '{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null)" != "true" ]; then
        echo -e "${RED}✗ PostgreSQL 容器 $DB_CONTAINER 未运行，已中止：.env 未改动。请先启动容器，或用 DB_CONTAINER 指定现行容器名${NC}" >&2
        return 1
    fi

    # 先改库：失败响亮退出（不吞 stderr、不加 || true），此时 .env 还没动
    if ! docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -v ON_ERROR_STOP=1 -c "ALTER USER $DB_USER WITH PASSWORD '$NEW_PASSWORD';"; then
        echo -e "${RED}✗ PostgreSQL 密码在库内修改失败，已中止：.env 未改动${NC}" >&2
        return 1
    fi

    # 库内已改，再对齐 .env 并核对真的写进去了
    sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$NEW_PASSWORD/" .env
    if ! grep -q "^POSTGRES_PASSWORD=$NEW_PASSWORD" .env; then
        echo -e "${RED}✗ .env 里没有 POSTGRES_PASSWORD 行可改（注意：库内密码已变更），请手动在 .env 补上新密码${NC}" >&2
        return 1
    fi
    echo -e "${GREEN}✓ PostgreSQL 密码已轮换（库内与 .env 一致）${NC}"
}

# 显示帮助
show_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  jwt / user  轮换 SECRET_KEY（所有会话失效；密封凭据须重录）"
    echo "  postgres    轮换 PostgreSQL 密码"
    echo "  all         轮换以上全部"
    echo "  help        显示此帮助"
    echo ""
    echo "已删除的轮换项（对应键无任何读取方，见脚本头注 A-069）："
    echo "  redis       无可轮换的 Redis 密码（Redis 走无密码 REDIS_URL）"
    echo "  crypto      无独立加密密钥（密封键由 SECRET_KEY 派生，用 jwt 轮换）"
    echo ""
    echo "示例:"
    echo "  $0 jwt           # 只轮换 SECRET_KEY"
    echo "  $0 all           # 轮换全部生效密钥"
}

# 主函数
case "${1:-help}" in
    jwt|user)
        backup_env
        rotate_user_secret
        echo ""
        echo -e "${YELLOW}请重启 EM / EA（两边必须用同一个 SECRET_KEY）：${NC}"
        echo "  sudo systemctl restart aetrix-em aetrix-ea"
        ;;
    postgres)
        backup_env
        rotate_postgres_password
        echo ""
        echo -e "${YELLOW}请重启所有服务（PostgreSQL + EM + EA）：${NC}"
        echo "  sudo systemctl restart postgresql aetrix-em aetrix-ea"
        ;;
    redis)
        echo -e "${RED}✗ 没有可轮换的 Redis 密码：该键全仓无读取方，本部署 Redis 走无密码 REDIS_URL（内网）。此轮换项已随 A-069 删除${NC}" >&2
        exit 1
        ;;
    crypto)
        echo -e "${RED}✗ 没有独立的加密密钥可轮换：该键全仓无读取方，凭据密封键由 SECRET_KEY 派生——轮换请用 jwt（注意旧密封凭据将解不开）。此轮换项已随 A-069 删除${NC}" >&2
        exit 1
        ;;
    all)
        backup_env
        echo -e "${RED}═══════════════════════════════════════════════════════════════════${NC}"
        echo -e "${RED}⚠️  警告：轮换所有密钥将导致所有服务中断！${NC}"
        echo -e "${RED}═══════════════════════════════════════════════════════════════════${NC}"
        read -p "确认继续? (yes/no): " confirm
        if [ "$confirm" = "yes" ]; then
            rotate_user_secret
            rotate_postgres_password
            echo ""
            echo -e "${GREEN}✓ 所有密钥已轮换，请重启所有服务${NC}"
        else
            echo -e "${YELLOW}已取消${NC}"
        fi
        ;;
    *)
        show_help
        ;;
esac
