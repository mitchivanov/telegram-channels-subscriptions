import gspread
from google.oauth2.service_account import Credentials
import os
import logging
from datetime import datetime

class GoogleSheetsService:
    def __init__(self, credentials_path=None, sheet_id=None):
        self.logger = logging.getLogger(__name__)
        # Значения будут инициализированы при первом вызове, чтобы дать время load_dotenv сработать
        self._credentials_path = credentials_path
        self._sheet_id = sheet_id
        self.client = None
        self.scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

    @property
    def credentials_path(self):
        return self._credentials_path or os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')

    @property
    def sheet_id(self):
        return self._sheet_id or os.getenv('GOOGLE_SHEET_ID')

    def _authenticate(self):
        """Аутентификация в Google Sheets API"""
        if not os.path.exists(self.credentials_path):
            self.logger.warning(f"Файл ключей {self.credentials_path} не найден. Запись в Google Sheets отключена.")
            return False

        try:
            creds = Credentials.from_service_account_file(self.credentials_path, scopes=self.scope)
            self.client = gspread.authorize(creds)
            return True
        except Exception as e:
            self.logger.error(f"Ошибка аутентификации Google Sheets: {e}")
            return False

    def _get_sheet(self):
        """Получение листа для записи"""
        if not self.client:
            if not self._authenticate():
                return None

        if not self.sheet_id:
            self.logger.warning("GOOGLE_SHEET_ID не задан в .env. Запись невозможна.")
            return None

        try:
            # Открываем таблицу по ID
            spreadsheet = self.client.open_by_key(self.sheet_id)
            # Берем первый лист
            return spreadsheet.sheet1
        except Exception as e:
            self.logger.error(f"Ошибка открытия таблицы {self.sheet_id}: {e}")
            return None

    def append_payment(self, user_id, username, amount, duration_days, plan_name, payment_type, transaction_id):
        """
        Добавление записи о платеже.
        Поля: [User ID, Username, Дата, Сумма, Длительность, Название плана, Тип, ID транзакции]
        """
        try:
            sheet = self._get_sheet()
            if not sheet:
                return False

            # Формируем строку
            row = [
                str(user_id),
                username or "Не указан",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                amount,  # Сумма должна быть числом или строкой
                str(duration_days),
                plan_name,
                payment_type,  # 'Новая' или 'Продление'
                str(transaction_id)
            ]

            # Добавляем строку в конец таблицы
            sheet.append_row(row)
            self.logger.info(f"Запись успешно добавлена в Google Sheets: {row}")
            return True
        except Exception as e:
            self.logger.error(f"Ошибка при записи в Google Sheets: {e}")
            return False

# Глобальный экземпляр
google_sheets_service = GoogleSheetsService()
