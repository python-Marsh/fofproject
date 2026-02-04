from dotenv import load_dotenv
load_dotenv()
import os
import time
import tempfile
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from plyer import notification

# ------------------------- CONFIG -------------------------
URL = "https://abs1.td.gov.hk/tdab2/tdabs_external/AdminServlet_tchinese?cmd=cmdShowHome#"

ID_NUMBER = os.getenv("ID_NUMBER")
CAR_NUMBER = os.getenv("CAR_NUMBER")
EMAIL = os.getenv("EMAIL")

# Comma-separated list of proxy URLs (or leave empty for none)
# Examples:
#   http://user:pass@proxy1.example.com:8000
#   socks5://user:pass@proxy2.example.com:1080
PROXY_POOL = [p.strip() for p in os.getenv("PROXY_POOL", "").split(",") if p.strip()]

HEADLESS = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")

REFRESH_SECONDS = 300
# ----------------------------------------------------------

def build_chrome_options(proxy_url: str | None, headless: bool | None = None) -> Options:
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--disable-geolocation")
    chrome_options.add_argument("--incognito")
    # Fresh user profile per session (keeps sessions isolated and ephemeral)
    user_data_dir = tempfile.mkdtemp(prefix="selenium-profile-")
    chrome_options.add_argument(f"--user-data-dir={user_data_dir}")

    # headless param overrides env var; used to force visible browser for interaction
    is_headless = headless if headless is not None else HEADLESS
    if is_headless:
        chrome_options.add_argument("--headless=new")

    if proxy_url:
        # Use a licensed, provider-approved proxy. Do not use this to bypass security or rate limits.
        chrome_options.add_argument(f"--proxy-server={proxy_url}")

    return chrome_options

def new_driver(proxy_url: str | None, headless: bool | None = None):
    options = build_chrome_options(proxy_url, headless=headless)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def navigate_to_slots(driver) -> tuple[bool, str]:
    """
    Drive through the booking flow and stop at the slot-selection page.
    Returns (time_found, page_source).  Does NOT click 繼續 after the slots
    appear — the browser stays on the actionable page.
    """
    wait = WebDriverWait(driver, 15)

    driver.get(URL)

    # Step 1: First '開始預約'
    start_link = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//a[@onclick='onClickStartApplication()']"))
    )
    start_link.click()
    print("✅ Clicked first 開始預約 link")

    # Step 2: Checkbox
    checkbox = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='checkbox' and @id='checkbox']"))
    )
    checkbox.click()
    print("✅ Clicked checkbox")

    # Step 3: '繼續'
    continue_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='button' and @value='繼續']"))
    )
    continue_btn.click()
    print("✅ Clicked 繼續 button")

    time.sleep(1)

    # Step 4: Second '開始預約' button
    book_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "/html/body/main/div/div/div/div[3]/div[1]/div[2]/div[1]/form[2]/div/div[1]/div[2]/div/div[5]/div[1]/div[2]/input"))
    )
    book_btn.click()
    print("✅ Clicked second 開始預約 button")

    # Fill in personal info
    passport_field = wait.until(EC.element_to_be_clickable((By.ID, "diPassportNumber")))
    passport_field.clear()
    passport_field.send_keys(ID_NUMBER)
    print(f"✅ Entered passport number: {ID_NUMBER}")

    license_field = wait.until(EC.element_to_be_clickable((By.ID, "txtLicenseNumber")))
    license_field.clear()
    license_field.send_keys(CAR_NUMBER)
    print(f"✅ Entered license number: {CAR_NUMBER}")

    radio_button = wait.until(EC.element_to_be_clickable((By.ID, "issuingCountryOther")))
    radio_button.click()
    print("✅ Selected radio button 'issuingCountryOther'")

    email_field = wait.until(EC.element_to_be_clickable((By.ID, "txtEmailAddress")))
    email_field.clear()
    email_field.send_keys(EMAIL)
    print(f"✅ Entered email address: {EMAIL}")

    continue_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='button' and @value='繼續']"))
    )
    continue_btn.click()
    print("✅ Clicked 繼續 button")

    cont_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "/html/body/main/div/div/div/div[3]/div[1]/div[2]/div/div[3]/div/div/input[2]"))
    )
    cont_btn.click()
    print("✅ Clicked confirmation button")

    search_button = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='button' and @value='快速搜索']"))
    )
    search_button.click()
    print("✅ Clicked the '快速搜索' button successfully!")
    time.sleep(2)

    all_day_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "/html/body/main/div/div/div/div[3]/div[1]/div[2]/div[1]/div[2]/div/div[2]/div/div/table/tbody/tr[3]/td[2]/table/tbody/tr[4]/td[2]/label/input"))
    )
    all_day_btn.click()
    print("✅ Clicked 星期一至五")

    continue_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='button' and @value='繼續']"))
    )
    continue_btn.click()
    print("✅ Clicked 繼續 button")
    time.sleep(1)

    page_source = driver.page_source

    # --- 上午 check ---
    if "沒有可預約的時間" not in page_source:
        print("✅ Available times found in 上午!")
        return True, page_source

    # --- fallback to 下午 ---
    print("⚠️ No available times in 上午, trying 下午...")
    fallback_button = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='button' and (contains(@value, '返回') or contains(@value, '重新搜索'))]"))
    )
    driver.execute_script("arguments[0].scrollIntoView(true);", fallback_button)
    fallback_button.click()
    print("✅ Clicked fallback button.")

    time.sleep(2)
    all_day_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "/html/body/main/div/div/div/div[3]/div[1]/div[2]/div[1]/div[2]/div/div[2]/div/div/table/tbody/tr[3]/td[2]/table/tbody/tr[4]/td[2]/label/input"))
    )
    all_day_btn.click()
    print("✅ Clicked 星期一至五")

    afternoon_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "/html/body/main/div/div/div/div[3]/div[1]/div[2]/div[1]/div[2]/div/div[2]/div/div/table/tbody/tr[4]/td[2]/table/tbody/tr[3]/td/label/input"))
    )
    afternoon_btn.click()
    print("✅ Clicked 下午")

    continue_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='button' and @value='繼續']"))
    )
    continue_btn.click()
    print("✅ Clicked 繼續 button")
    time.sleep(1)

    page_source = driver.page_source
    if "沒有可預約的時間" in page_source:
        print("⚠️ No available times in 下午 either.")
        return False, page_source

    print("✅ Available times found in 下午!")
    return True, page_source


def extract_slot_info(page_source: str) -> str:
    """Pull readable text from the slots page so the user can see what opened up."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_source, "html.parser")
    main = soup.find("main") or soup.find("body")
    if main:
        return main.get_text(separator="\n", strip=True)
    return "(Could not extract slot details from page.)"


def do_check(driver) -> bool:
    """
    Headless check cycle.  Navigates to the slots page, stops there,
    and returns True/False.  Does NOT navigate away when slots are found.
    """
    time_found, page_source = navigate_to_slots(driver)

    if time_found:
        notification.notify(
            title="Car Registration Alert",
            message="Available time slots found! Opening interactive browser...",
            timeout=10
        )
        with open("available_spots.html", "w", encoding="utf-8") as f:
            f.write(page_source)
        print("✅ Page source saved to 'available_spots.html'")

        slot_info = extract_slot_info(page_source)
        print("\n" + "=" * 60)
        print("AVAILABLE SLOTS DETECTED:")
        print("=" * 60)
        print(slot_info)
        print("=" * 60 + "\n")

    return time_found


def open_interactive_session(proxy_url: str | None = None):
    """
    Spawn a visible (non-headless) browser, replay the booking flow to the
    slots page, and keep it open for the user to complete the reservation
    manually.  Blocks until the user presses Enter.
    """
    print("🚀 Opening interactive browser window…")
    driver = new_driver(proxy_url, headless=False)
    try:
        time_found, _ = navigate_to_slots(driver)
        if time_found:
            print("✅ Interactive browser is showing available slots.")
            print("   → Complete your booking in the browser window that just opened.")
            input("\n⏳ Press Enter when you are done (or to skip) to resume monitoring…\n")
        else:
            print("⚠️ Slots disappeared by the time the interactive browser loaded.")
            input("Press Enter to resume monitoring…\n")
    except Exception as e:
        print(f"⚠️ Error in interactive session: {e}")
        input("Press Enter to resume monitoring…\n")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def main():
    proxy_index = 0
    proxy_url = PROXY_POOL[proxy_index] if PROXY_POOL else None
    driver = new_driver(proxy_url)

    try:
        while True:
            try:
                found = do_check(driver)
                if found:
                    # Hand off to a visible browser so the user can act
                    open_interactive_session(proxy_url)
            except Exception as e:
                print(f"⚠️ Error during check: {e}")

            print(f"\n⏳ Waiting {REFRESH_SECONDS}s before next check…")
            time.sleep(REFRESH_SECONDS)

            # Rotate proxy for the next cycle (compliant rotation via your provider)
            if PROXY_POOL:
                proxy_index = (proxy_index + 1) % len(PROXY_POOL)
                proxy_url = PROXY_POOL[proxy_index]
                print(f"🔁 Rotating proxy to: {proxy_url}")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = new_driver(proxy_url)

    except KeyboardInterrupt:
        print("\nStopping script…")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
