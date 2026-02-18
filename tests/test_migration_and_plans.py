import pytest
from sqlalchemy import select
from app.database import async_init_db, get_async_session_maker, User, SubscriptionPlan, UserSubscription, Base
from app.subscription_service import subscription_service, NEW_PLANS
from datetime import datetime, timedelta

@pytest.mark.asyncio
async def test_migration_and_plans():
    # 1. Initialize DB (Reset tables)
    engine = await async_init_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_maker = get_async_session_maker(engine)

    # 2. Setup old data
    async with session_maker() as session:
        # Create an old plan
        old_plan = SubscriptionPlan(
            name='Old Plan',
            description='Legacy',
            price=10000,
            duration_days=30,
            channel_id='old_channel'
        )
        session.add(old_plan)

        # Create a user
        user = User(telegram_user_id='123456789', is_active=True)
        session.add(user)
        await session.flush()

        # Create a subscription to the old plan
        sub = UserSubscription(
            user_id=user.id,
            plan_id=old_plan.id,
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow() + timedelta(days=30),
            is_active=True
        )
        session.add(sub)
        await session.commit()

        old_plan_id = old_plan.id
        sub_id = sub.id

    # 3. Run migration logic
    # We need to inject the session maker into the service
    subscription_service.async_session_maker = session_maker
    await subscription_service._init_subscription_plans()

    # 4. Verify results
    async with session_maker() as session:
        # Check new plans exist
        result = await session.execute(select(SubscriptionPlan))
        all_plans = result.scalars().all()
        plan_names = [p.name for p in all_plans]

        for new_plan in NEW_PLANS:
            assert new_plan['name'] in plan_names, f"Plan {new_plan['name']} not created"

        # Check subscription migrated
        result = await session.execute(select(UserSubscription).where(UserSubscription.id == sub_id))
        migrated_sub = result.scalar_one()

        result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == migrated_sub.plan_id))
        current_plan = result.scalar_one()

        assert current_plan.name == 'Подписка на 1 месяц', f"Subscription not migrated to 1 month plan. Current plan: {current_plan.name}"
        assert current_plan.id != old_plan_id
