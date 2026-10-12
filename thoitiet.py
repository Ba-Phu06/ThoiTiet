import requests
import csv
import os  # Bắt buộc phải thêm dòng này
from datetime import datetime

endpoint = "https://api.openweathermap.org/data/2.5/weather"
api_key = os.environ.get("WEATHER_API_KEY") # Lấy Key bí mật từ GitHub

params = {
    "lat": 16.0544,
    "lon": 108.2022,
    "appid": api_key, # Chỗ này đổi thành biến api_key
    "units": "metric",
    "lang": "vi"
}
# ... (phần code bên dưới giữ nguyên)
response = requests.get(endpoint, params=params)

if response.status_code == 200:
    data = response.json()
    
    # 1. Trích xuất các thuộc tính rõ ràng cho Database
    ngay = datetime.now().strftime("%Y-%m-%d") # Định dạng chuẩn SQL: YYYY-MM-DD
    gio = datetime.now().strftime("%H:%M:%S")
    nhiet_do = data['main']['temp']
    do_am = data['main']['humidity']
    ap_suat = data['main']['pressure']
    tinh_trang = data['weather'][0]['description']
    
    filename = "Database_ThoiTiet.csv"
    file_exists = os.path.isfile(filename)
    
    # 2. Ghi vào file với cấu trúc bảng
    with open(filename, mode='a', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            # Dòng Header định nghĩa schema cho DB
            writer.writerow(["Ngay", "Gio", "Nhiet_Do_C", "Do_Am_PhanTram", "Ap_Suat_hPa", "Tinh_Trang"])
        
        writer.writerow([ngay, gio, nhiet_do, do_am, ap_suat, tinh_trang])
        
    print(f"[{ngay} {gio}] Cập nhật Database thành công!")
else:
    print(f"Lỗi {response.status_code}: {response.text}")
