import requests
import csv
import os
from datetime import datetime, timezone, timedelta

endpoint = "https://api.openweathermap.org/data/2.5/weather"
api_key = os.environ.get("WEATHER_API_KEY")

params = {
    "lat": 16.0544,
    "lon": 108.2022,
    "appid": api_key,
    "units": "metric",
    "lang": "vi"
}

response = requests.get(endpoint, params=params)

if response.status_code == 200:
    data = response.json()
    
    # 1. Tính thời gian chuẩn Việt Nam
    utc_now = datetime.now(timezone.utc)
    vn_now = utc_now + timedelta(hours=7)
    
    # Chỉ gán biến 1 lần duy nhất ở đây!
    ngay = vn_now.strftime("%Y-%m-%d")
    gio = vn_now.strftime("%H:%M:%S")
    
    # 2. Trích xuất dữ liệu thời tiết
    nhiet_do = data['main']['temp']
    do_am = data['main']['humidity']
    ap_suat = data['main']['pressure']
    tinh_trang = data['weather'][0]['description']
    
    filename = "Database_ThoiTiet.csv"
    file_exists = os.path.isfile(filename)
    
    # 3. Ghi vào file CSV
    with open(filename, mode='a', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["Ngay", "Gio", "Nhiet_Do_C", "Do_Am_PhanTram", "Ap_Suat_hPa", "Tinh_Trang"])
        
        writer.writerow([ngay, gio, nhiet_do, do_am, ap_suat, tinh_trang])
