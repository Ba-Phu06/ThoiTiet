import csv
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==========================================
# 1. CẤU HÌNH HỆ THỐNG & LOGGING
# ==========================================
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CSV_FILENAME = "Database_Weather_Energy_Env.csv"
LOG_FILENAME = "weather_scraper.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILENAME, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Định nghĩa Schema cột cố định cho file CSV
FIELDNAMES = [
    # Metadata & Thời gian
    "scrape_date", "scrape_time", "station_updated_at", "location_name", "lat", "lon",
    # Weather (Thời tiết)
    "weather_code", "weather_main", "weather_desc",
    "temp_c", "feels_like_c", "temp_min_c", "temp_max_c",
    "humidity_pct", "pressure_hpa", "sea_level_hpa", "grnd_level_hpa",
    "visibility_m", "rain_1h_mm",
    # Energy (Năng lượng Gió & Mặt trời)
    "wind_speed_ms", "wind_deg", "wind_gust_ms", "wind_power_density_wm2",
    "cloud_cover_pct", "sunrise_time", "sunset_time", "daylight_hours", "is_daylight",
    # Environment (Chất lượng không khí - đơn vị ug/m3)
    "aqi_index", "aqi_category", "pm2_5", "pm10", "co", "no2", "o3", "so2"
]

AQI_LABELS = {
    1: "Tot (Good)",
    2: "Kha (Fair)",
    3: "Trung binh (Moderate)",
    4: "Kem (Poor)",
    5: "Rat kem (Very Poor)"
}


# ==========================================
# 2. LỚP THU THẬP DỮ LIỆU (COLLECTOR)
# ==========================================
class OpenWeatherCollector:
    BASE_URL = "https://api.openweathermap.org/data/2.5"

    def __init__(self, api_key: str, timeout: int = 10):
        if not api_key:
            raise ValueError("Thiếu biến môi trường WEATHER_API_KEY!")
        self.api_key = api_key
        self.timeout = timeout
        self.session = self._init_session()

    @staticmethod
    def _init_session() -> requests.Session:
        """Khởi tạo Session có cơ chế tự động thử lại (Retry) khi mạng chập chờn."""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,  # Chờ 1s, 2s, 4s giữa các lần thử lại
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _fetch_endpoint(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Hàm gọi API an toàn với xử lý ngoại lệ tập trung."""
        url = f"{self.BASE_URL}/{endpoint}"
        query_params = {**params, "appid": self.api_key}
        try:
            response = self.session.get(url, params=query_params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Lỗi HTTP tại {endpoint} (Status {response.status_code}): {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Lỗi kết nối mạng tại {endpoint}: {e}")
        return None

    @staticmethod
    def _calc_wind_power_density(wind_speed: float, temp_c: float, pressure_hpa: float) -> float:
        """
        Tính mật độ công suất gió lý thuyết: P/A = 0.5 * rho * v^3 (W/m^2)
        Trong đó mật độ không khí khô: rho = P / (R_specific * T_kelvin)
        """
        if wind_speed <= 0:
            return 0.0
        r_specific = 287.058  # J/(kg·K)
        temp_k = temp_c + 273.15
        pressure_pa = pressure_hpa * 100.0
        air_density = pressure_pa / (r_specific * temp_k)
        power_density = 0.5 * air_density * (wind_speed ** 3)
        return round(power_density, 2)

    @staticmethod
    def _unix_to_vn_time(timestamp: Optional[int], fmt: str = "%H:%M:%S") -> str:
        if not timestamp:
            return ""
        return datetime.fromtimestamp(timestamp, tz=VN_TZ).strftime(fmt)

    def collect_all(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """Thu thập và hợp nhất dữ liệu Weather, Energy và Environment."""
        # 1. Gọi API Thời tiết hiện tại
        weather_data = self._fetch_endpoint(
            "weather",
            {"lat": lat, "lon": lon, "units": "metric", "lang": "vi"}
        )
        if not weather_data:
            return None

        # 2. Gọi API Ô nhiễm không khí (Môi trường)
        air_data = self._fetch_endpoint(
            "air_pollution",
            {"lat": lat, "lon": lon}
        )

        # Thời gian cào dữ liệu chuẩn giờ Việt Nam
        vn_now = datetime.now(VN_TZ)

        # Trích xuất các khối dữ liệu con an toàn bằng .get()
        main_blk = weather_data.get("main", {})
        wind_blk = weather_data.get("wind", {})
        clouds_blk = weather_data.get("clouds", {})
        rain_blk = weather_data.get("rain", {})
        sys_blk = weather_data.get("sys", {})
        weather_list = weather_data.get("weather", [{}])
        w_info = weather_list[0] if weather_list else {}

        # Tính toán các chỉ số Năng lượng (Energy Metrics)
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

        # Trích xuất dữ liệu Môi trường (Air Quality)
        air_list = air_data.get("list", [{}]) if air_data else [{}]
        air_first = air_list[0] if air_list else {}
        aqi_val = air_first.get("main", {}).get("aqi")
        comp_blk = air_first.get("components", {})

        return {
            # Metadata & Thời gian
            "scrape_date": vn_now.strftime("%Y-%m-%d"),
            "scrape_time": vn_now.strftime("%H:%M:%S"),
            "station_updated_at": self._unix_to_vn_time(current_ts, "%Y-%m-%d %H:%M:%S"),
            "location_name": weather_data.get("name", "Unknown"),
            "lat": weather_data.get("coord", {}).get("lat", lat),
            "lon": weather_data.get("coord", {}).get("lon", lon),

            # Weather
            "weather_code": w_info.get("id", ""),
            "weather_main": w_info.get("main", ""),
            "weather_desc": w_info.get("description", ""),
            "temp_c": temp_c,
            "feels_like_c": main_blk.get("feels_like", ""),
            "temp_min_c": main_blk.get("temp_min", ""),
            "temp_max_c": main_blk.get("temp_max", ""),
            "humidity_pct": main_blk.get("humidity", ""),
            "pressure_hpa": pressure_hpa,
            "sea_level_hpa": main_blk.get("sea_level", ""),
            "grnd_level_hpa": main_blk.get("grnd_level", ""),
            "visibility_m": weather_data.get("visibility", ""),
            "rain_1h_mm": rain_blk.get("1h", 0.0),

            # Energy
            "wind_speed_ms": wind_speed,
            "wind_deg": wind_blk.get("deg", ""),
            "wind_gust_ms": wind_blk.get("gust", 0.0),
            "wind_power_density_wm2": wind_power_wm2,
            "cloud_cover_pct": clouds_blk.get("all", ""),
            "sunrise_time": self._unix_to_vn_time(sunrise_ts),
            "sunset_time": self._unix_to_vn_time(sunset_ts),
            "daylight_hours": daylight_hours,
            "is_daylight": is_daylight,

            # Environment
            "aqi_index": aqi_val if aqi_val is not None else "",
            "aqi_category": AQI_LABELS.get(aqi_val, ""),
            "pm2_5": comp_blk.get("pm2_5", ""),
            "pm10": comp_blk.get("pm10", ""),
            "co": comp_blk.get("co", ""),
            "no2": comp_blk.get("no2", ""),
            "o3": comp_blk.get("o3", ""),
            "so2": comp_blk.get("so2", "")
        }


# ==========================================
# 3. HÀM LƯU TRỮ CSV AN TOÀN
# ==========================================
def save_to_csv(record: Dict[str, Any], filename: str) -> None:
    file_exists = os.path.isfile(filename) and os.path.getsize(filename) > 0
    try:
        with open(filename, mode="a", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
            if not file_exists:
                writer.writeheader()
            writer.writerow(record)
        logger.info(
            f"Đã lưu thành công: {record['location_name']} | "
            f"Nhiệt độ: {record['temp_c']}°C | AQI: {record['aqi_index']} | "
            f"Gió: {record['wind_power_density_wm2']} W/m²"
        )
    except OSError as e:
        logger.error(f"Lỗi khi ghi file CSV '{filename}': {e}")


# ==========================================
# 4. ĐIỂM KHỞI CHẠY (ENTRY POINT)
# ==========================================
def main():
    api_key = os.environ.get("WEATHER_API_KEY")
    collector = OpenWeatherCollector(api_key=api_key)

    # Tọa độ Đà Nẵng (có thể mở rộng thành danh sách nhiều tỉnh/thành phố)
    locations = [
        {"name": "Đà Nẵng", "lat": 16.0544, "lon": 108.2022},
    ]

    for loc in locations:
        record = collector.collect_all(lat=loc["lat"], lon=loc["lon"])
        if record:
            save_to_csv(record, CSV_FILENAME)


if __name__ == "__main__":
    main()
