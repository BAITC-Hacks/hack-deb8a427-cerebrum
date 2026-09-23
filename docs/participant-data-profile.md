# Воспроизводимый профиль пакета участника

Проверка от 23 сентября 2026 года дополняет краткий профиль в
[data-profile.md](data-profile.md). Источник — присланный пользователем
`beeline_case_participants (1).zip`, SHA-256
`df1d955fb97816ff6de8ceb142ed589915d969f43f840a650dcdfbe12734f20b`.
Все 14 файлов из манифеста коммита `b594a66` побайтно совпали с архивом.
Семь исходных CSV распакованы во временный каталог вне Git.

Данные синтетические по руководству участника. Выводы нельзя переносить
на реальный бизнес Beeline. Прежние результаты генератора команды не
подменяют этот профиль.

## Что учитывать при выборе кампаний

- В аудитории 23 441 уникальный `ID_NUMBER`, 29 колонок; повторов ID нет.
  Пустых текущих тарифов 95, `arpu_segment` — 5, `data_segment` — 110.
  Пропуск нельзя превращать в существующий тариф или сегмент.
- Все непустые тарифы аудитории и истории входят в словарь из 21 тарифа.
  Оба тарифных словаря совпадают по пяти общим колонкам, включая параметры.
  В `data/traffic.csv` отсутствует тариф в 16 725 строках.
- Непустых сегментов вне категорий раздела 6 `PARTICIPANT_GUIDE.md` нет.
  `feature_dictionary.csv` содержит описания признаков, а не перечисления
  допустимых значений сегментов. Проверка категорий не доказывает корректность
  вычисления каждого сегмента по исходным показателям.
- `tariff_8` встречается у 6 904 абонентов: один такой фильтр превышает лимит
  5 000. Размер нужно проверять по фактическому пересечению фильтров.
- `predicted_arpu`: 0–216 619,82; сумма по исходным десятичным значениям —
  150 641 084,246268422585390. Пропусков и нечисловых значений нет.
  Значение ARPU определяет масштаб возможного результата, но не эффект перехода.
- Аудитория не имеет общих ID ни с одной из трёх исторических таблиц.
  Между историческими таблицами покрытие различается; соединение с текущей
  аудиторией по ID не восстановит историю её абонентов.
- В `change_tariff.csv` 14 823 строки и 6 полных дублей, в `arpu_monthly.csv`
  78 798 строк и 84 полных дубля. Дубли требуют явного решения перед
  агрегированием; исходники в этой QA не исправлялись. Руководство указывает
  14 824 смены — на одну больше фактического файла.
- Всего пустых ячеек: 3 940 в профиле и 68 031 в traffic. Полные сведения
  по каждой колонке ниже; чистота нескольких ключей не означает чистоты файла.

## Воспроизведение

Используется существующий [analysis/profile_data.py](../analysis/profile_data.py),
без изменений алгоритма и evaluator. Нужен Python 3.11+; сам профилировщик
использует только стандартную библиотеку. Команды PowerShell выполняются
из корня репозитория с активированным окружением.

Подготовка из исходного архива сохраняет относительные пути CSV. Маленький
`qa_segment_dictionary.csv` **не является восьмым входным датасетом**:
это техническое представление трёх списков из таблицы руководства.
Категории извлекаются из документа, а не из наблюдаемых данных аудитории.

```powershell
$participantZip = Join-Path $env:USERPROFILE 'Downloads/beeline_case_participants (1).zip'
$qaData = Join-Path ([IO.Path]::GetTempPath()) ('cerebrum-official-profile-' + [guid]::NewGuid().ToString('N'))
@'
from pathlib import Path
import csv, re, sys, zipfile
archive, root = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
root.mkdir(parents=True, exist_ok=False)
with zipfile.ZipFile(archive) as package:
    for name in package.namelist():
        if not name.endswith('.csv'):
            continue
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise ValueError('Unsafe archive path')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(package.read(name))
    guide = package.read('PARTICIPANT_GUIDE.md').decode('utf-8-sig')
segments = ['arpu_segment', 'data_segment', 'call_segment']
values = []
for segment in segments:
    match = re.search(r'^\| `' + segment + r'` \| ([^|]+) \|', guide, re.M)
    if match is None:
        raise ValueError('Documented segment table not found')
    values.append([value.strip() for value in match[1].split('/')])
with (root / 'qa_segment_dictionary.csv').open('w', encoding='utf-8', newline='') as stream:
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(segments)
    writer.writerows(zip(*values, strict=True))
'@ | python -X utf8 - "$participantZip" "$qaData"
python -X utf8 analysis/profile_data.py --data-dir "$qaData" --customer-id ID_NUMBER --current-tariff current_tariff --tariff-key tariff_dictionary.csv:tariff_plan_code --id-column customer_profile.csv:ID_NUMBER --id-column data/change_tariff.csv:ID_NUMBER --id-column data/traffic.csv:ID_NUMBER --id-column data/arpu_monthly.csv:ID_NUMBER --segment-rule arpu_segment=qa_segment_dictionary.csv:arpu_segment --segment-rule data_segment=qa_segment_dictionary.csv:data_segment --segment-rule call_segment=qa_segment_dictionary.csv:call_segment --output "$qaData/profile.md"
$profileExit = $LASTEXITCODE
Get-Content "$qaData/profile.md" -Encoding UTF8
python -m unittest discover -s tests -p test_profile_data.py -v
```

Фактический код профилировщика — **1**, время первого прогона 3,649 с:
это найденные `ISSUE` из-за пропусков, а не сбой чтения. Непроверенных
сопоставлений `NOT_CHECKED` нет. Тесты профилировщика: **3/3 PASS**.
Повторный отчёт побайтно совпал; SHA-256 CSV до и после не изменились.
SHA-256 автоматического `profile.md`:
`3c26190dec7bd57e0bb0d3ec8609c93f6c8fe31328e26a78451b076fcc656787`.

Размеры, пропуски и полные дубли дополнительно сверены через pandas 3.0.1
с `dtype=str, keep_default_na=False`. Проверка тарифов истории и равенства
параметров словарей выполнена отдельно; общая CLI-проверка тарифов ниже
относится к `customer_profile.csv`. Ни CSV, ни строки абонентов не публикуются.

## Автоматический результат



Данные кейса синтетические. Выводы о реальном бизнесе Beeline не делаются.

## Методика

CSV читаются без изменения исходников. Пропуск — пустая строка или только пробелы; литералы NA/null не считаются пропусками автоматически. Полный дубль — повтор всей строки по исходным строковым значениям (счёт сверх первого вхождения).
Типы выведены из всех непустых значений, а не из схемы CSV. Диапазон — min/max конечных чисел; для смешанных колонок это только числовая часть. Идентификаторы сравниваются как строки, без удаления пробелов и ведущих нулей.
Кодировка: `utf-8-sig`; разделитель: `','`. В отчёте нет строк исходных данных и значений идентификаторов абонентов.

## Датасеты

### customer_profile.csv

SHA-256: `a85e9badf4cb51b1ba8c43a868498b6fa8075509f3cd8e19ef72e1c25a6dc815`

Строк: **23441**; колонок: **29**; полных дублей: **0**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| ID_NUMBER | string (identifier) | 0 | 23441 | n/a | n/a |
| ARPU_current | decimal | 28 | 23413 | 0.0 | 1417082.91 |
| ARPU_3m_avg | decimal | 5 | 23436 | 0.0 | 476043.62666666665 |
| ARPU_trend | string/mixed | 5 | 0 | n/a | n/a |
| current_tariff | string (identifier) | 95 | 0 | n/a | n/a |
| OUT_LOC_ONNET_MIN | decimal | 110 | 23331 | 0.0 | 10968.563333333334 |
| OUT_LOC_OFFNET_MIN | decimal | 110 | 23331 | 0.0 | 1071.215 |
| OUT_LOC_OFFNET_UNPAID_MIN | decimal | 110 | 23331 | 0.0 | 623.75 |
| OUT_LOC_OFFNET_PAID_MIN | decimal | 110 | 23331 | 0.0 | 776.365 |
| OUT_INTER_MIN | decimal | 110 | 23331 | 0.0 | 445.125 |
| OUT_LOC_LAND_MIN | decimal | 110 | 23331 | 0.0 | 367.0366666666667 |
| OUT_LOCAL_ONNET_SMS_AMT | decimal | 110 | 23331 | 0.0 | 336.6666666666667 |
| OUT_LOCAL_OFFNET_SMS_AMT | decimal | 110 | 23331 | 0.0 | 226.0 |
| OUT_LOCAL_LAND_PAID_SMS_AMT | decimal | 110 | 23331 | 0.0 | 4.666666666666667 |
| OUT_INTER_SMS_AMT | decimal | 110 | 23331 | 0.0 | 36.0 |
| DATA_VOLUME | decimal | 110 | 23331 | 0.0 | 425742.18 |
| LTE_DATA_VOLUME | decimal | 110 | 23331 | 0.0 | 254689.99 |
| TOTAL_ROAM_CALL_AMT | decimal | 110 | 23331 | 0.0 | 35.0 |
| TOTAL_ROAM_SMS_AMT | decimal | 110 | 23331 | 0.0 | 23.0 |
| TOTAL_ROAM_GPRS_MB | decimal | 110 | 23331 | 0.0 | 1565.38 |
| COUNT_CONTACT | decimal | 470 | 22971 | 1.0 | 30.0 |
| AVG_TRANSACT_CONTACT | decimal | 470 | 22971 | 1.0 | 389.5 |
| SUM_TRANSACT_CONTACT | decimal | 470 | 22971 | 1.0 | 2422.0 |
| AVG_DURATION_CONTACT | decimal | 470 | 22971 | 1.0 | 84243.55555555555 |
| COUNT_BASE_STATION | decimal | 162 | 23279 | 1.0 | 20.0 |
| arpu_segment | string/mixed | 5 | 0 | n/a | n/a |
| data_segment | string/mixed | 110 | 0 | n/a | n/a |
| call_segment | string/mixed | 0 | 0 | n/a | n/a |
| predicted_arpu | decimal | 0 | 23441 | 0.0 | 216619.82 |

### data/arpu_monthly.csv

SHA-256: `83d96b61c38a8bf7cdf5f07bc4f17f7f2f39885559c496fc28c266cef8ad3c4a`

Строк: **78798**; колонок: **3**; полных дублей: **84**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| ARPU_1M | decimal | 0 | 78798 | -541.15 | 976802.76 |
| TIME_KEY | string/mixed | 0 | 0 | n/a | n/a |
| ID_NUMBER | string (identifier) | 0 | 78798 | n/a | n/a |

### data/change_tariff.csv

SHA-256: `c52d7dad11fccc7425afe6d36a44a1fa03c8cc148be2039ce264d0c5c100f230`

Строк: **14823**; колонок: **6**; полных дублей: **6**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| TIME_KEY | string/mixed | 0 | 0 | n/a | n/a |
| AVG_ARPU_PREV_3M | decimal | 0 | 14823 | -37.41 | 333832.77 |
| AVG_ARPU_NEXT_3M | decimal | 0 | 14823 | 0.0 | 42693.38 |
| ID_NUMBER | string (identifier) | 0 | 14823 | n/a | n/a |
| tariff_plan_code_from | string/mixed | 0 | 0 | n/a | n/a |
| tariff_plan_code_to | string/mixed | 0 | 0 | n/a | n/a |

### data/dict_tariff.csv

SHA-256: `a34d81dacffbb937ddd542e87d421afcea4842379acfbad55b8d59bdf710f4be`

Строк: **21**; колонок: **5**; полных дублей: **0**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| Data_in_PKG | integer | 0 | 21 | 0 | 30720 |
| Min_another_operator_in_PKG | integer | 0 | 21 | 0 | 200 |
| Min_another_operator_and_city_in_PKG | integer | 0 | 21 | 0 | 300 |
| price_tariff | decimal | 0 | 21 | 0.0 | 12528.6 |
| tariff_plan_code | string/mixed | 0 | 0 | n/a | n/a |

### data/traffic.csv

SHA-256: `cb0a3f8827d8be6989b65dc5bf0b7c901047cf64e485f4ee74e3c24787ee846c`

Строк: **75736**; колонок: **30**; полных дублей: **0**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| DEVICE_ID | string (identifier) | 2697 | 73039 | n/a | n/a |
| time_key | string/mixed | 0 | 0 | n/a | n/a |
| OUT_LOC_ONNET_MIN | decimal | 0 | 75736 | 0.0 | 11517.33 |
| OUT_LOC_OFFNET_MIN | decimal | 0 | 75736 | 0.0 | 868.98 |
| OUT_LOC_OFFNET_UNPAID_MIN | decimal | 0 | 75736 | 0.0 | 779.15 |
| OUT_LOC_OFFNET_PAID_MIN | decimal | 0 | 75736 | 0.0 | 613.8 |
| OUT_INTER_MIN | decimal | 0 | 75736 | 0.0 | 551.6 |
| OUT_LOC_LAND_MIN | decimal | 0 | 75736 | 0.0 | 211.9 |
| OUT_LOCAL_ONNET_SMS_AMT | decimal | 0 | 75736 | 0.0 | 562.0 |
| OUT_LOCAL_OFFNET_SMS_AMT | decimal | 0 | 75736 | 0.0 | 719.0 |
| OUT_LOCAL_LAND_PAID_SMS_AMT | decimal | 0 | 75736 | 0.0 | 7.0 |
| OUT_INTER_SMS_AMT | decimal | 0 | 75736 | 0.0 | 155.0 |
| DATA_VOLUME | decimal | 0 | 75736 | 0.0 | 228977.65 |
| LTE_DATA_VOLUME | decimal | 0 | 75736 | 0.0 | 234708.98 |
| TOTAL_ROAM_CALL_AMT | decimal | 0 | 75736 | 0.0 | 106.0 |
| TOTAL_ROAM_SMS_AMT | decimal | 0 | 75736 | 0.0 | 63.0 |
| TOTAL_ROAM_GPRS_MB | decimal | 0 | 75736 | 0.0 | 2840.31 |
| COUNT_CONTACT | decimal | 3987 | 71749 | 1.0 | 30.0 |
| AVG_TRANSACT_CONTACT | decimal | 3987 | 71749 | 1.0 | 282.0 |
| SUM_TRANSACT_CONTACT | decimal | 3987 | 71749 | 1.0 | 3671.0 |
| AVG_DURATION_CONTACT | decimal | 3987 | 71749 | 1.0 | 189869.66666666663 |
| COUNT_BASE_STATION | decimal | 2251 | 73485 | 1.0 | 20.0 |
| FIRST_DISP_DIAG | decimal | 6302 | 69434 | -99.0 | 10.5 |
| OS_1 | decimal | 6027 | 69709 | 0.0 | 1.0 |
| OS_2 | decimal | 6027 | 69709 | 0.0 | 1.0 |
| OS_3 | decimal | 6027 | 69709 | 0.0 | 1.0 |
| OS_4 | decimal | 6027 | 69709 | 0.0 | 1.0 |
| date_issue_device | string/mixed | 0 | 48 | -99 | -99 |
| ID_NUMBER | string (identifier) | 0 | 75736 | n/a | n/a |
| tariff_plan_code | string/mixed | 16725 | 0 | n/a | n/a |

### feature_dictionary.csv

SHA-256: `80bb9dddea7a054c3ef6569805013efbe313c7918a9d72d06203d1534e8f5c21`

Строк: **38**; колонок: **3**; полных дублей: **0**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| feature | string/mixed | 0 | 0 | n/a | n/a |
| unit | string/mixed | 0 | 0 | n/a | n/a |
| description | string/mixed | 0 | 0 | n/a | n/a |

### qa_segment_dictionary.csv

SHA-256: `fb87a11352d61abd85057c3e86ed979dfedf86b29d88e408e29f888483aec4df`

Строк: **3**; колонок: **3**; полных дублей: **0**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| arpu_segment | string/mixed | 0 | 0 | n/a | n/a |
| data_segment | string/mixed | 0 | 0 | n/a | n/a |
| call_segment | string/mixed | 0 | 0 | n/a | n/a |

### tariff_dictionary.csv

SHA-256: `d3e6588f5d8e8e51202b4a77b2d4c0243080965a82d8e1d9411149220dd9df1c`

Строк: **21**; колонок: **6**; полных дублей: **0**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| Data_in_PKG | integer | 0 | 21 | 0 | 30720 |
| Min_another_operator_in_PKG | integer | 0 | 21 | 0 | 200 |
| Min_another_operator_and_city_in_PKG | integer | 0 | 21 | 0 | 300 |
| price_tariff | decimal | 0 | 21 | 0.0 | 12528.6 |
| tariff_plan_code | string (identifier) | 0 | 0 | n/a | n/a |
| description | string/mixed | 0 | 0 | n/a | n/a |

## Аудитория customer_profile

Уникальных непустых идентификаторов абонентов: **23441**. Пустых: **0**; повторных строк по идентификатору: **0**.

### arpu_segment

| Значение | Строк |
| --- | ---: |
| (пусто) | 5 |
| HIGH | 13918 |
| LOW | 2738 |
| MID | 6780 |

### data_segment

| Значение | Строк |
| --- | ---: |
| (пусто) | 110 |
| HEAVY | 11735 |
| LITE | 10253 |
| NON_USER | 1343 |

### call_segment

| Значение | Строк |
| --- | ---: |
| HIGH | 4416 |
| LOW | 10716 |
| MEDIUM | 8309 |

### current_tariff

| Значение | Строк |
| --- | ---: |
| (пусто) | 95 |
| tariff_1 | 83 |
| tariff_10 | 3965 |
| tariff_11 | 2484 |
| tariff_12 | 1272 |
| tariff_13 | 1789 |
| tariff_14 | 1949 |
| tariff_15 | 108 |
| tariff_16 | 20 |
| tariff_17 | 67 |
| tariff_18 | 67 |
| tariff_19 | 74 |
| tariff_2 | 36 |
| tariff_20 | 50 |
| tariff_21 | 30 |
| tariff_3 | 170 |
| tariff_4 | 3634 |
| tariff_5 | 245 |
| tariff_6 | 119 |
| tariff_7 | 153 |
| tariff_8 | 6904 |
| tariff_9 | 127 |

predicted_arpu: min=0.0, max=216619.82; пропусков=0, нечисловых/неконечных=0.

## Проверки

| Статус | Результат |
| --- | --- |
| OK | Ключ абонента: пустых=0, повторных строк=0. |
| OK | Числовая полнота predicted_arpu. |
| ISSUE | Тарифы: пустых в профиле=95; строк с неизвестным тарифом=0; пустых ключей словаря=0; повторных ключей словаря=0. |
| INFO | Идентификаторы customer_profile.csv:ID_NUMBER ↔ data/change_tariff.csv:ID_NUMBER: общих=0, только слева=23441, только справа=14817. Различие выборок само по себе не является ошибкой; назначение связи нужно сверить со словарём. |
| INFO | Идентификаторы customer_profile.csv:ID_NUMBER ↔ data/traffic.csv:ID_NUMBER: общих=0, только слева=23441, только справа=14875. Различие выборок само по себе не является ошибкой; назначение связи нужно сверить со словарём. |
| INFO | Идентификаторы customer_profile.csv:ID_NUMBER ↔ data/arpu_monthly.csv:ID_NUMBER: общих=0, только слева=23441, только справа=14991. Различие выборок само по себе не является ошибкой; назначение связи нужно сверить со словарём. |
| INFO | Идентификаторы data/change_tariff.csv:ID_NUMBER ↔ data/traffic.csv:ID_NUMBER: общих=14727, только слева=90, только справа=148. Различие выборок само по себе не является ошибкой; назначение связи нужно сверить со словарём. |
| INFO | Идентификаторы data/change_tariff.csv:ID_NUMBER ↔ data/arpu_monthly.csv:ID_NUMBER: общих=14817, только слева=0, только справа=174. Различие выборок само по себе не является ошибкой; назначение связи нужно сверить со словарём. |
| INFO | Идентификаторы data/traffic.csv:ID_NUMBER ↔ data/arpu_monthly.csv:ID_NUMBER: общих=14875, только слева=0, только справа=116. Различие выборок само по себе не является ошибкой; назначение связи нужно сверить со словарём. |
| ISSUE | arpu_segment по qa_segment_dictionary.csv:arpu_segment: пустых=5; строк вне словаря=0. |
| ISSUE | data_segment по qa_segment_dictionary.csv:data_segment: пустых=110; строк вне словаря=0. |
| OK | call_segment по qa_segment_dictionary.csv:call_segment: пустых=0; строк вне словаря=0. |

## Значение для алгоритма

- Пропуски и повторные ключи нужно учесть до объединения таблиц: дубли могут увеличивать оценку аудитории.
- Размеры категорий помогают проверить представимость сегментов; размер конкретной кампании нужно считать по пересечению её фильтров.
- Распределение ARPU не определяет эффект смены тарифа; для выбора кампаний необходимы пилоты.
- NOT_CHECKED означает отсутствие данных или сопоставления, а не успешную проверку.
