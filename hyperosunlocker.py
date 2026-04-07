import hashlib
import random
import time
import os
import sys
from datetime import datetime, timezone, timedelta
import ntplib
import pytz
import urllib3
import json

# === HEADLESS MODE FOR GITHUB ACTIONS ===
# Token is read from environment variable XIAOMI_TOKEN
# No browser popups, no manual input, no colorama needed

ntp_servers = [
    "ntp0.ntp-servers.net", "ntp1.ntp-servers.net", "ntp2.ntp-servers.net",
    "ntp3.ntp-servers.net", "ntp4.ntp-servers.net", "ntp5.ntp-servers.net",
    "ntp6.ntp-servers.net"
]

token_input = os.environ.get("XIAOMI_TOKEN", "")
if not token_input:
    print("[ERROR] XIAOMI_TOKEN environment variable not set!")
    sys.exit(1)

print("[INFO] Checking Account Status...")
cookie_value = token_input.strip()
feedtime = float(1400)
feed_time_shift = feedtime
feed_time_shift_1 = feed_time_shift / 1000

MAX_REQUESTS = 50  # Safety limit to avoid infinite loop in Actions

def generate_device_id():
    random_data = f"{random.random()}-{time.time()}"
    device_id = hashlib.sha1(random_data.encode('utf-8')).hexdigest().upper()
    return device_id

def get_initial_beijing_time():
    client = ntplib.NTPClient()
    beijing_tz = pytz.timezone("Asia/Shanghai")
    for server in ntp_servers:
        try:
            print(f"[INFO] Getting Beijing time from {server}...")
            response = client.request(server, version=3)
            ntp_time = datetime.fromtimestamp(response.tx_time, timezone.utc)
            beijing_time = ntp_time.astimezone(beijing_tz)
            print(f"[OK] Beijing time: {beijing_time.strftime('%Y-%m-%d %H:%M:%S.%f')}")
            return beijing_time
        except Exception as e:
            print(f"[WARN] Failed to connect to {server}: {e}")
    print("[ERROR] All NTP Servers failed.")
    return None

def get_synchronized_beijing_time(start_beijing_time, start_timestamp):
    elapsed = time.time() - start_timestamp
    current_time = start_beijing_time + timedelta(seconds=elapsed)
    return current_time

def wait_until_target_time(start_beijing_time, start_timestamp):
    beijing_now = get_synchronized_beijing_time(start_beijing_time, start_timestamp)

    # If it's before midnight, target is today's midnight
    # If it's after midnight (e.g. 00:01), target is tomorrow's midnight
    today_midnight = beijing_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if beijing_now >= today_midnight:
        target_time = today_midnight + timedelta(days=1) - timedelta(seconds=feed_time_shift_1)
    else:
        target_time = today_midnight - timedelta(seconds=feed_time_shift_1)

    time_remaining = (target_time - beijing_now).total_seconds()

    print(f"\n[INFO] Bootloader unlock request")
    print(f"[OK] Phase Shift: {feed_time_shift:.2f} ms")
    print(f"[OK] Target fire time: {target_time.strftime('%Y-%m-%d %H:%M:%S.%f')}")
    print(f"[OK] Time remaining: {time_remaining:.1f} seconds ({time_remaining/60:.1f} minutes)")
    print("[INFO] Waiting... do not exit")

    while True:
        current_time = get_synchronized_beijing_time(start_beijing_time, start_timestamp)
        time_diff = target_time - current_time

        if time_diff.total_seconds() > 1:
            time.sleep(min(1.0, time_diff.total_seconds() - 1))
        elif current_time >= target_time:
            print(f"[OK] FIRE! Time: {current_time.strftime('%Y-%m-%d %H:%M:%S.%f')}. Starting requests!")
            break
        else:
            time.sleep(0.0001)

def check_unlock_status(session, cookie_value, device_id):
    try:
        url = "https://sgp-api.buy.mi.com/bbs/api/global/user/bl-switch/state"
        headers = {
            "Cookie": f"new_bbs_serviceToken={cookie_value};versionCode=500411;versionName=5.4.11;deviceId={device_id};"
        }

        response = session.make_request('GET', url, headers=headers)
        if response is None:
            print("[ERROR] Could not retrieve unlock status.")
            return False

        response_data = json.loads(response.data.decode('utf-8'))
        response.release_conn()

        if response_data.get("code") == 100004:
            print("[ERROR] Expired Cookie. You need to update XIAOMI_TOKEN with a fresh token.")
            sys.exit(1)

        data = response_data.get("data", {})
        is_pass = data.get("is_pass")
        button_state = data.get("button_state")
        deadline_format = data.get("deadline_format", "")

        if is_pass == 4:
            if button_state == 1:
                print("[OK] Account Status: Eligible. Requests will be sent.")
                return True
            elif button_state == 2:
                print(f"[WARN] Account Status: Requests blocked until {deadline_format} (Month/Day). Continuing anyway...")
                return True
            elif button_state == 3:
                print("[WARN] Account Status: Account created less than 30 days ago. Continuing anyway...")
                return True
        elif is_pass == 1:
            print(f"[OK] Account Status: REQUEST ALREADY APPROVED! Unlock before {deadline_format}.")
            print("[OK] Go to Developer Options > Mi Unlock Status > Add account and device!")
            sys.exit(0)
        else:
            print(f"[ERROR] Account Status: Unknown state. is_pass={is_pass}, button_state={button_state}")
            print(f"[ERROR] Full response: {response_data}")
            sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Status check failed: {e}")
        return False

class HTTP11Session:
    def __init__(self):
        self.http = urllib3.PoolManager(
            maxsize=10,
            retries=True,
            timeout=urllib3.Timeout(connect=2.0, read=15.0),
            headers={}
        )

    def make_request(self, method, url, headers=None, body=None):
        try:
            request_headers = {}
            if headers:
                request_headers.update(headers)
                request_headers['Content-Type'] = 'application/json; charset=utf-8'

            if method == 'POST':
                if body is None:
                    body = '{"is_retry":true}'.encode('utf-8')
                request_headers['Content-Length'] = str(len(body))
                request_headers['Accept-Encoding'] = 'gzip, deflate, br'
                request_headers['User-Agent'] = 'okhttp/4.12.0'
                request_headers['Connection'] = 'keep-alive'

            response = self.http.request(
                method,
                url,
                headers=request_headers,
                body=body,
                preload_content=False
            )

            return response
        except Exception as e:
            print(f"[Network Error] {e}")
            return None

def main():
    device_id = generate_device_id()
    session = HTTP11Session()

    if check_unlock_status(session, cookie_value, device_id):
        start_beijing_time = get_initial_beijing_time()
        if start_beijing_time is None:
            print("[ERROR] Failed to get Beijing time from any NTP server.")
            sys.exit(1)

        start_timestamp = time.time()

        wait_until_target_time(start_beijing_time, start_timestamp)

        url = "https://sgp-api.buy.mi.com/bbs/api/global/apply/bl-auth"
        headers = {
            "Cookie": f"new_bbs_serviceToken={cookie_value};versionCode=500411;versionName=5.4.11;deviceId={device_id};"
        }

        request_count = 0
        try:
            while request_count < MAX_REQUESTS:
                request_count += 1
                request_time = get_synchronized_beijing_time(start_beijing_time, start_timestamp)
                print(f"[Request #{request_count}] Sent at {request_time.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC+8)")

                response = session.make_request('POST', url, headers=headers)
                if response is None:
                    continue

                response_time = get_synchronized_beijing_time(start_beijing_time, start_timestamp)
                print(f"[Response] Received at {response_time.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC+8)")

                try:
                    response_data = response.data
                    response.release_conn()
                    json_response = json.loads(response_data.decode('utf-8'))
                    code = json_response.get("code")
                    data = json_response.get("data", {})

                    if code == 0:
                        apply_result = data.get("apply_result")
                        if apply_result == 1:
                            print("======================================")
                            print("[OK] REQUEST APPROVED!")
                            print("[OK] Go to your phone NOW:")
                            print("[OK] Settings > Developer Options > Mi Unlock Status > Add account and device")
                            print("======================================")
                            check_unlock_status(session, cookie_value, device_id)
                            sys.exit(0)
                        elif apply_result == 3:
                            deadline_format = data.get("deadline_format", "Not declared")
                            print(f"[FAIL] Quota reached. Try again at {deadline_format} (Month/Day).")
                            sys.exit(1)
                        elif apply_result == 4:
                            deadline_format = data.get("deadline_format", "Not declared")
                            print(f"[FAIL] Account blocked until {deadline_format} (Month/Day).")
                            sys.exit(1)
                    elif code == 100001:
                        print(f"[Status] Request rejected. Response: {json_response}")
                    elif code == 100003:
                        print(f"[Status] Possibly approved! Response: {json_response}")
                        check_unlock_status(session, cookie_value, device_id)
                    elif code is not None:
                        print(f"[Status] Unknown code: {code}. Response: {json_response}")
                    else:
                        print(f"[Error] No status code in response: {json_response}")

                except json.JSONDecodeError:
                    print(f"[Error] JSON decode error. Raw: {response_data}")
                except Exception as e:
                    print(f"[Error] Response processing: {e}")
                    continue

            print(f"[INFO] Finished {MAX_REQUESTS} requests. Check Actions log for results.")

        except Exception as e:
            print(f"[Request Error] {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
