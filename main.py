"""
GM Bot для Ink Chain - Прямая работа через Web3 (БЕЗ Selenium!)
Быстрее, надежнее, проще!
"""

import json
import logging
from datetime import datetime
from web3 import Web3
import time
import random
from pathlib import Path
from eth_account import Account

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'gm_bot_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===== КОНФИГУРАЦИЯ КОНТРАКТА =====
# Ink Mainnet
INK_CHAIN_ID = 57073
INK_RPC_URL = "https://rpc-gel.inkonchain.com"

# Адрес контракта DailyGM на Ink Chain (правильный из explorer)
GM_CONTRACT_ADDRESS = "0x9F500d075118272B3564ac6Ef2c70a9067Fd2d3F"

# ABI контракта DailyGM на Ink Chain
# Минимальный ABI для взаимодействия с контрактом
GM_CONTRACT_ABI = [
    {
        "inputs": [],
        "name": "gm",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

class Config:
    """Класс для работы с конфигурацией"""
    
    @staticmethod
    def load_config(config_path='config.json'):
        """Загрузка конфигурации из JSON файла"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Файл конфигурации {config_path} не найден!")
            Config.create_default_config(config_path)
            logger.info(f"Создан файл конфигурации по умолчанию: {config_path}")
            return None
    
    @staticmethod
    def load_failed_wallets(failed_file=None):
        """Загрузка failed кошельков для retry"""
        if failed_file is None:
            # Ищем последний failed файл
            import glob
            failed_files = glob.glob('failed_wallets_*.json')
            if not failed_files:
                logger.error("❌ Не найдено файлов с failed кошельками!")
                return None
            failed_file = max(failed_files)  # Берем самый новый
            logger.info(f"📂 Используем последний failed файл: {failed_file}")
        
        try:
            with open(failed_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"✅ Загружено {data['count']} failed кошельков")
                logger.info(f"📅 Дата: {data.get('timestamp', 'unknown')}")
                return data['wallets']
        except FileNotFoundError:
            logger.error(f"❌ Файл {failed_file} не найден!")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки failed кошельков: {e}")
            return None
    
    @staticmethod
    def create_default_config(config_path='config.json'):
        """Создание файла конфигурации по умолчанию"""
        default_config = {
            "rpc_url": INK_RPC_URL,
            "chain_id": INK_CHAIN_ID,
            "gm_contract_address": "0x9F500d075118272B35564ac6Ef2c70a9067Fd2d3F",
            "max_retries": 3,
            "retry_delay": 5,
            "gas_price_multiplier": 1.1,
            "max_gas_price_gwei": 50,
            "delay_between_wallets": {
                "enabled": True,
                "min_seconds": 30,
                "max_seconds": 80
            },
            "wallets": [
                {
                    "address": "0x...",
                    "private_key": "ваш_приватный_ключ_1",
                    "proxy": None
                },
                {
                    "address": "0x...",
                    "private_key": "ваш_приватный_ключ_2", 
                    "proxy": None
                }
            ]
        }
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)

class WalletStatus:
    """Класс для отслеживания статуса кошельков"""
    SUCCESS = "✅ Успешно"
    FAILED = "❌ Ошибка"
    INSUFFICIENT_BALANCE = "💰 Недостаточно средств"
    NETWORK_ERROR = "🌐 Ошибка сети"
    ALREADY_GMED = "⏰ Уже отправлен GM сегодня"
    TIMEOUT = "⏱️ Таймаут"
    INVALID_CONTRACT = "🔧 Неверный адрес контракта"

class GMBot:
    """Основной класс бота"""
    
    def __init__(self, config, wallet_range=None):
        """
        Инициализация бота
        
        Args:
            config: Конфигурация
            wallet_range: Диапазон кошельков для обработки (tuple или list)
                         Примеры:
                         - None: все кошельки
                         - (0, 5): кошельки с индексами 0-4 (первые 5)
                         - [0, 2, 5, 10]: конкретные кошельки по индексам
                         - "1-10": кошельки 1-10 (строка)
        """
        self.config = config
        self.results = []
        self.failed_wallets = []
        self.wallet_range = self._parse_wallet_range(wallet_range)
        
        # Фильтруем кошельки по диапазону
        if self.wallet_range:
            self.config['wallets'] = self._filter_wallets(config['wallets'])
            logger.info(f"🎯 Выбрано кошельков для обработки: {len(self.config['wallets'])}")
        
        # Используем адрес контракта - всегда берем из константы, чтобы избежать проблем с checksum
        contract_addr = GM_CONTRACT_ADDRESS
        
        # Подключение к Ink Chain
        self.w3 = Web3(Web3.HTTPProvider(config.get('rpc_url', INK_RPC_URL)))
        
        if not self.w3.is_connected():
            raise ConnectionError(f"Не удалось подключиться к RPC: {config.get('rpc_url')}")
        
        logger.info(f"✅ Подключено к Ink Chain (Chain ID: {self.w3.eth.chain_id})")
        
        # Инициализация контракта - используем Web3.to_checksum_address для получения правильного формата
        try:
            # Сначала приводим к lowercase, потом получаем checksum
            normalized_addr = contract_addr.lower()
            checksum_addr = self.w3.to_checksum_address(normalized_addr)
            
            self.gm_contract = self.w3.eth.contract(
                address=checksum_addr,
                abi=GM_CONTRACT_ABI
            )
            logger.info(f"✅ Контракт GM загружен: {checksum_addr}")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки контракта: {e}")
            raise
        logger.info(f"✅ Контракт GM загружен: {contract_addr}")
        logger.info(f"📋 Загружено кошельков из конфига: {len(self.config['wallets'])}")
    
    def check_balance(self, address):
        """Проверка баланса кошелька"""
        try:
            address = Web3.to_checksum_address(address)
            balance = self.w3.eth.get_balance(address)
            balance_eth = self.w3.from_wei(balance, 'ether')
            logger.info(f"Баланс кошелька {address[:10]}...: {balance_eth:.6f} ETH")
            return balance_eth
        except Exception as e:
            logger.error(f"Ошибка проверки баланса для {address[:10]}...: {e}")
            return None
    
    def check_can_gm(self, address):
        """Проверка, можно ли отправить GM сегодня"""
        # Пока не знаем точной функции проверки в контракте
        # Пробуем отправить и обработаем ошибку если уже отправлено
        logger.info("✅ Пробуем отправить GM...")
        return True, 0
    
    def estimate_gas(self, transaction):
        """Оценка газа для транзакции"""
        try:
            gas_estimate = self.w3.eth.estimate_gas(transaction)
            # Добавляем 20% запаса
            return int(gas_estimate * 1.2)
        except Exception as e:
            logger.warning(f"Не удалось оценить газ: {e}. Используем значение по умолчанию")
            return 100000  # Стандартное значение
    
    def send_gm_transaction(self, wallet_data):
        """Отправка GM транзакции"""
        address = Web3.to_checksum_address(wallet_data['address'])
        private_key = wallet_data['private_key']
        
        # Убираем 0x из приватного ключа если есть
        if private_key.startswith('0x') or private_key.startswith('0X'):
            private_key = private_key[2:]
        
        try:
            # Проверка баланса
            balance_eth = self.check_balance(address)
            min_balance = 0.0001  # Минимальный баланс для газа
            
            if balance_eth is None or balance_eth < min_balance:
                return False, WalletStatus.INSUFFICIENT_BALANCE, f"Баланс: {balance_eth:.6f} ETH (нужно минимум {min_balance:.6f} ETH)"
            
            # Проверка, можно ли отправить GM
            can_gm, wait_seconds = self.check_can_gm(address)
            if not can_gm:
                hours = wait_seconds // 3600
                minutes = (wait_seconds % 3600) // 60
                return False, WalletStatus.ALREADY_GMED, f"Следующий GM через {hours}ч {minutes}м"
            
            # Получение nonce
            nonce = self.w3.eth.get_transaction_count(address)
            
            # Получение цены газа
            gas_price = self.w3.eth.gas_price
            max_gas_price = self.w3.to_wei(
                self.config.get('max_gas_price_gwei', 50), 
                'gwei'
            )
            
            if gas_price > max_gas_price:
                logger.warning(f"⚠️ Цена газа высокая: {self.w3.from_wei(gas_price, 'gwei'):.2f} Gwei")
                gas_price = max_gas_price
            
            # Применяем множитель к цене газа для быстрого подтверждения
            multiplier = self.config.get('gas_price_multiplier', 1.1)
            gas_price = int(gas_price * multiplier)
            
            logger.info(f"💰 Цена газа: {self.w3.from_wei(gas_price, 'gwei'):.2f} Gwei")
            
            # Построение транзакции - вызываем метод gm() без параметров
            transaction = self.gm_contract.functions.gm().build_transaction({
                'from': address,
                'gas': 100000,  # Временное значение для оценки
                'gasPrice': gas_price,
                'nonce': nonce,
                'chainId': self.config.get('chain_id', INK_CHAIN_ID)
            })
            
            # Оценка газа
            gas_limit = self.estimate_gas(transaction)
            transaction['gas'] = gas_limit
            
            logger.info(f"⛽ Gas Limit: {gas_limit}")
            
            # Подписание транзакции
            account = Account.from_key(private_key)
            signed_txn = self.w3.eth.account.sign_transaction(transaction, private_key)
            
            # Отправка транзакции
            logger.info("📤 Отправка GM транзакции...")
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            tx_hash_hex = tx_hash.hex()
            
            logger.info(f"✅ Транзакция отправлена: {tx_hash_hex}")
            logger.info(f"🔗 Эксплорер: https://explorer.inkonchain.com/tx/{tx_hash_hex}")
            
            # Ожидание подтверждения
            logger.info("⏳ Ожидание подтверждения транзакции...")
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt['status'] == 1:
                logger.info(f"🎉 GM успешно отправлен! Gas использовано: {receipt['gasUsed']}")
                return True, WalletStatus.SUCCESS, f"TX: {tx_hash_hex}"
            else:
                logger.error(f"❌ Транзакция не выполнена (reverted)")
                return False, WalletStatus.FAILED, f"Транзакция reverted. TX: {tx_hash_hex}"
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ Ошибка при отправке GM: {error_msg}")
            
            # Более детальная обработка ошибок
            if "insufficient funds" in error_msg.lower():
                return False, WalletStatus.INSUFFICIENT_BALANCE, error_msg
            elif "already" in error_msg.lower() or "wait" in error_msg.lower():
                return False, WalletStatus.ALREADY_GMED, error_msg
            elif "timeout" in error_msg.lower():
                return False, WalletStatus.TIMEOUT, error_msg
            else:
                return False, WalletStatus.FAILED, error_msg
    
    def process_wallet(self, wallet_data, attempt=1):
        """Обработка одного кошелька"""
        address = wallet_data['address']
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Обработка кошелька: {address[:10]}...{address[-8:]}")
        logger.info(f"Попытка: {attempt}/{self.config['max_retries']}")
        logger.info(f"{'='*60}")
        
        return self.send_gm_transaction(wallet_data)
    
    def run(self):
        """Запуск обработки всех кошельков"""
        logger.info("🚀 Запуск GM бота для Ink Chain")
        logger.info(f"Количество кошельков: {len(self.config['wallets'])}")
        logger.info(f"Максимум попыток: {self.config['max_retries']}")
        
        delay_config = self.config.get('delay_between_wallets', {})
        delay_enabled = delay_config.get('enabled', False)
        
        if delay_enabled:
            min_delay = delay_config.get('min_seconds', 30)
            max_delay = delay_config.get('max_seconds', 80)
            logger.info(f"⏱️ Задержка между кошельками: {min_delay}-{max_delay} секунд")
        
        for index, wallet_data in enumerate(self.config['wallets']):
            address = wallet_data['address']
            success = False
            
            # Добавляем задержку перед обработкой кошелька (кроме первого)
            if delay_enabled and index > 0:
                delay = random.randint(min_delay, max_delay)
                logger.info(f"\n⏳ Задержка перед следующим кошельком: {delay} секунд...")
                time.sleep(delay)
            
            for attempt in range(1, self.config['max_retries'] + 1):
                success, status, error_message = self.process_wallet(wallet_data, attempt)
                
                # Сохранение результата
                result = {
                    'address': address,
                    'attempt': attempt,
                    'status': status,
                    'message': error_message,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                self.results.append(result)
                
                if success or status == WalletStatus.ALREADY_GMED:
                    # Успех или уже отправлен - не повторяем
                    break
                
                if attempt < self.config['max_retries']:
                    retry_delay = self.config['retry_delay']
                    logger.info(f"⏳ Повтор через {retry_delay} секунд...")
                    time.sleep(retry_delay)
            
            if not success and status != WalletStatus.ALREADY_GMED:
                self.failed_wallets.append({
                    'address': address,
                    'last_status': status,
                    'last_error': error_message
                })
        
        self.print_summary()
        self.save_results()
    
    def print_summary(self):
        """Вывод итоговой статистики"""
        logger.info("\n" + "="*60)
        logger.info("📊 ИТОГОВАЯ СТАТИСТИКА")
        logger.info("="*60)
        
        total = len(self.config['wallets'])
        successful = sum(1 for r in self.results if r['status'] == WalletStatus.SUCCESS)
        already_gmed = sum(1 for r in self.results if r['status'] == WalletStatus.ALREADY_GMED)
        failed = len(self.failed_wallets)
        
        logger.info(f"Всего кошельков: {total}")
        logger.info(f"✅ Успешно: {successful}")
        logger.info(f"⏰ Уже отправлен GM: {already_gmed}")
        logger.info(f"❌ Неудачно: {failed}")
        
        if self.failed_wallets:
            logger.info("\n🔴 Проблемные кошельки:")
            for wallet in self.failed_wallets:
                logger.info(f"  • {wallet['address'][:10]}...{wallet['address'][-8:]}")
                logger.info(f"    Статус: {wallet['last_status']}")
                logger.info(f"    Ошибка: {wallet['last_error']}")
    
    def save_results(self):
        """Сохранение результатов в JSON"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        results_file = f"results_{timestamp}.json"
        
        # Сохраняем полные результаты
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump({
                'results': self.results,
                'failed_wallets': self.failed_wallets,
                'summary': {
                    'total': len(self.config['wallets']),
                    'successful': sum(1 for r in self.results if r['status'] == WalletStatus.SUCCESS),
                    'already_gmed': sum(1 for r in self.results if r['status'] == WalletStatus.ALREADY_GMED),
                    'failed': len(self.failed_wallets)
                }
            }, f, indent=4, ensure_ascii=False)
        logger.info(f"💾 Результаты сохранены в {results_file}")
        
        # Сохраняем failed кошельки в отдельный файл для retry
        if self.failed_wallets:
            failed_file = f"failed_wallets_{timestamp}.json"
            failed_data = {
                'timestamp': timestamp,
                'count': len(self.failed_wallets),
                'wallets': []
            }
            
            # Находим полные данные кошельков (с private_key и proxy)
            for failed in self.failed_wallets:
                # Ищем оригинальный кошелек в конфиге
                for wallet in self.config['wallets']:
                    if wallet['address'].lower() == failed['address'].lower():
                        failed_data['wallets'].append({
                            'address': wallet['address'],
                            'private_key': wallet['private_key'],
                            'proxy': wallet.get('proxy'),
                            'last_error': failed['last_error'],
                            'last_status': failed['last_status']
                        })
                        break
            
            with open(failed_file, 'w', encoding='utf-8') as f:
                json.dump(failed_data, f, indent=4, ensure_ascii=False)
            
            logger.info(f"🔴 Failed кошельки сохранены в {failed_file}")
            logger.info(f"📋 Для retry запустите: python main.py --retry {failed_file}")
            logger.info(f"   или просто: python main.py --retry-last")

def main():
    """Главная функция"""
    import sys
    
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║          GM Bot для Ink Chain v2.0 (Web3)              ║
    ║                                                          ║
    ║  ⚡ Быстрее - без браузера!                             ║
    ║  🔒 Безопаснее - прямая работа с блокчейном!           ║
    ║  💪 Надежнее - меньше точек отказа!                    ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    
    # Парсим аргументы командной строки
    wallet_range = None
    retry_mode = False
    retry_file = None
    
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        
        if arg == '--retry' or arg == '-r':
            retry_mode = True
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith('-'):
                retry_file = sys.argv[i + 1]
                i += 1
            i += 1
        elif arg == '--retry-last' or arg == '-rl':
            retry_mode = True
            retry_file = None  # Будем искать последний файл
            i += 1
        else:
            # Это диапазон кошельков
            if '-' in arg:
                wallet_range = arg
            elif ',' in arg:
                wallet_range = [int(x.strip()) for x in arg.split(',')]
            else:
                wallet_range = arg
            i += 1
    
    try:
        logger.info("📂 Загрузка конфигурации...")
        
        if retry_mode:
            logger.info("🔄 РЕЖИМ RETRY: Повторная обработка failed кошельков")
            
            # Загружаем failed кошельки
            failed_wallets = Config.load_failed_wallets(retry_file)
            if failed_wallets is None:
                return
            
            # Создаем временный конфиг только с failed кошельками
            config = Config.load_config()
            if config is None:
                return
            
            # Заменяем кошельки на failed
            config['wallets'] = failed_wallets
            logger.info(f"🎯 Будет обработано {len(failed_wallets)} failed кошельков")
            
        else:
            config = Config.load_config()
            if config is None:
                logger.error("❌ Конфигурация не загружена!")
                logger.error("Отредактируйте config.json и запустите скрипт снова")
                return
            
            if wallet_range:
                logger.info(f"🎯 Режим выбора кошельков: {wallet_range}")
        
        logger.info(f"✅ Конфигурация загружена")
        logger.info(f"📊 Всего кошельков: {len(config.get('wallets', []))}")
        
        if not config.get('wallets') or len(config['wallets']) == 0:
            logger.error("❌ В конфигурации нет кошельков!")
            return
        
        # Проверка что кошельки настроены
        first_wallet = config['wallets'][0]
        if first_wallet.get('address') == "0x..." or not first_wallet.get('private_key'):
            logger.error("❌ Кошельки не настроены!")
            return
        
        logger.info("🚀 Инициализация бота...")
        bot = GMBot(config, wallet_range=wallet_range if not retry_mode else None)
        
        logger.info("▶️  Начинаем обработку кошельков...")
        bot.run()
        
    except ValueError as e:
        logger.error(f"ValueError: {e}")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)

if __name__ == "__main__":
    main()