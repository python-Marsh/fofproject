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

def build_chrome_options(proxy_url: str | None) -> Options:
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--disable-geolocation")
    chrome_options.add_argument("--incognito")
    chrome_options.add_argument("--headless=new")
    # Fresh user profile per session (keeps sessions isolated and ephemeral)
    user_data_dir = tempfile.mkdtemp(prefix="selenium-profile-")
    chrome_options.add_argument(f"--user-data-dir={user_data_dir}")

    if HEADLESS:
        # Headless "new" mode works better with modern Chrome
        chrome_options.add_argument("--headless=new")

    if proxy_url:
        # Use a licensed, provider-approved proxy. Do not use this to bypass security or rate limits.
        chrome_options.add_argument(f"--proxy-server={proxy_url}")

    return chrome_options

def new_driver(proxy_url: str | None):
    options = build_chrome_options(proxy_url)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def do_check(driver) -> bool:
    """
    Returns True if an available time is found (so we notify and try to advance),
    else False.
    """
    wait = WebDriverWait(driver, 15)

    # Open the page
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

    # Step 5: Second '開始預約' button
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
    print("✅ Clicked second 開始預約 button")

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
    time_found = False

    if "沒有可預約的時間" in page_source:
        print("⚠️ No available times in 上午, trying fallback...")
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
        print("✅ Clicked 星期 一至五")

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
            print("⚠️ No available times in 下午.")
        else:
            time_found = True
            print("✅ Available times found in 下午!")
    else:
        time_found = True
        print("✅ Available times found in 上午!")

    if time_found:
        notification.notify(
            title="Selenium Alert",
            message="Time has been found. Manual action may be required.",
            timeout=10
        )
        filename = "available_spots.txt"
        html_source = driver.page_source
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html_source)
        print(f"✅ Page source of available spots saved to '{filename}'")

        try:
            continue_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//input[@type='button' and @value='繼續']"))
            )
            continue_btn.click()
            filename = "unknown_page.txt"
            html_source = driver.page_source
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html_source)
            print(f"✅ Page source or next page of available spots saved to '{filename}'")
        except Exception as e:
            print(f"⚠️ Could not click 繼續 button: {e}")

    return time_found

def main():
    # Start with the first proxy (or None if no proxies provided)
    proxy_index = 0
    proxy_url = PROXY_POOL[proxy_index] if PROXY_POOL else None
    driver = new_driver(proxy_url)

    try:
        while True:
            try:
                found = do_check(driver)
            except Exception as e:
                print(f"⚠️ Error during check: {e}")

            # Wait before next cycle
            time.sleep(REFRESH_SECONDS)

            # Rotate proxy for the next cycle (compliant rotation via your provider)
            if PROXY_POOL:
                proxy_index = (proxy_index + 1) % len(PROXY_POOL)
                next_proxy = PROXY_POOL[proxy_index]
                print(f"🔁 Rotating proxy to: {next_proxy}")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = new_driver(next_proxy)

            else:
                # No proxy rotation: refresh the same session by reopening the page next loop
                pass

    except KeyboardInterrupt:
        print("\nStopping script...")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
