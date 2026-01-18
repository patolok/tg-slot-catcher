import time
import sys
import re
import os
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)

# ANSI цвета для консоли
RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"
YELLOW = "\033[93m"


# --- ЧТЕНИЕ ИЗ КОНФИГА ---
def load_config(filename="config.txt"):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    config = {}

    if not os.path.exists(path):
        print(f"Файл {filename} не найден")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()

    return config


# --- ВАЛИДАЦИЯ ТОКЕНА ТГ-БОТА ---
def check_telegram_token():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    try:
        resp = requests.get(url, timeout=10)
    except Exception as e:
        print(f"{RED}Ошибка соединения с Telegram API: {e}{RESET}")
        sys.exit(1)

    if resp.status_code != 200:
        print(f"{RED}Неверный Telegram BOT TOKEN!\n{RESET}")
        sys.exit(1)

    data = resp.json()
    if not data.get("ok"):
        print(f"{RED}Telegram BOT TOKEN не прошёл проверку!{RESET}")
        sys.exit(1)

    bot_name = data["result"].get("username", "unknown")
    print(f"{GREEN}\nИспользуется Telegram бот: @{bot_name} {RESET}")


# --- ЗАГРУЗКА НАСТРОЕК ---
config = load_config()
USERNAME, PASSWORD, BOT_TOKEN, USER_ID = (
    config.get(k) or sys.exit(f"В config.txt не задано: {k}")
    for k in ("USERNAME", "PASSWORD", "BOT_TOKEN", "USER_ID")
)
check_telegram_token()
CHECK_INTERVAL = int(config.get("CHECK_INTERVAL", 180))
PAGE_LOAD_WAIT = int(config.get("PAGE_LOAD_WAIT", 30))
LOGIN_URL = "https://platform.21-school.ru/"
PROJECT_ID = 0
print(
    "\nВведите Project ID проверяемого проекта. Например 71963 для QA1.\n"
    "Его можно найти в адресной строке браузера на страничке проекта.\n"
)
try:
    while True:
        user_input = input("Project ID: ")
        if user_input.strip().isdigit() and int(user_input.strip()) > 0:
            PROJECT_ID = user_input.strip()
            break
        else:
            print("Некорректный Project ID")

except KeyboardInterrupt:
    print(f"\n{GREEN}⏹ Завершение работы по Ctrl+C...{RESET}")
    sys.exit(0)

START_URL = f"https://platform.21-school.ru/calendar/review/{PROJECT_ID}"


def send_telegram(message: str):
    """Отправка уведомления в телеграм"""
    if not BOT_TOKEN or not USER_ID:
        print("(Telegram отключён — BOT_TOKEN или USER_ID пусты)")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": USER_ID, "text": message}, timeout=10)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"{RED}Ошибка при отправке в Telegram: {e}{RESET}")


# --- SELENIUM ---
chrome_options = Options()
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--blink-settings=imagesEnabled=false")
driver = webdriver.Chrome(options=chrome_options)


def login():
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        print(
            f"\n{GREEN}Авторизация на платформе (попытка {attempt} из {max_attempts}).{RESET}"
        )
        try:
            driver.get(LOGIN_URL)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            username_field = driver.find_element(By.NAME, "username")
            username_field.clear()
            username_field.send_keys(USERNAME)
            password_field = driver.find_element(By.NAME, "password")
            password_field.clear()
            password_field.send_keys(PASSWORD)

            login_button = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "button[type='submit'], input[type='submit']")
                )
            )
            driver.execute_script("arguments[0].scrollIntoView(true);", login_button)
            time.sleep(1)
            login_button.click()

            time.sleep(20)

            if driver.current_url == LOGIN_URL:
                print(f"{GREEN}Авторизация на платформе удалась.{RESET}")
                return True
            else:
                print(
                    f"{YELLOW}Авторизация не удалась на попытке {attempt}. Пробуем снова...{RESET}"
                )

        except (TimeoutException, NoSuchElementException, WebDriverException):
            print(f"{RED}Ошибка на попытке {attempt}{RESET}")
            time.sleep(5)  # Пауза между попытками

        except KeyboardInterrupt:
            raise

    print(f"{RED}Авторизация не удалась после {max_attempts} попыток!{RESET}")
    return False


def extract_left_px(style: str):
    if not style:
        return None

    m = re.search(r"left:\s*(\d+)px", style)
    if m:
        return int(m.group(1))

    m = re.search(r"translateX\((\d+)px\)", style)
    if m:
        return int(m.group(1))

    return None


def get_column_geometry():
    columns = driver.find_elements(
        By.CSS_SELECTOR, "[data-testid='Calendar.Grid.column']"
    )
    rects = []
    for col in columns:
        r = col.rect
        rects.append({"left": r["x"], "right": r["x"] + r["width"]})
    return rects


def get_free_slots():
    slots = set()

    # Заголовки дней
    header_elems = driver.find_elements(
        By.CSS_SELECTOR, "[data-testid='Calendar.Header.label']"
    )
    headers = [" ".join(h.text.split()) for h in header_elems]

    # Геометрия колонок
    columns = driver.find_elements(
        By.CSS_SELECTOR, "[data-testid='Calendar.Grid.column']"
    )
    column_rects = []
    for col in columns:
        r = col.rect
        column_rects.append({"left": r["x"], "right": r["x"] + r["width"]})

    # Свободные слоты
    slot_elements = driver.find_elements(
        By.CSS_SELECTOR, "[data-testid='ProjectTimeSlot.IndividualProject white']"
    )

    for slot in slot_elements:
        slot_x = slot.rect["x"]

        col_index = None
        for i, r in enumerate(column_rects):
            if r["left"] <= slot_x <= r["right"]:
                col_index = i
                break

        day_label = headers[col_index] if col_index is not None else "unknown-date"

        time_el = slot.find_elements(
            By.CSS_SELECTOR, "[data-testid='Calendar.Slot.time']"
        )
        desc_el = slot.find_elements(
            By.CSS_SELECTOR, "[data-testid='Calendar.Slot.description']"
        )

        time_txt = time_el[0].text.strip() if time_el else ""
        desc_txt = desc_el[0].text.strip() if desc_el else ""

        if desc_txt == "Peer Review slot":
            continue

        if time_txt:
            key = f"{day_label} | {time_txt} | {desc_txt}"
            slots.add(key)

    print(f"Найдено слотов: {len(slots)}")
    return slots


def slot_sort_key(slot: str):
    date_part, time_part, _ = slot.split(" | ")

    try:
        dt = datetime.strptime(f"{date_part} {time_part}", "%d %b, %a %H:%M")
        return dt
    except KeyboardInterrupt:
        raise
    except Exception:
        return datetime.max


# --- ОСНОВНОЙ ЦИКЛ ---
print("\nКонфигурация завершена. Проверка каждые", CHECK_INTERVAL, "секунд.")

try:

    if not login():
        sys.exit(1)

    # Переходим на страницу календаря
    driver.get(START_URL)
    time.sleep(PAGE_LOAD_WAIT)

    old_slots = get_free_slots()

    for s in sorted(old_slots, key=slot_sort_key):
        print(s)

    if old_slots:
        sorted_old = sorted(old_slots, key=slot_sort_key)
        send_telegram("📅 Свободные слоты:\n" + "\n".join(f"• {s}" for s in sorted_old))
    else:
        send_telegram("📅 Свободных слотов пока нет.")

    while True:
        print("🔄 Обновление календаря")
        driver.refresh()
        time.sleep(PAGE_LOAD_WAIT)
        if "auth" in driver.current_url:
            print("🔑 Сессия истекла, логинюсь заново")
            if login():
                driver.get(START_URL)
                time.sleep(PAGE_LOAD_WAIT)
                continue

        new_slots = get_free_slots()

        added = new_slots - old_slots

        if added:
            for slot in sorted(added, key=slot_sort_key):
                msg = f"🟢 ПОЯВИЛСЯ СЛОТ: {slot}"
                print(msg)
                send_telegram(msg)
            send_telegram(f"Записаться: {START_URL}")

        old_slots = new_slots
        time.sleep(CHECK_INTERVAL)

except KeyboardInterrupt:
    print(f"\n{GREEN}⏹ Завершение работы по Ctrl+C...{RESET}")
    send_telegram("⏹ Скрипт остановлен вручную.")

except Exception as e:
    print(f"{RED}❌ Критическая ошибка: {e}{RESET}")
    send_telegram("❌ Скрипт упал")
    raise

finally:
    print("Закрываю браузер...")
    try:
        driver.quit()
    except Exception as e:
        print(f"Ошибка при закрытии браузера: {e}")
