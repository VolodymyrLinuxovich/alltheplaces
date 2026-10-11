import csv
import io
import re
import unicodedata
import zipfile
from typing import Any, Iterable

from scrapy import Spider
from scrapy.http import Response

from locations.categories import Categories, apply_category
from locations.hours import DAYS, OpeningHours
from locations.items import Feature

# Japanese day names in the order of DAYS (Mo-Su).
DAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]


class GovMhlwMedicalFacilitiesJPSpider(Spider):
    """
    Medical facilities and pharmacies published as open data by the Ministry of
    Health, Labour and Welfare (医療情報ネット オープンデータ). A new release
    comes out every 6 months.

    The 診療科・診療時間票 files only list hours for each department within a
    facility, which has no established tagging in OSM, so they are skipped.
    """

    name = "gov_mhlw_medical_facilities_jp"
    start_urls = ["https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/kenkou_iryou/iryou/newpage_43373.html"]
    allowed_domains = ["www.mhlw.go.jp"]
    dataset_attributes = {
        "attribution": "required",
        "attribution:name:ja": "厚生労働省 医療情報ネット",
        "attribution:name:en": "Ministry of Health, Labour and Welfare",
        "attribution:website": "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/kenkou_iryou/iryou/newpage_43373.html",
        "license": "Public Data License (Version 1.0)",
        "license:website": "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0",
        "use:commercial": "permit",
    }
    # File name prefix of each facility table: category, parse method
    tables = {
        "01-1_hospital_facility_info": "parse_hospital",
        "02-1_clinic_facility_info": "parse_clinic",
        "03-1_dental_facility_info": "parse_dentist",
        "04_maternity_home": "parse_maternity_home",
        "05_pharmacy": "parse_pharmacy",
    }

    def parse(self, response: Response, **kwargs: Any) -> Any:
        for prefix, callback in self.tables.items():
            # The page lists every release, newest first.
            url = response.xpath(f'//a[contains(@href, "/{prefix}_")][contains(@href, ".zip")]/@href').get()
            yield response.follow(url, callback=self.parse_zip, cb_kwargs={"callback": getattr(self, callback)})

    def parse_zip(self, response: Response, callback) -> Iterable[Feature]:
        with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
            for file_name in archive.namelist():
                if not file_name.endswith(".csv"):
                    continue
                with archive.open(file_name) as csv_file:
                    for row in csv.DictReader(io.TextIOWrapper(csv_file, encoding="utf-8-sig")):
                        item = self.parse_common(row)
                        yield from callback(item, row)

    def parse_common(self, row: dict) -> Feature:
        item = Feature()
        item["ref"] = row["ID"]
        # Missing coordinates are 0 and a few are placeholders with whole degrees such as 38, 140.
        lat, lon = row["所在地座標（緯度）"], row["所在地座標（経度）"]
        if "." in lat and "." in lon:
            item["lat"], item["lon"] = lat, lon
        item["addr_full"] = clean(row["所在地"])
        item["website"] = clean_website(row.get("案内用ホームページアドレス") or row.get("薬局のホームページアドレス"))

        item["name"] = clean(row.get("正式名称") or row.get("名称"))
        if kana := clean(row.get("正式名称（フリガナ）") or row.get("フリガナ")):
            item["extras"]["name:ja-Kana"] = kana
        if (short_name := clean(row.get("略称"))) and short_name != item["name"]:
            item["extras"]["short_name"] = short_name
        return item

    def parse_hospital(self, item: Feature, row: dict) -> Iterable[Feature]:
        apply_category(Categories.HOSPITAL, item)
        set_beds(item, row)
        yield item

    def parse_clinic(self, item: Feature, row: dict) -> Iterable[Feature]:
        # Clinics with beds (有床診療所) are amenity=clinic, the rest amenity=doctors.
        if set_beds(item, row):
            apply_category(Categories.CLINIC, item)
        else:
            apply_category({"amenity": "doctors"}, item)
        yield item

    def parse_dentist(self, item: Feature, row: dict) -> Iterable[Feature]:
        apply_category(Categories.DENTIST, item)
        yield item

    def parse_maternity_home(self, item: Feature, row: dict) -> Iterable[Feature]:
        apply_category(Categories.BIRTHING_CENTRE, item)
        yield item

    def parse_pharmacy(self, item: Feature, row: dict) -> Iterable[Feature]:
        apply_category(Categories.PHARMACY, item)
        item["extras"]["dispensing"] = "yes"
        item["opening_hours"] = parse_pharmacy_hours(row)
        yield item


def clean(value: str | None) -> str | None:
    if value:
        return unicodedata.normalize("NFKC", value).strip() or None
    return None


def clean_website(value: str | None) -> str | None:
    if not (value := clean(value)):
        return None
    if re.fullmatch(r"https?://\S+", value):
        return value
    # Fix mistyped schemes such as "http//", "htt://" and "//www", and bare domains like "www.example.co.jp".
    if m := re.fullmatch(r"(?:(https?|htt)?:?//)?([\w-]+(?:\.[\w-]+)+(?:[/?#]\S*)?)", value, flags=re.ASCII):
        return f"{'https' if m.group(1) == 'https' else 'http'}://{m.group(2)}"
    return None


def set_beds(item: Feature, row: dict) -> bool:
    if (beds := row.get("合計病床数")) and beds.isdigit() and int(beds) > 0:
        item["extras"]["beds"] = str(int(beds))
        return True
    return False


def parse_pharmacy_hours(row: dict) -> str | None:
    oh = OpeningHours()
    nth_week_closures = []
    for day, day_ja in zip(DAYS, DAYS_JA):
        # 0 means closed and 1 means open for all of these fields.
        if row[f"営業日（{day_ja}）"] != "1" or row[f"定期閉店毎週（{day_ja}）"] == "0":
            oh.set_closed(day)
            continue
        weeks = [str(n) for n in range(1, 6) if row[f"定期閉店第{chr(ord('０') + n)}週（{day_ja}）"] == "0"]
        if len(weeks) == 5:
            oh.set_closed(day)
            continue
        if not add_ranges(oh, row, day, day_ja):
            return None
        if weeks:
            nth_week_closures.append(f"{day}[{','.join(weeks)}] off")

    if not (rules := oh.as_opening_hours()):
        return None
    rules = [rules, *nth_week_closures]

    if row["営業日（祝）"] == "1":
        holiday = OpeningHours()
        if add_ranges(holiday, row, "Mo", "祝"):
            rules.append(holiday.as_opening_hours().replace("Mo ", "PH "))
    elif row["営業日（祝）"] == "0":
        rules.append("PH off")
    return "; ".join(rules)


def add_ranges(oh: OpeningHours, row: dict, day: str, day_ja: str) -> bool:
    ranges = []
    for n in range(1, 5):
        start = row[f"{day_ja}_開店時間帯{n}_開始時間"]
        end = row[f"{day_ja}_開店時間帯{n}_終了時間"]
        if not (start and end):
            continue
        # Some pharmacies split the day into back to back ranges: 08:30-12:30 and 12:30-17:30.
        if ranges and ranges[-1][1] == start:
            ranges[-1][1] = end
        else:
            ranges.append([start, end])
    for start, end in ranges:
        oh.add_range(day, start, end)
    return bool(ranges)
