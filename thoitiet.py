import csv
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==========================================
# 1. CẤU HÌNH HỆ THỐNG & MÚI GIỜ VIỆT NAM (UTC+7)
# ==========================================
VN_TZ = timezone(timedelta(hours=7))
CSV_FILENAME = "Database_ThoiTiet.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Giữ 6 cột gốc ở đầu để tương thích dữ liệu cũ, mở rộng thêm 30 thuộc tính mới
FIELDNAMES = [
    # 6 thuộc tính gốc
    "Ngay", "Gio", "Nhiet_Do_C", "Do_Am_PhanTram", "Ap_Suat_hPa", "Tinh_Trang",
    # Metadata & Trạm quan trắc
    "Gio_Tram_Cap_Nhat", "Dia_Diem", "Vi_Do", "Kinh_Do",
    # Weather mở rộng (Thời tiết)
    "Ma_Thoi_Tiet", "Nhom_Thoi_Tiet", "Nhiet_Do_Cam_Nhan_C",
    "Nhiet_Do_Min_C", "Nhiet_Do_Max_C", "Ap_Suat_Bien_hPa", "Ap_Suat_Mat_Dat_hPa",
    "Tam_Nhin_Xa_m", "Luong_Mua_1h_mm",
    # Energy (Năng lượng Gió & Mặt trời)
    "Toc_Do_Gio_ms", "Huong_Gio_Do", "Gio_Giat_ms", "Mat_Do_Cong_Suat_Gio_Wm2",
    "Do_Che_Phu_May_PhanTram", "Gio_Mat_Troi_Moc", "Gio_Mat_Troi_Lan",
    "So_Gio_Ban_Ngay", "Dang_Ban_Ngay",
    # Environment (Môi trường & Chất lượng không khí - ug/m3)
    "Chi_So_AQI", "Muc_Do_AQI", "PM2_5", "PM10", "CO", "NO2", "O3", "SO2"
]

AQI_LABELS = {
    1: "Tốt",
    2: "Khá",
    3: "Trung bình",
    4: "Kém",
    5: "Rất kém"
}


# ==========================================
# 2. LỚP THU THẬP DỮ LIỆU (COLLECTOR)
# ==========================================
class OpenWeatherCollector:
    BASE_URL = "https://api.openweathermap.org/data/2.5"

    def __init__(self, api_key: str, timeout: int = 15):
        if not api_key:
            raise ValueError("Thiếu biến môi trường WEATHER_API_KEY!")
        self.api_key = api_key
        self.timeout = timeout
        self.session = self._init_session()

    @staticmethod
    def _init_session() -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _fetch_endpoint(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.BASE_URL}/{endpoint}"
        query_params = {**params, "appid": self.api_key}
        try:
            response = self.session.get(url, params=query_params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Lỗi khi gọi API {endpoint}: {e}")
            return None

    @staticmethod
    def _calc_wind_power_density(wind_speed: float, temp_c: float, pressure_hpa: float) -> float:
        """Tính mật độ công suất gió lý thuyết: P/A = 0.5 * rho * v^3 (W/m^2)."""
        if wind_speed <= 0:
            return 0.0
        r_specific = 287.058
        temp_k = temp_c + 273.15
        pressure_pa = pressure_hpa * 100.0
        air_density = pressure_pa / (r_specific * temp_k)
        return round(0.5 * air_density * (wind_speed ** 3), 2)

    @staticmethod
    def _unix_to_vn_time(timestamp: Optional[int], fmt: str = "%H:%M:%S") -> str:
        if not timestamp:
            return ""
        return datetime.fromtimestamp(timestamp, tz=VN_TZ).strftime(fmt)

    def collect_all(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        # 1. Gọi API Thời tiết hiện tại
        weather_data = self._fetch_endpoint(
            "weather",
            {"lat": lat, "lon": lon, "units": "metric", "lang": "vi"}
        )
        if not weather_data:
            return None

        # 2. Gọi API Ô nhiễm không khí
        air_data = self._fetch_endpoint(
            "air_pollution",
            {"lat": lat, "lon": lon}
        )

        # Tính thời gian chuẩn Việt Nam (UTC+7)
        vn_now = datetime.now(VN_TZ)

        main_blk = weather_data.get("main", {})
        wind_blk = weather_data.get("wind", {})
        clouds_blk = weather_data.get("clouds", {})
        rain_blk = weather_data.get("rain", {})
        sys_blk = weather_data.get("sys", {})
        weather_list = weather_data.get("weather", [{}])
        w_info = weather_list[0] if weather_list else {}

        temp_c = main_blk.get("temp", 0.0)
        pressure_hpa = main_blk.get("pressure", 1013.25)
        wind_speed = wind_blk.get("speed", 0.0)
        wind_power_wm2 = self._calc_wind_power_density(wind_speed, temp_c, pressure_hpa)

        sunrise_ts = sys_blk.get("sunrise")
        sunset_ts = sys_blk.get("sunset")
        current_ts = weather_data.get("dt", int(vn_now.timestamp()))

        daylight_hours = (
            round((sunset_ts - sunrise_ts) / 3600.0, 2)
            if (sunrise_ts and sunset_ts) else 0.0
        )
        is_daylight = (
            1 if (sunrise_ts and sunset_ts and sunrise_ts <= current_ts <= sunset_ts) else 0
        )

        air_list = air_data.get("list", [{}]) if air_data else [{}]
        air_first = air_list[0] if air_list else {}
        aqi_val = air_first.get("main", {}).get("aqi")
        comp_blk = air_first.get("components", {})

        return {
            # 6 cột gốc
            "Ngay": vn_now.strftime("%Y-%m-%d"),
            "Gio": vn_now.strftime("%H:%M:%S"),
            "Nhiet_Do_C": temp_c,
            "Do_Am_PhanTram": main_blk.get("humidity", ""),
            "Ap_Suat_hPa": pressure_hpa,
            "Tinh_Trang": w_info.get("description", ""),

            # Metadata
            "Gio_Tram_Cap_Nhat": self._unix_to_vn_time(current_ts, "%Y-%m-%d %H:%M:%S"),
            "Dia_Diem": weather_data.get("name", "Da Nang"),
            "Vi_Do": weather_data.get("coord", {}).get("lat", lat),
            "Kinh_Do": weather_data.get("coord", {}).get("lon", lon),

            # Weather mở rộng
            "Ma_Thoi_Tiet": w_info.get("id", ""),
            "Nhom_Thoi_Tiet": w_info.get("main", ""),
            "Nhiet_Do_Cam_Nhan_C": main_blk.get("feels_like", ""),
            "Nhiet_Do_Min_C": main_blk.get("temp_min", ""),
            "Nhiet_Do_Max_C": main_blk.get("temp_max", ""),
            "Ap_Suat_Bien_hPa": main_blk.get("sea_level", ""),
            "Ap_Suat_Mat_Dat_hPa": main_blk.get("grnd_level", ""),
            "Tam_Nhin_Xa_m": weather_data.get("visibility", ""),
            "Luong_Mua_1h_mm": rain_blk.get("1h", 0.0),

            # Energy
            "Toc_Do_Gio_ms": wind_speed,
            "Huong_Gio_Do": wind_blk.get("deg", ""),
            "Gio_Giat_ms": wind_blk.get("gust", 0.0),
            "Mat_Do_Cong_Suat_Gio_Wm2": wind_power_wm2,
            "Do_Che_Phu_May_PhanTram": clouds_blk.get("all", ""),
            "Gio_Mat_Troi_Moc": self._unix_to_vn_time(sunrise_ts),
            "Gio_Mat_Troi_Lan": self._unix_to_vn_time(sunset_ts),
            "So_Gio_Ban_Ngay": daylight_hours,
            "Dang_Ban_Ngay": is_daylight,

            # Environment
            "Chi_So_AQI": aqi_val if aqi_val is not None else "",
            "Muc_Do_AQI": AQI_LABELS.get(aqi_val, ""),
            "PM2_5": comp_blk.get("pm2_5", ""),
            "PM10": comp_blk.get("pm10", ""),
            "CO": comp_blk.get("co", ""),
            "NO2": comp_blk.get("no2", ""),
            "O3": comp_blk.get("o3", ""),
            "SO2": comp_blk.get("so2", "")
        }


# ==========================================
# 3. LƯU CSV & TỰ ĐỘNG NÂNG CẤP CỘT MỚI
# ==========================================
def save_to_csv(record: Dict[str, Any], filename: str) -> None:
    file_exists = os.path.isfile(filename) and os.path.getsize(filename) > 0

    if file_exists:
        with open(filename, mode="r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            current_headers = reader.fieldnames or []
            # Nếu file cũ chỉ có 6 cột, đọc toàn bộ dữ liệu cũ và nâng cấp lên 36 cột
            if current_headers != FIELDNAMES:
                logger.info("Phát hiện cấu trúc CSV cũ (6 cột). Đang nâng cấp lên 36 thuộc tính...")
                old_rows = list(reader)
                with open(filename, mode="w", encoding="utf-8-sig", newline="") as out_file:
                    writer = csv.DictWriter(out_file, fieldnames=FIELDNAMES, extrasaction="ignore")
                    writer.writeheader()
                    for old_row in old_rows:
                        writer.writerow(old_row)
                    writer.writerow(record)
                logger.info("Đã nâng cấp bảng CSV và thêm dòng dữ liệu mới thành công!")
                return

    # Nếu file chưa tồn tại hoặc đã có sẵn 36 cột chuẩn
    with open(filename, mode="a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)
    logger.info(f"Đã ghi thêm dữ liệu lúc {record['Ngay']} {record['Gio']} (Giờ VN).")


def main():
    api_key = os.environ.get("WEATHER_API_KEY")
    collector = OpenWeatherCollector(api_key=api_key)

    # Tọa độ Đà Nẵng
    record = collector.collect_all(lat=16.0544, lon=108.2022)
    if record:
        save_to_csv(record, CSV_FILENAME)


if __name__ == "__main__":
    main()
