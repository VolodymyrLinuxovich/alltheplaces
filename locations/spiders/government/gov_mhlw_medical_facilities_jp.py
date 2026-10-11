import csv
import io
import re
import sys
import unicodedata
import zipfile
from typing import Any, Iterable

from scrapy import Spider
from scrapy.http import Request, Response

from locations.categories import Categories, apply_category
from locations.hours import DAYS, OpeningHours
from locations.items import Feature

# Japanese day names in the order of DAYS (Mo-Su).
DAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]

# 診療科目コード to healthcare:speciality, from the mapping of the planned OSM import:
# https://wiki.openstreetmap.org/wiki/Import/Catalogue/Japan_mhlw_facilities
# https://github.com/nyampire/jp-healthcare-osm/blob/main/mapping/speciality_mapping.csv
SPECIALITIES = {
    "01001": "internal",  # 内科
    "01002": "infectious_diseases",  # 感染症内科
    "01003": "venereology",  # 性感染症内科
    "01004": "haematology;oncology",  # 血液・腫瘍内科
    "01005": "haematology",  # 血液内科
    "01006": "endocrinology",  # 糖尿病内科
    "01007": "endocrinology",  # 代謝内科
    "01008": "endocrinology",  # 内分泌内科
    "01009": "endocrinology",  # 脂質代謝内科
    "01010": "endocrinology",  # 糖尿病・内分泌内科
    "01011": "endocrinology",  # 糖尿病・代謝内科
    "01012": "endocrinology",  # 代謝・内分泌内科
    "01013": "neurology",  # 脳神経内科
    "01014": "pulmonology",  # 呼吸器内科
    "01015": "geriatrics;pulmonology",  # 老年・呼吸器内科
    "01016": "otolaryngology",  # 気管食道内科
    "01017": "cardiology",  # 循環器内科
    "01018": "cardiology",  # 心臓内科
    "01019": "cardiology",  # 心臓血管内科
    "01020": "gastroenterology",  # 消化器内科
    "01021": "gastroenterology",  # 胃腸内科
    "01022": "nephrology",  # 腎臓内科
    "01023": "dialysis",  # 人工透析内科
    "01024": "hepatology",  # 肝臓内科
    "01025": "neurology",  # 神経内科
    "01026": "oncology",  # 腫瘍内科
    "01027": "traditional_chinese_medicine",  # 漢方内科
    "01028": "geriatrics",  # 老年内科
    "01029": "internal",  # 女性内科
    "01030": "gastroenterology",  # 内視鏡内科
    "01031": "palliative",  # 疼痛緩和内科
    "01032": "anaesthetics",  # ペインクリニック内科
    "01033": "allergology",  # アレルギー疾患内科
    "01034": "palliative",  # 緩和ケア内科
    "01991": "internal",  # インフルエンザ
    "02001": "surgery",  # 外科
    "02002": "neurosurgery",  # 脳神経外科
    "02003": "neurosurgery",  # 脳外科
    "02004": "neurosurgery",  # 脳・血管外科
    "02005": "cardiothoracic_surgery",  # 呼吸器外科
    "02006": "surgery",  # 食道外科
    "02007": "otolaryngology",  # 気管食道外科
    "02008": "vascular_surgery",  # 血管外科
    "02009": "cardiothoracic_surgery",  # 循環器外科
    "02010": "cardiothoracic_surgery",  # 心臓外科
    "02011": "cardiothoracic_surgery",  # 心臓血管外科
    "02012": "abdominal_surgery",  # 消化器外科
    "02014": "abdominal_surgery;transplant",  # 消化器・移植外科
    "02015": "abdominal_surgery",  # 胃腸外科
    "02016": "proctology",  # 大腸外科
    "02017": "urology",  # 腎臓外科
    "02018": "abdominal_surgery",  # 肝臓外科
    "02019": "abdominal_surgery",  # 膵臓外科
    "02020": "abdominal_surgery",  # 胆のう外科
    "02021": "abdominal_surgery",  # 肝臓・胆のう・膵臓外科
    "02022": "surgery",  # 乳腺外科
    "02023": "surgery",  # 乳腺・内分泌外科
    "02024": "surgery",  # 女性乳腺外科
    "02025": "proctology",  # 肛門外科
    "02026": "proctology",  # 大腸・肛門外科
    "02027": "anaesthetics",  # ペインクリニック外科
    "02028": "oncology",  # 腫瘍外科
    "02029": "otolaryngology",  # 頭頸部外科
    "02030": "cardiothoracic_surgery",  # 胸部外科
    "02031": "abdominal_surgery",  # 腹部外科
    "02032": "surgery",  # 内視鏡外科
    "02033": "transplant",  # 移植外科
    "02034": "surgery;transplant",  # 移植・内視鏡外科
    "02035": "orthopaedics",  # 整形外科
    "02036": "plastic_surgery",  # 形成外科
    "02037": "plastic_surgery",  # 美容外科
    "02038": "palliative",  # 緩和ケア外科
    "02991": "surgery",  # その他（外科系）
    "03001": "paediatrics",  # 小児科
    "03002": "paediatrics;ophthalmology",  # 小児眼科
    "03003": "paediatrics;otolaryngology",  # 小児耳鼻いんこう科
    "03004": "paediatrics;dermatology",  # 小児皮膚科
    "03005": "child_psychiatry",  # 児童精神科
    "03006": "paediatric_surgery",  # 小児外科
    "03007": "paediatrics;urology",  # 小児泌尿器科
    "03008": "neonatology",  # 新生児内科
    "03009": "paediatric_surgery;oncology",  # 小児腫瘍外科
    "03991": "paediatrics",  # 小児神経科
    "04001": "gynaecology",  # 産婦人科
    "04002": "gynaecology",  # 産科
    "04003": "gynaecology",  # 婦人科
    "04004": "gynaecology;fertility",  # 産婦人科（生殖医療）
    "04991": "gynaecology",  # 女性診療科
    "05001": "ophthalmology",  # 眼科
    "05002": "otolaryngology",  # 耳鼻いんこう科
    "05003": "otolaryngology",  # 気管食道・耳鼻いんこう科
    "05004": "otolaryngology",  # 頭頸部・耳鼻いんこう科
    "05991": "ophthalmology",  # 神経眼科
    "05992": "otolaryngology",  # 耳鼻咽喉科・頭頸部外科
    "06001": "dermatology",  # 皮膚科
    "06002": "dermatology",  # 美容皮膚科
    "06003": "dermatology;urology",  # 皮膚泌尿器科
    "06004": "urology",  # 泌尿器科
    "06005": "urology",  # 男性泌尿器科
    "06006": "urology",  # 神経泌尿器科
    "06007": "nephrology;urology",  # 腎臓・泌尿器科
    "06991": "dermatology",  # 皮膚腫瘍科
    "06992": "urology",  # 女性泌尿器科
    "07001": "psychiatry",  # 精神科
    "07002": "psychiatry",  # 神経科
    "07003": "psychiatry",  # 心療内科
    "07004": "psychiatry;geriatrics",  # 老年精神科
    "07005": "psychiatry;geriatrics",  # 老年心療内科
    "07991": "neuropsychiatry",  # 神経精神科
    "08001": "dentistry",  # 歯科
    "08002": "orthodontics",  # 矯正歯科
    "08003": "dental_oral_maxillo_facial_surgery",  # 歯科口腔外科
    "08004": "paediatric_dentistry",  # 小児歯科
    "08005": "orthodontics;paediatric_dentistry",  # 小児矯正歯科
    "08991": "dental_oral_maxillo_facial_surgery",  # 口腔腫瘍外科
    "09001": "allergology",  # アレルギー科
    "09002": "rheumatology",  # リウマチ科
    "09003": "physiatry",  # リハビリテーション科
    "09004": "radiology",  # 放射線科
    "09005": "diagnostic_radiology",  # 放射線診断科
    "09006": "radiotherapy",  # 放射線治療科
    "09007": "radiotherapy",  # 腫瘍放射線科
    "09008": "pathology",  # 病理診断科
    "09009": "clinical_pathology",  # 臨床検査科
    "09010": "emergency",  # 救急科
    "09011": "anaesthetics",  # 麻酔科
    "09991": "general",  # 総合診療科
}
# At dental clinics the "other" codes are mostly implants and cosmetic dentistry.
DENTAL_SPECIALITIES = SPECIALITIES | {"08991": "dentistry", "09991": "dentistry"}
EMERGENCY = "09010"


class GovMhlwMedicalFacilitiesJPSpider(Spider):
    """
    Medical facilities and pharmacies published as open data by the Ministry of
    Health, Labour and Welfare (医療情報ネット オープンデータ). A new release
    comes out every 6 months.

    The 診療科・診療時間票 files list the hours of each department within a
    facility. They are merged into one opening_hours for the facility, without
    the emergency department, as planned for the OSM import.
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
    # File name prefix of each facility table: parse method, file name prefix of its department table
    tables = {
        "01-1_hospital_facility_info": ("parse_hospital", "01-2_hospital_speciality_hours"),
        "02-1_clinic_facility_info": ("parse_clinic", "02-2_clinic_speciality_hours"),
        "03-1_dental_facility_info": ("parse_dentist", "03-2_dental_speciality_hours"),
        "04_maternity_home": ("parse_maternity_home", None),
        "05_pharmacy": ("parse_pharmacy", None),
    }

    def parse(self, response: Response, **kwargs: Any) -> Any:
        for prefix, (callback, departments_prefix) in self.tables.items():
            cb_kwargs = {"callback": getattr(self, callback), "departments": {}}
            url = self.find_zip(response, prefix)
            if departments_prefix:
                # Read the departments first, then the facilities they belong to.
                yield response.follow(
                    self.find_zip(response, departments_prefix),
                    callback=self.parse_departments_zip,
                    cb_kwargs={"facilities_url": response.urljoin(url), "cb_kwargs": cb_kwargs},
                )
            else:
                yield response.follow(url, callback=self.parse_zip, cb_kwargs=cb_kwargs)

    @staticmethod
    def find_zip(response: Response, prefix: str) -> str:
        # The page lists every release, newest first.
        return response.xpath(f'//a[contains(@href, "/{prefix}_")][contains(@href, ".zip")]/@href').get()

    def parse_departments_zip(self, response: Response, facilities_url: str, cb_kwargs: dict) -> Any:
        departments = cb_kwargs["departments"]
        for row in read_zip(response):
            # Keep only the specialities and a set of hours per day, as the files are large.
            codes, hours = departments.setdefault(row["ID"], ([], [set() for _ in DAYS_JA]))
            if (code := row["診療科目コード"]) not in codes:
                codes.append(code)
            if code == EMERGENCY:
                continue
            for day_hours, day_ja in zip(hours, DAYS_JA):
                start = row[f"{day_ja}_診療開始時間"]
                end = row[f"{day_ja}_診療終了時間"]
                # Skip times that can not be outpatient hours, such as 00:00-23:59 and 09:00-09:00.
                if start and end and "06:00" <= start < end <= "23:00":
                    day_hours.add((sys.intern(start), sys.intern(end)))
        yield Request(facilities_url, callback=self.parse_zip, cb_kwargs=cb_kwargs)

    def parse_zip(self, response: Response, callback, departments: dict) -> Iterable[Feature]:
        for row in read_zip(response):
            item = self.parse_common(row)
            yield from callback(item, row, departments.get(row["ID"]))

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

    def parse_hospital(self, item: Feature, row: dict, departments: tuple | None) -> Iterable[Feature]:
        apply_category(Categories.HOSPITAL, item)
        set_beds(item, row)
        set_departments(item, row, departments, SPECIALITIES)
        yield item

    def parse_clinic(self, item: Feature, row: dict, departments: tuple | None) -> Iterable[Feature]:
        # Clinics with beds (有床診療所) are amenity=clinic, the rest amenity=doctors.
        if set_beds(item, row):
            apply_category(Categories.CLINIC, item)
        else:
            apply_category({"amenity": "doctors"}, item)
        set_departments(item, row, departments, SPECIALITIES)
        yield item

    def parse_dentist(self, item: Feature, row: dict, departments: tuple | None) -> Iterable[Feature]:
        apply_category(Categories.DENTIST, item)
        set_departments(item, row, departments, DENTAL_SPECIALITIES)
        yield item

    def parse_maternity_home(self, item: Feature, row: dict, departments: tuple | None) -> Iterable[Feature]:
        apply_category(Categories.BIRTHING_CENTRE, item)
        yield item

    def parse_pharmacy(self, item: Feature, row: dict, departments: tuple | None) -> Iterable[Feature]:
        apply_category(Categories.PHARMACY, item)
        item["extras"]["dispensing"] = "yes"
        item["opening_hours"] = parse_pharmacy_hours(row)
        yield item


def read_zip(response: Response) -> Iterable[dict]:
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        for file_name in archive.namelist():
            if not file_name.endswith(".csv"):
                continue
            with archive.open(file_name) as csv_file:
                yield from csv.DictReader(io.TextIOWrapper(csv_file, encoding="utf-8-sig"))


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

    # Skip facilities with no open day at all, which are always data errors.
    if ":" not in (rules := oh.as_opening_hours()):
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


def set_departments(item: Feature, row: dict, departments: tuple | None, specialities: dict[str, str]) -> None:
    if not departments:
        return
    codes, hours = departments
    values = []
    for code in codes:
        for value in specialities.get(code, "").split(";"):
            if value and value not in values:
                values.append(value)
    if values:
        item["extras"]["healthcare:speciality"] = ";".join(values)
    item["opening_hours"] = parse_department_hours(row, hours)


def parse_department_hours(row: dict, hours: list[set]) -> str | None:
    oh = OpeningHours()
    nth_week_closures = []
    for day, day_ja, day_hours in zip(DAYS, DAYS_JA, hours):
        # 0 means closed and 1 means open for all of these fields.
        flag = row[f"毎週決まった曜日に休診（{day_ja}）"]
        if flag == "0" or not day_hours:
            if flag == "1":
                return None  # open on this day, but at unknown times
            oh.set_closed(day)
            continue
        weeks = [str(n) for n in range(1, 6) if row[f"決まった週に休診（定期週）第{n}週（{day_ja}）"] == "0"]
        if len(weeks) == 5:
            oh.set_closed(day)
            continue
        for start, end in merge_ranges(day_hours):
            oh.add_range(day, start, end)
        if weeks:
            nth_week_closures.append(f"{day}[{','.join(weeks)}] off")

    # Skip facilities with no open day at all, which are always data errors.
    if ":" not in (rules := oh.as_opening_hours()):
        return None
    rules = [rules, *nth_week_closures]
    if row["祝日に休診"] == "0":
        rules.append("PH off")
    return "; ".join(rules)


def merge_ranges(ranges: set[tuple[str, str]]) -> list[list[str]]:
    # Departments overlap, so join ranges that overlap or touch.
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
