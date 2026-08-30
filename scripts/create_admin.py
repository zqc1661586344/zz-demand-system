#!/usr/bin/env python3
"""一次性脚本：在生产环境中创建首个管理员账号。

使用方式：
    # 交互式输入用户名和密码（密码不回显）
    python scripts/create_admin.py

    # 通过环境变量传入（适合 CI/CD 或自动化部署）
    ADMIN_USERNAME=admin ADMIN_PASSWORD='YourStrongP@ss1' python scripts/create_admin.py

    # 指定用户名 + 交互式输入密码
    ADMIN_USERNAME=admin python scripts/create_admin.py

设计要点：
    - 不经过 HTTP，直接写库，绕过前端和 API 层
    - 密码强度校验（≥8 位，含大小写 + 数字）
    - 自动分配 admin 角色 + is_superuser=True
    - 用户名已存在时拒绝覆盖（幂等安全）
    - 运行后建议立即修改默认密码
"""

import getpass
import os
import re
import sys

# 确保项目根目录在 sys.path 中，以便 import app.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.user import Role, User, UserRole
from app.services.auth_service import hash_password


def _validate_password(password: str) -> str | None:
    """校验密码强度，返回错误信息；通过则返回 None。"""
    if len(password) < 8:
        return "密码长度不足 8 位"
    if not re.search(r"[A-Z]", password):
        return "密码需包含至少一个大写字母"
    if not re.search(r"[a-z]", password):
        return "密码需包含至少一个小写字母"
    if not re.search(r"\d", password):
        return "密码需包含至少一个数字"
    return None


def create_admin(username: str, password: str) -> None:
    """创建管理员用户并分配 admin 角色。"""
    # 初始化数据库（建表 + 种子角色）
    init_db()

    db = SessionLocal()
    try:
        # 检查用户名是否已存在
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            print(f"❌ 用户 '{username}' 已存在（id={existing.id}），拒绝覆盖。")
            print("   如需重置密码，请使用 change-password 接口或直接在数据库中更新。")
            sys.exit(1)

        # 获取 admin 角色
        admin_role = db.query(Role).filter(Role.name == "admin").first()
        if admin_role is None:
            print("❌ admin 角色不存在，请先确保数据库已初始化（init_db）。")
            sys.exit(1)

        # 创建用户
        user = User(
            username=username,
            email=f"{username}@admin.local",  # 占位邮箱，登录后可在系统中修改
            full_name="System Admin",
            hashed_password=hash_password(password),
            is_active=True,
            is_superuser=True,
        )
        user.roles.append(admin_role)
        db.add(user)
        db.commit()
        db.refresh(user)

        print(f"✅ 管理员账号创建成功！")
        print(f"   用户名: {username}")
        print(f"   用户ID: {user.id}")
        print(f"   角色:   admin (superuser)")
        print(f"   ⚠️  请立即登录系统并修改默认密码。")

    except Exception as e:
        db.rollback()
        print(f"❌ 创建失败: {e}")
        sys.exit(1)
    finally:
        db.close()


def main() -> None:
    # 用户名：优先环境变量，否则交互输入
    username = os.environ.get("ADMIN_USERNAME")
    if not username:
        username = input("请输入管理员用户名: ").strip()
    if not username:
        print("❌ 用户名不能为空")
        sys.exit(1)

    # 密码：优先环境变量，否则交互输入（不回显）
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("请输入管理员密码: ")
        confirm = getpass.getpass("请再次确认密码: ")
        if password != confirm:
            print("❌ 两次输入的密码不一致")
            sys.exit(1)

    # 密码强度校验
    err = _validate_password(password)
    if err:
        print(f"❌ {err}")
        sys.exit(1)

    create_admin(username, password)


if __name__ == "__main__":
    main()
