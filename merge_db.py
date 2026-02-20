# merge_db.py
# Запуск: docker compose exec bot python merge_db.py
# (или положи в корень проекта и закинь в контейнер)

import asyncio
import csv
import os
from datetime import datetime
from dotenv import load_dotenv

# Грузим .env так же, как это делает main.py
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "app", ".env"))

from sqlalchemy import select
from app.database import async_init_db, get_async_session_maker, \
    User, UserSubscription, SubscriptionPlan

DUMP_DIR = os.path.join(os.path.dirname(__file__), "dump_data")
VOLUMES  = ["vol1", "vol2", "vol3", "vol4"]


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def parse_pg_date(val: str) -> datetime:
    """'2025-12-18 07:12:00.123456+00' → datetime"""
    if not val or not val.strip():
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(val.split("+")[0].strip().replace(" ", "T"))
    except ValueError:
        return datetime.utcnow()


def str_val(val) -> str | None:
    """Пустую строку превращаем в None"""
    v = (val or "").strip()
    return v or None


def parse_bool(val) -> bool:
    """PostgreSQL CSV: 't'/'f'"""
    return str(val).strip().lower() in ("t", "true", "1", "yes")


# ─── Основная логика ──────────────────────────────────────────────────────────

async def main():
    engine       = await async_init_db()
    session_maker = get_async_session_maker(engine)

    # Проверяем что планы уже есть (бот должен быть запущен до этого)
    async with session_maker() as session:
        result = await session.execute(select(SubscriptionPlan).order_by(SubscriptionPlan.id))
        plans  = result.scalars().all()

    if not plans:
        print("❌ В таблице subscription_plans пусто.")
        print("   Сначала запусти: docker compose up -d")
        print("   Подожди ~15 сек пока бот создаст планы, потом запускай этот скрипт.")
        return

    print("📋 Планы в базе:")
    for p in plans:
        print(f"   id={p.id}  {p.name}  {p.duration_days} дней  {p.price // 100}₽")

    default_plan = plans[0]
    print(f"\n🔗 Старые подписки будут привязаны к плану: [{default_plan.id}] {default_plan.name}")
    print(f"📁 Папка с CSV: {DUMP_DIR}\n")

    total = {"users_new": 0, "users_dup": 0, "subs_new": 0, "subs_dup": 0}

    for label in VOLUMES:
        users_file = os.path.join(DUMP_DIR, f"users_{label}.csv")
        subs_file  = os.path.join(DUMP_DIR, f"subs_{label}.csv")

        if not os.path.exists(users_file):
            print(f"⚠️  {users_file} не найден — пропускаем")
            continue

        print(f"── {label} ──────────────────────────")

        id_map: dict[str, int] = {}  # старый_id → новый_id

        # ── Пользователи ──────────────────────────────────────────────────────
        async with session_maker() as session:
            with open(users_file, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    tg_id  = row["telegram_user_id"].strip()
                    old_id = row["id"].strip()

                    res = await session.execute(
                        select(User).where(User.telegram_user_id == tg_id)
                    )
                    existing = res.scalar_one_or_none()

                    if existing:
                        id_map[old_id] = existing.id
                        total["users_dup"] += 1
                    else:
                        user = User(
                            telegram_user_id         = tg_id,
                            first_name               = str_val(row.get("first_name")),
                            is_active                = parse_bool(row.get("is_active", "t")),
                            email                    = str_val(row.get("email")),
                            created_at               = parse_pg_date(row.get("created_at")),
                            first_start_reminder_sent= parse_bool(row.get("first_start_reminder_sent", "f")),
                        )
                        session.add(user)
                        await session.flush()       # получаем сгенерированный id
                        id_map[old_id] = user.id
                        total["users_new"] += 1

            await session.commit()

        new_u = sum(1 for k in id_map)
        print(f"   users : +{total['users_new']} новых | {total['users_dup']} уже существовали")

        # ── Подписки ──────────────────────────────────────────────────────────
        if not os.path.exists(subs_file):
            print(f"   subs  : файл не найден, пропускаем")
            continue

        async with session_maker() as session:
            with open(subs_file, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    old_uid = row["user_id"].strip()

                    if old_uid not in id_map:
                        print(f"   ⚠️  user_id={old_uid} не найден в маппинге, пропуск строки")
                        continue

                    new_uid = id_map[old_uid]

                    # Не дублируем активную подписку
                    res = await session.execute(
                        select(UserSubscription).where(
                            UserSubscription.user_id  == new_uid,
                            UserSubscription.is_active == True,
                        )
                    )
                    if res.scalar_one_or_none():
                        total["subs_dup"] += 1
                        continue

                    sub = UserSubscription(
                        user_id                    = new_uid,
                        plan_id                    = default_plan.id,
                        start_date                 = parse_pg_date(row.get("start_date")),
                        end_date                   = parse_pg_date(row.get("end_date")),
                        is_active                  = parse_bool(row.get("is_active", "f")),
                        invite_link                = str_val(row.get("invite_link")),
                        reminder_sent              = parse_bool(row.get("reminder_sent", "f")),
                        last_day_reminder_sent     = parse_bool(row.get("last_day_reminder_sent", "f")),
                        expired_reminder_sent      = parse_bool(row.get("expired_reminder_sent", "f")),
                        provider_payment_charge_id = str_val(row.get("provider_payment_charge_id")),
                    )
                    session.add(sub)
                    total["subs_new"] += 1

            await session.commit()

        print(f"   subs  : +{total['subs_new']} новых | {total['subs_dup']} пропущено (дубли)")

    # ── Итог ──────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 45}")
    print(f"✅ ГОТОВО")
    print(f"   Добавлено пользователей : {total['users_new']}")
    print(f"   Пропущено дублей        : {total['users_dup']}")
    print(f"   Добавлено подписок      : {total['subs_new']}")
    print(f"   Пропущено подписок      : {total['subs_dup']}")

    async with session_maker() as session:
        u = len((await session.execute(select(User))).scalars().all())
        s = len((await session.execute(select(UserSubscription))).scalars().all())
        a = len((await session.execute(
            select(UserSubscription).where(UserSubscription.is_active == True)
        )).scalars().all())

    print(f"\n📊 Итоговое состояние базы:")
    print(f"   Пользователей  : {u}")
    print(f"   Подписок всего : {s}  (активных: {a})")


if __name__ == "__main__":
    asyncio.run(main())
