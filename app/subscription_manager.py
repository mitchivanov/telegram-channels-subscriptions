from app.database import User, SubscriptionPlan, UserSubscription
from datetime import datetime, timedelta
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select, update

class SubscriptionManager:
    def __init__(self, session):
        self.session = session
    
    async def create_user(self, username, email, full_name=None, is_active=True):
        """Создание нового пользователя"""
        try:
            user = User(
                username=username,
                email=email,
                full_name=full_name,
                is_active=is_active
            )
            self.session.add(user)
            await self.session.commit()
            return user
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise e
    
    async def create_subscription_plan(self, name, price, duration_days, description=None):
        """Создание нового тарифного плана"""
        try:
            plan = SubscriptionPlan(
                name=name,
                description=description,
                price=price,
                duration_days=duration_days
            )
            self.session.add(plan)
            await self.session.commit()
            return plan
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise e
    
    async def subscribe_user(self, user_id, plan_id, start_date=None, reminder_sent=None, commit: bool = True):
        """Подписать пользователя на тарифный план"""
        try:
            result_user = await self.session.execute(select(User).where(User.id == user_id))
            user = result_user.scalar_one_or_none()
            result_plan = await self.session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
            plan = result_plan.scalar_one_or_none()
            
            if not user or not plan:
                raise ValueError("Пользователь или тарифный план не существует")
            
            if start_date is None:
                start_date = datetime.utcnow()
            
            end_date = start_date + timedelta(days=plan.duration_days)
            
            subscription = UserSubscription(
                user_id=user_id,
                plan_id=plan_id,
                start_date=start_date,
                end_date=end_date,
                is_active=True
            )
            
            # Устанавливаем reminder_sent, если он передан
            if reminder_sent is not None:
                subscription.reminder_sent = reminder_sent
            
            self.session.add(subscription)
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
            return subscription
        except SQLAlchemyError as e:
            if commit:
                await self.session.rollback()
            raise e
    
    async def cancel_subscription(self, subscription_id):
        """Отменить подписку пользователя"""
        try:
            result = await self.session.execute(select(UserSubscription).where(UserSubscription.id == subscription_id))
            subscription = result.scalar_one_or_none()
            
            if not subscription:
                raise ValueError("Подписка не найдена")
            
            subscription.is_active = False
            await self.session.commit()
            return subscription
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise e
    
    async def extend_subscription(self, subscription_id, days, reminder_sent=None):
        """Продлить подписку на указанное количество дней"""
        try:
            result = await self.session.execute(select(UserSubscription).where(UserSubscription.id == subscription_id))
            subscription = result.scalar_one_or_none()
            
            if not subscription:
                raise ValueError("Подписка не найдена")
            
            subscription.end_date = subscription.end_date + timedelta(days=days)
            
            if not subscription.is_active and subscription.end_date > datetime.utcnow():
                subscription.is_active = True
            
            # Устанавливаем reminder_sent, если он передан
            if reminder_sent is not None:
                subscription.reminder_sent = reminder_sent
            
            await self.session.commit()
            return subscription
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise e
    
    async def change_subscription_plan(self, user_id, new_plan_id):
        """Изменить тарифный план пользователя"""
        try:
            result = await self.session.execute(select(UserSubscription).where(
                UserSubscription.user_id == user_id,
                UserSubscription.is_active == True
            ))
            current_subscription = result.scalar_one_or_none()
            
            if not current_subscription:
                raise ValueError("У пользователя нет активной подписки")
            
            current_subscription.is_active = False
            
            return await self.subscribe_user(user_id, new_plan_id)
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise e
    
    async def get_active_subscriptions(self, user_id=None):
        """Получить все активные подписки или только для конкретного пользователя"""
        query = select(UserSubscription).where(UserSubscription.is_active == True)
        
        if user_id is not None:
            query = query.where(UserSubscription.user_id == user_id)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def check_subscription_expiration(self):
        """Проверить и обновить статус подписок, срок действия которых истек"""
        now = datetime.utcnow()
        
        try:
            result = await self.session.execute(select(UserSubscription).where(
                UserSubscription.is_active == True,
                UserSubscription.end_date < now
            ))
            expired_subscriptions = result.scalars().all()
            
            for subscription in expired_subscriptions:
                subscription.is_active = False
            
            if expired_subscriptions:
                await self.session.commit()
                
            return expired_subscriptions
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise e 