import os, re, time, requests, threading, asyncio, httpx, openai, json, pyautogui, aiohttp
from json.decoder import JSONDecodeError
from datetime import datetime, timedelta
from queue import Queue
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import NoSuchElementException

TELEGRAM_API_TOKEN = 'bot api token'
SPECIAL_USER_ID = 000000000 # admin telegram id
openai.api_key = 'api key here' # in case you want to generate prompts
driver = None

def load_user_times():
    try:
        with open('user_times.json', 'r') as f:
            user_times = json.load(f)
    except JSONDecodeError:
        user_times = {}

    for user_id, value in list(user_times.items()):
        if not isinstance(value, dict):
            user_times[user_id] = {}

        for command, timestamp in list(value.items()):
            if not isinstance(timestamp, str):
                user_times[user_id][command] = {}

    return user_times
    
def save_user_times(user_times):
    user_times_copy = {}

    for user_id, value in user_times.items():
        if user_id == str(SPECIAL_USER_ID):
            continue
        if not isinstance(value, dict):
            user_times_copy[user_id] = {}
        else:
            user_times_copy[user_id] = value

    with open("user_times.json", "w") as f:
        json.dump(user_times_copy, f)

user_times = load_user_times()

def generate_img(text, api_key):
    openai.api_key = api_key
    response = openai.Image.create(
      prompt=text,
      n=1,
      size="1024x1024"
    )
    image_url = response['data'][0]['url']

    return image_url

def generate_prompt(keywords):
    messages = [
        {"role": "system", "content": f'''
         As a prompt generator for a generative AI called "Midjourney", you will create image prompts for the AI to visualize. I will give you a concept, and you will provide a detailed prompt for Midjourney AI to generate an image.
Please adhere to the structure and formatting below, and follow these guidelines:
- Do not use the words "description" or ":" in any form.
- Do not place a comma between [ar] and [v].
- Write each prompt in one line without using return.
Structure:
[1] = {keywords}
[2] = a detailed description of [1] with specific imagery details.
[3] = a detailed description of the scene's environment.
[4] = a detailed description of the scene's mood, feelings, and atmosphere.
[5] = A style (e.g. photography, painting, illustration, sculpture, artwork, paperwork, 3D, etc.) for [1].
[6] = A description of how [5] will be executed (e.g. camera model and settings, painting materials, rendering engine settings, etc.)
[ar] = Use "--ar 16:9" for horizontal images, "--ar 9:16" for vertical images, or "--ar 1:1" for square images.
[v] = Use "--niji" for Japanese art style, or "--v 5" for other styles.
Formatting: 
Follow this prompt structure: "/imagine prompt: [1], [2], [3], [4], [5], [6], [ar] [v]".
Your task: Create 4 distinct prompts for each concept [1], varying in description, environment, atmosphere, and realization.
- Write your prompts in English.
- Do not describe unreal concepts as "real" or "photographic".
- Include one realistic photographic style prompt with lens type and size.
- Separate different prompts with two new lines.
Example Prompts:
Prompt 1:
/imagine prompt: A stunning Halo Reach landscape with a Spartan on a hilltop, lush green forests surround them, clear sky, distant city view, focusing on the Spartan's majestic pose, intricate armor, and weapons, Artwork, oil painting on canvas, --ar 16:9 --v 5
Prompt 2:
/imagine prompt: A captivating Halo Reach landscape with a Spartan amidst a battlefield, fallen enemies around, smoke and fire in the background, emphasizing the Spartan's determination and bravery, detailed environment blending chaos and beauty, Illustration, digital art, --ar 16:9 --v 5
'''},
        {"role": "user", "content": keywords},
       
    ]
    response = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=messages)
    return response.choices[0].message.content

def create_chrome_driver(user_data_dir):
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--log-level=3")
    options.add_argument('--no-proxy-server')
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_argument(f'--user-data-dir={user_data_dir}')

    driver = webdriver.Chrome(options=options)
    return driver

async def send_telegram_message(chat_id, text, parse_mode=None):
    url = f'https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/sendMessage'
    data = {'chat_id': chat_id, 'text': text, 'parse_mode': parse_mode}
    async with httpx.AsyncClient() as client:
        await client.post(url, data=data)

async def telegram_bot(message_queue, user_sessions, driver, loop):
    async def can_use_command(chat_id, command):
        chat_id_str = int(chat_id)
        
        if chat_id_str not in user_times:
            user_times[chat_id_str] = {}
        
        if command not in user_times[chat_id_str]:
            user_times[chat_id_str][command] = datetime.now().isoformat()
            save_user_times(user_times)
            return True
        
        if chat_id != SPECIAL_USER_ID:
            last_used = datetime.fromisoformat(user_times[chat_id_str][command])
            time_since_last_used = datetime.now() - last_used
            if time_since_last_used < timedelta(hours=24):
                remaining_time = timedelta(hours=24) - time_since_last_used
                await send_telegram_message(chat_id, f"Time remaining for command {command}: {remaining_time}")
                return False
        
        user_times[chat_id_str][command] = datetime.now().isoformat()
        save_user_times(user_times)
        return True
    
    async def get_latest_update_id():
        url = f'https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/getUpdates'
        params = {'limit': 1, 'offset': -1}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url, params=params)
        updates = response.json()['result']
        if updates:
            return updates[0]['update_id']
        return None
        
    async def main_loop():
        url = f'https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/getUpdates'
        offset = None
        
        while True:
            params = {'timeout': 100, 'offset': offset}
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(url, params=params)
            updates = response.json()['result']

            for update in updates:
                if 'message' not in update:
                    continue
                message = update['message']
                chat_id = message['chat']['id']
                
                if 'text' in message:
                    text = message['text']
                    command = text.split()[0]
                
                    if command == '/imagine':
                        can_use_command_result = await can_use_command(chat_id, command)
                        if can_use_command_result:
                            if len(text) > 9:
                                sendo = text[9:]
                                await send_telegram_message(chat_id, f'Processing your request: {sendo}')
                                if chat_id not in user_sessions:
                                    user_sessions[chat_id] = {'username': message['from']['username']}

                                user_sessions[chat_id]['sendo'] = sendo
                                user_times[str(chat_id)] = datetime.now().isoformat()
                                save_user_times(user_times)
                                print("Calling save_user_times")
                                message_queue.put(chat_id)
                            else:
                                await send_telegram_message(chat_id, "Please provide a text after '/imagine'.")
                            
                    elif command == '/help':
                        help_text = ("*Commands*\n\n"
                                     "/start - Start the bot.\n"
                                     "/generate [text] - write keywords separated by spaces to generate prompts\n"
                                     "/imagine [text] - Send a text to be processed by Midjourney.\n"
                                     "/img [text] - Send a text to be processed by DALL-E.\n"
                                     "/U1, /U2, /U3, /U4 - Choose one of the generated images to be upscaled.\n"
                                     "/V1, /V2, /V3, /V4 - Choose one of the generated images to make variations.\n"
                                     "/make\_variations - Make variations of the chosen image.\n"
                                     "/help - Show this help message.")

                        await send_telegram_message(chat_id, help_text, parse_mode='Markdown')
                        
                    elif command == '/generate':
                        can_use_command_result = await can_use_command(chat_id, command)
                        if can_use_command_result:
                            if len(text) > 10:
                                keywords = text[10:]
                                generated_text = generate_prompt(keywords)
                                prompts = generated_text.split('\n\n')
                                for prompt in prompts:
                                    if prompt.strip():
                                        await send_telegram_message(chat_id, prompt)
                            else:
                                await send_telegram_message(chat_id, "Please provide keywords after '/generate'.")

                    elif command == '/start':
                        start_text = ("Hey! I'm an image generator.")
                        await send_telegram_message(chat_id, start_text)
                    
                    elif command in ('/V1', '/V2', '/V3', '/V4'):
                        can_use_command_result = await can_use_command(chat_id, command)
                        if can_use_command_result:
                            button_label = text[1:]
                            user_session = user_sessions.get(chat_id, None)
                            if not user_session:
                                continue

                            user_msg = user_session['sendo']
                            username = user_session['username']
                            try:
                                time.sleep(1)
                                await click_button_v(driver, user_msg, button_label)
                                await send_telegram_message(chat_id, f"Version requested {text}.")
                                
                                if '--v 5' in user_msg:
                                    await asyncio.sleep(20)
                                else:
                                    await asyncio.sleep(30)
                                    
                                image_url = None
                                while not image_url:
                                    image_url = find_last_upscaled_image(driver, user_msg, button_label)
                                    if not image_url:
                                        print("Version image not found after the user text. Retrying...")
                                        await asyncio.sleep(5)
                                        
                                print(f"Image URL: {image_url}")
                                local_image_path = f'downloaded_image_version_{button_label}_{username}.png'
                                await download_image(image_url, local_image_path)
                                await send_telegram_photo(chat_id, local_image_path, caption=f'@{username}')
                                
                            except NoSuchElementException:
                                await send_telegram_message(chat_id, f"Could not find button {text}. Please try again.")

                    elif command in ('/U1', '/U2', '/U3', '/U4'):
                        can_use_command_result = await can_use_command(chat_id, command)
                        if can_use_command_result:
                            button_number = int(text[2])
                            user_session = user_sessions.get(chat_id, None)
                            if not user_session:
                                continue

                            user_msg = user_session['sendo']
                            username = user_session['username']
                            try:
                                time.sleep(1)
                                await click_button(driver, user_msg, button_number)
                                await send_telegram_message(chat_id, f"Upscale requested {text}.")
                                
                                if '--v 5' in user_msg:
                                    await asyncio.sleep(3)
                                else:
                                    await asyncio.sleep(20)

                                image_url = None
                                while not image_url:
                                    image_url = find_last_upscaled_image(driver, user_msg, button_number)
                                    if not image_url:
                                        print("Upscaled image not found after the user text. Retrying...")
                                        await asyncio.sleep(5)
                                        
                                print(f"Image URL: {image_url}")
                                local_image_path = f'downloaded_image_upscaled_{username}.png'
                                await download_image(image_url, local_image_path)
                                await send_telegram_photo(chat_id, local_image_path, caption=f'@{username}')
                                
                            except NoSuchElementException:
                                await send_telegram_message(chat_id, f"Could not find button {text}. Please try again.")
                    
                    elif command.startswith("/img"):
                        question = text[4:].strip()
                        if not question:
                            await send_telegram_message(chat_id, "Please provide a message after the /img command.")
                        else:
                            image_url = generate_img(question, "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")
                            local_image_path = 'dalle_edited_image.png'
                            await download_image(image_url, local_image_path)
                            await send_telegram_photo(chat_id, local_image_path)
                            os.remove(local_image_path)
                    
                    elif command == '/make_variations':
                        can_use_command_result = await can_use_command(chat_id, command)
                        if can_use_command_result:
                            user_session = user_sessions.get(chat_id, None)
                            if not user_session or 'sendo' not in user_session:
                                await send_telegram_message(chat_id, "Please use the /make_variations command after providing the /imagine command and one of the commands '/U1, /U2, /U3, /U4'.")
                            else:
                                try:
                                    time.sleep(1)
                                    user_msg = user_session['sendo']
                                    await click_button(driver, user_msg, 1)
                                    await send_telegram_message(chat_id, "Clicked the variations button.")
                                    user_times[str(chat_id)] = datetime.now().isoformat()
                                    save_user_times(user_times)
                                    print("Calling save_user_times")
                                    
                                    await asyncio.sleep(20)

                                    image_url = None
                                    while not image_url:
                                        image_url = find_last_variation_image(driver, user_msg)
                                        if not image_url:
                                            print("Variations image not found after the user text. Retrying...")
                                            await asyncio.sleep(5)
                                            
                                    print(f"Image URL: {image_url}")
                                    local_image_path = f'downloaded_image_variations_{username}.png'
                                    await download_image(image_url, local_image_path)
                                    await send_telegram_photo(chat_id, local_image_path, caption=f'@{username}')
                                except NoSuchElementException:
                                    await send_telegram_message(chat_id, "Could not find the variations button. Please try again after you receive your upscaled picture.")

                    offset = update['update_id'] + 1
                
                elif 'photo' in message:
                    if 'caption' in message and message['caption'].startswith('/imagine'): 
                        caption = message['caption'][9:].strip()  
                        largest_photo = max(message['photo'], key=lambda x: x['file_size'])
                        file_id = largest_photo['file_id']
                        file_unique_id = largest_photo['file_unique_id']

                        can_use_command_result = await can_use_command(chat_id, '/imagine')
                        if can_use_command_result:
                            local_image_path = f"{caption}_{file_unique_id}.jpg"
                            await download_telegram_file(file_id, local_image_path)
                            
                            if chat_id not in user_sessions:
                                user_sessions[chat_id] = {'username': message['from'].get('username', '')}
                                
                            user_sessions[chat_id]['image_path'] = local_image_path

                            await handle_uploaded_image(chat_id, user_sessions, driver, file_unique_id, caption)

                offset = update['update_id'] + 1

    latest_update_id = await get_latest_update_id()
    if latest_update_id is not None:
        offset = latest_update_id + 1
    else:
        offset = None

    await main_loop()

def find_last_image(driver, user_text):
    user_text_pattern = re.compile(f"{user_text}")
    page_source = driver.page_source
    image_urls = re.findall(r'https://cdn\.discordapp\.com/attachments/\d+/\d+/[\w-]+\.png', page_source)

    last_user_text_element_index = None
    user_text_elements = list(user_text_pattern.finditer(page_source))

    if not user_text_elements:
        print('User text not found.')
        return None

    last_user_text_element = user_text_elements[-1]
    last_user_text_element_index = page_source.find(last_user_text_element.group())
    for index, image_url in enumerate(image_urls):
        if page_source.find(image_url) > last_user_text_element_index:
            return image_url

    print('No image found after the user text.')
    return None

def find_last_upscaled_image(driver, user_text, button_number):
    user_text_pattern = re.compile(f"{user_text}.*(Variations by|Upscaled by|Image #{button_number}).+@midjourney01")
    page_source = driver.page_source
    image_urls = re.findall(r'https://cdn\.discordapp\.com/attachments/\d+/\d+/[\w-]+\.png', page_source)

    last_user_text_element_index = None
    user_text_elements = list(user_text_pattern.finditer(page_source))

    if not user_text_elements:
        print('User text not found.')
        return None

    last_user_text_element = user_text_elements[-1]
    last_user_text_element_index = page_source.find(last_user_text_element.group())

    found_images = 0
    for index, image_url in enumerate(image_urls):
        if page_source.find(image_url) > last_user_text_element_index:
            found_images += 1
            if found_images == 2:
                return image_url

    print('No upscaled image found after the user text.')
    return None

async def download_image(image_url, local_path):
    async with httpx.AsyncClient() as client:
        async with client.stream('GET', image_url) as resp:
            with open(local_path, 'wb') as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)

async def send_telegram_photo(chat_id, photo_path, caption=None):
    url = f'https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/sendPhoto'
    with open(photo_path, 'rb') as photo:
        files = {'photo': photo}
        data = {'chat_id': chat_id, 'caption': caption}
        async with httpx.AsyncClient() as client:
            await client.post(url, files=files, data=data)
            
async def click_button(driver, user_text, button_number):
    user_text_elements = driver.find_elements(By.XPATH, f"//*[contains(text(), '{user_text}')]")

    if not user_text_elements:
        print('User text not found.')
        return

    last_user_text_element = user_text_elements[-1]
    buttons_elements = driver.find_elements(By.XPATH, f"//*[contains(@id, 'message-accessories-')]/div[2]/div[1]/div/button[{button_number}]")

    corresponding_button_element = None

    for button_element in buttons_elements:
        if last_user_text_element.location['y'] < button_element.location['y']:
            corresponding_button_element = button_element
            break

    if corresponding_button_element:
        corresponding_button_element.click()
    else:
        print(f'No button {button_number} found after the user text.')

async def click_button_v(driver, user_text, button_label):
    user_text_elements = driver.find_elements(By.XPATH, f"//*[contains(text(), '{user_text}')]")

    if not user_text_elements:
        print('User text not found.')
        return

    last_user_text_element = user_text_elements[-1]
    buttons_elements = driver.find_elements(By.XPATH, f'//button[.//div[contains(@class,"label-") and text()="{button_label}"]]')

    corresponding_button_element = None

    for button_element in buttons_elements:
        if last_user_text_element.location['y'] < button_element.location['y']:
            corresponding_button_element = button_element
            break

    if corresponding_button_element:
        corresponding_button_element.click()
    else:
        print(f'No button {button_label} found after the user text.')

def find_last_variation_image(driver, user_text):
    user_text_pattern = re.compile(f"{user_text}.+Variations by.+midjourney01")
    page_source = driver.page_source

    user_text_elements = list(user_text_pattern.finditer(page_source))

    if not user_text_elements:
        print('User text not found.')
        return None

    last_user_text_element = user_text_elements[-1]
    last_user_text_element_index = page_source.find(last_user_text_element.group())

    image_urls = re.findall(r'https://cdn\.discordapp\.com/attachments/\d+/\d+/[\w-]+\.png', page_source)

    for image_url in reversed(image_urls):
        image_url_index = page_source.find(image_url)
        if image_url_index > last_user_text_element_index:
            return image_url

    print('No variation image found after the user text.')
    return None

def handle_file_upload_dialog(driver, local_image_path, new_image_path):
    time.sleep(1.5)

    pyautogui.typewrite(local_image_path, interval=0.001)

    pyautogui.press('enter')
    numero = WebDriverWait(driver, 10).until(ec.visibility_of_element_located((By.XPATH, '//*[@id="app-mount"]/div[2]/div[1]/div[1]/div/div[2]/div/div/div/div/div[3]/div[2]/main/form/div/div[1]/div/div[3]/div/div[2]')))
    numero.send_keys(Keys.RETURN)

    WebDriverWait(driver, 10).until(ec.visibility_of_element_located((By.CSS_SELECTOR, "img.lazyImg-ewiNCh")))

    timeout = 15
    start_time = time.time()
    last_image_url = None

    while time.time() - start_time < timeout:
        image_elements = driver.find_elements(By.CSS_SELECTOR, "img.lazyImg-ewiNCh")
        for img_element in reversed(image_elements):
            img_url = img_element.get_attribute("src")
            if new_image_path in img_url:
                last_image_url = img_url
                break
        if last_image_url:
            break
        time.sleep(1)

    if last_image_url:
        return last_image_url
    else:
        return "Image URL not found"
    
async def download_telegram_file(bot, file_id, local_file_path):
    file_info = await bot.get_file(file_id)
    file_path = file_info.file_path
    url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            with open(local_file_path, "wb") as f:
                while True:
                    chunk = await resp.content.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    print(f"Downloading {file_id}: {len(chunk)} bytes written")

async def download_telegram_file(file_id, local_path):
    url = f"https://api.telegram.org/bot{TELEGRAM_API_TOKEN}/getFile?file_id={file_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            result = await response.json()
            file_path = result['result']['file_path']

    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_API_TOKEN}/{file_path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(download_url) as response:
            with open(local_path, 'wb') as file:
                while True:
                    chunk = await response.content.read(1024)
                    if not chunk:
                        break
                    file.write(chunk)

async def handle_uploaded_image(chat_id, user_sessions, driver, file_unique_id, caption):
    local_image_path = user_sessions[chat_id]['image_path']
    username = user_sessions[chat_id]['username']

    safe_caption = re.sub(r'\W+', '-', caption).strip('-')
    prompt_msg = safe_caption.replace('-', ' ')

    file_unique_id = re.sub(r'\W+', '-', file_unique_id).strip('-')
    new_image_path = f"{safe_caption}-{file_unique_id}.jpg"

    os.rename(local_image_path, new_image_path)

    upload_button = WebDriverWait(driver, 10).until(ec.visibility_of_element_located((By.CSS_SELECTOR, 'button.attachButton-_ACFSu')))
    upload_button.click()
    
    upload_button2 = WebDriverWait(driver, 10).until(ec.visibility_of_element_located((By.ID, 'channel-attach-upload-file')))
    upload_button2.click()
    
    uploaded_img_url = handle_file_upload_dialog(driver, new_image_path, new_image_path)
    numero = WebDriverWait(driver, 10).until(ec.visibility_of_element_located((By.XPATH, '//*[@id="app-mount"]/div[2]/div[1]/div[1]/div/div[2]/div/div/div/div/div[3]/div[2]/main/form/div/div[1]/div/div[3]/div/div[2]')))
    numero.send_keys('/ima')
    time.sleep(1)
    numero.send_keys(Keys.RETURN)
    time.sleep(1)
    sendo = numero.send_keys(uploaded_img_url+ ' ' + prompt_msg)
    time.sleep(1)
    numero.send_keys(Keys.RETURN)
    
    await asyncio.sleep(25)
    
    image_url = None
    while not image_url:
        image_url = find_last_image(driver, prompt_msg)
        if not image_url:
            print("Image not found after the user text. Retrying...")
            time.sleep(5)
            
    print(f"Image URL: {image_url}")
    local_image_path = f'edited_image.png_{username}.png'
    await download_image(image_url, local_image_path)
    await send_telegram_photo(chat_id, local_image_path, caption=f'@{username}')

async def main(message_queue, user_sessions, driver):
    try:
        numero = WebDriverWait(driver, 5).until(ec.visibility_of_element_located((By.XPATH, '//*[@id="app-mount"]/div[2]/div[1]/div[1]/div/div/div/section/div[2]/button[2]')))
        numero.click()
        print('Scan QR code or log in, then press ENTER')
        input()
    except:
        print('Already logged in.')

    while True:
        if not message_queue.empty():
            chat_id = message_queue.get()
            user_session = user_sessions[chat_id]
            username = user_session['username']
            
            if 'image_path' in user_session:
                local_image_path = user_session['image_path']

                os.remove(local_image_path)

                user_session.pop('image_path', None)
            elif 'sendo' in user_session:
                user_msg = user_session['sendo']
                numero = WebDriverWait(driver, 10).until(ec.visibility_of_element_located((By.XPATH, '//*[@id="app-mount"]/div[2]/div[1]/div[1]/div/div[2]/div/div/div/div/div[3]/div[2]/main/form/div/div[1]/div/div[3]/div/div[2]')))
                numero.send_keys('/ima')
                time.sleep(1)
                numero.send_keys(Keys.RETURN)
                time.sleep(1)
                sendo = numero.send_keys(user_msg)
                time.sleep(1)
                numero.send_keys(Keys.RETURN)
                await asyncio.sleep(20)
                
                image_url = None
                user_text = user_msg.replace('"', '')
                print(f"User text: {user_text}")
                while not image_url:
                    image_url = find_last_image(driver, user_text)
                    if not image_url:
                        print("Image not found after the user text. Retrying...")
                        time.sleep(5)

                print(f"Image URL: {image_url}")
                local_image_path = f'downloaded_image.png_{username}.png'
                await download_image(image_url, local_image_path)
                await send_telegram_photo(chat_id, local_image_path, caption=f'@{username}')

def main_thread(message_queue, user_sessions):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main(message_queue, user_sessions, driver))

def telegram_thread(message_queue, user_sessions, driver):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(telegram_bot(message_queue, user_sessions, driver, loop))
    loop.close()

if __name__ == "__main__":
    message_queue = Queue()
    user_sessions = {}

    user_data_dir = 'data'
    if not os.path.exists(user_data_dir):
        os.makedirs(user_data_dir)

    driver = create_chrome_driver(user_data_dir)
    driver.get('https://discord.com/channels/00000000000000')

    telegram_thread = threading.Thread(target=telegram_thread, args=(message_queue, user_sessions, driver), daemon=True)
    main_thread = threading.Thread(target=main_thread, args=(message_queue, user_sessions), daemon=True)

    telegram_thread.start()
    main_thread.start()

    telegram_thread.join()
    main_thread.join()
