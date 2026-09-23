# Профиль синтетических данных команды

Этап 1 выполнен для согласованной локальной QA на данных команды, сгенерированных
`generate_demo_data.py`, seed 2026. Официальный пакет организаторов отсутствует.
Это не профиль базы HackAlem и не подтверждение совместимости с судейством.
Все данные синтетические; выводы нельзя переносить на реальный бизнес Beeline.

## Источник и воспроизведение

Проверен код из main `688581e`. Генератор создаёт два CSV: customer_profile
и tariff_dictionary. Третий файл, qa_segment_dictionary, — маленький QA-словарь
с категориями, явно взятыми из генератора; он не получен от организаторов
и не выведен из наблюдаемых значений профиля. Исходные CSV сохранены вне Git.

После установки зависимостей по README выполните в PowerShell из корня проекта:

```powershell
$qaData = Join-Path ([IO.Path]::GetTempPath()) ('cerebrum-profile-' + [guid]::NewGuid().ToString('N'))
python generate_demo_data.py --output "$qaData" --seed 2026
python -c "from pathlib import Path; import sys; Path(sys.argv[1], 'qa_segment_dictionary.csv').write_text('arpu_segment,data_segment,call_segment\nLOW,NON_USER,LOW\nMID,LITE,MEDIUM\nHIGH,HEAVY,HIGH\n', encoding='utf-8', newline='\n')" "$qaData"
python -X utf8 analysis/profile_data.py --data-dir "$qaData" --customer-id customer_id --current-tariff current_tariff --tariff-key tariff_dictionary.csv:tariff_id --id-column customer_profile.csv:current_tariff --id-column tariff_dictionary.csv:tariff_id --segment-rule arpu_segment=qa_segment_dictionary.csv:arpu_segment --segment-rule data_segment=qa_segment_dictionary.csv:data_segment --segment-rule call_segment=qa_segment_dictionary.csv:call_segment --output "$qaData/profile.md"
python -m unittest discover -s tests -p test_profile_data.py -v
```

Профилировщик завершился с кодом 0; 3 теста прошли. Два отчёта совпали побайтно;
SHA-256 исходных CSV до и после совпали. Подсчёты дополнительно сверены через
pandas: 23 441 уникальный абонент, нет пропусков или полных дублей в двух
основных таблицах, все 21 текущий тариф существуют в словаре. В bootstrap-
проверке использован отдельный venv, NumPy 2.3.5 и pandas 3.0.1.

Сопоставляемые между файлами идентификаторы здесь — **тарифы**. Во второй таблице
нет customer_id и нет истории абонентов: межфайловая проверка customer_id
неприменима, а не «успешна». Диапазоны ID не экспортируются.

## Выводы для алгоритма

- Поля текущего тарифа и сегментов заполнены. Объединение с тарифным словарём
  не теряет строки; сам словарь содержит уникальные ключи.
- Отдельные категории крупнее 5 000 абонентов. Размер кампании нужно считать
  по пересечению её фильтров, а не по одной маргинальной категории.
- predicted_arpu лежит в диапазоне 100.29–17999.95. Он влияет на ценность
  аудитории, но не даёт оценки эффекта смены тарифа без пилота.
- Чистота этого генератора не доказывает чистоту будущих данных организаторов.
  После получения пакета проверку нужно повторить с его реальными словарями.

Ниже — агрегированный результат скрипта без строк исходной аудитории.

## Автоматический профиль

Данные кейса синтетические. Выводы о реальном бизнесе Beeline не делаются.

### Методика

CSV читаются без изменения исходников. Пропуск — пустая строка или только пробелы; литералы NA/null не считаются пропусками автоматически. Полный дубль — повтор всей строки по исходным строковым значениям (счёт сверх первого вхождения).
Типы выведены из всех непустых значений, а не из схемы CSV. Диапазон — min/max конечных чисел; для смешанных колонок это только числовая часть. Идентификаторы сравниваются как строки, без удаления пробелов и ведущих нулей.
Кодировка: `utf-8-sig`; разделитель: `','`. В отчёте нет строк исходных данных и значений идентификаторов абонентов.

### Датасеты

### customer_profile.csv

SHA-256: `a53f322b32275b2fa80819cd8f99a7b0f4f30f9c106afa76245b45eb6cd23396`

Строк: **23441**; колонок: **6**; полных дублей: **0**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| customer_id | string (identifier) | 0 | 23441 | n/a | n/a |
| current_tariff | string (identifier) | 0 | 0 | n/a | n/a |
| arpu_segment | string/mixed | 0 | 0 | n/a | n/a |
| data_segment | string/mixed | 0 | 0 | n/a | n/a |
| call_segment | string/mixed | 0 | 0 | n/a | n/a |
| predicted_arpu | decimal | 0 | 23441 | 100.29 | 17999.95 |

### qa_segment_dictionary.csv

SHA-256: `fb87a11352d61abd85057c3e86ed979dfedf86b29d88e408e29f888483aec4df`

Строк: **3**; колонок: **3**; полных дублей: **0**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| arpu_segment | string/mixed | 0 | 0 | n/a | n/a |
| data_segment | string/mixed | 0 | 0 | n/a | n/a |
| call_segment | string/mixed | 0 | 0 | n/a | n/a |

### tariff_dictionary.csv

SHA-256: `784c72bb448b656c376f56363ceb46b05302b629fdf75f4998e178642ab559d8`

Строк: **21**; колонок: **4**; полных дублей: **0**.

| Колонка | Тип | Пропуски | Числовых значений | Min | Max |
| --- | --- | ---: | ---: | --- | --- |
| tariff_id | string (identifier) | 0 | 0 | n/a | n/a |
| monthly_fee | decimal | 0 | 21 | 700.0 | 15000.0 |
| data_gb | decimal | 0 | 21 | 1.0 | 100.0 |
| minutes | integer | 0 | 21 | 50 | 1500 |

### Аудитория customer_profile

Уникальных непустых идентификаторов абонентов: **23441**. Пустых: **0**; повторных строк по идентификатору: **0**.

### arpu_segment

| Значение | Строк |
| --- | ---: |
| HIGH | 9356 |
| LOW | 4663 |
| MID | 9422 |

### data_segment

| Значение | Строк |
| --- | ---: |
| HEAVY | 10537 |
| LITE | 9377 |
| NON_USER | 3527 |

### call_segment

| Значение | Строк |
| --- | ---: |
| HIGH | 7721 |
| LOW | 7879 |
| MEDIUM | 7841 |

### current_tariff

| Значение | Строк |
| --- | ---: |
| tariff_1 | 1056 |
| tariff_10 | 1108 |
| tariff_11 | 1072 |
| tariff_12 | 1093 |
| tariff_13 | 1154 |
| tariff_14 | 1129 |
| tariff_15 | 1126 |
| tariff_16 | 1093 |
| tariff_17 | 1142 |
| tariff_18 | 1146 |
| tariff_19 | 1082 |
| tariff_2 | 1089 |
| tariff_20 | 1145 |
| tariff_21 | 1168 |
| tariff_3 | 1123 |
| tariff_4 | 1134 |
| tariff_5 | 1162 |
| tariff_6 | 1074 |
| tariff_7 | 1156 |
| tariff_8 | 1068 |
| tariff_9 | 1121 |

predicted_arpu: min=100.29, max=17999.95; пропусков=0, нечисловых/неконечных=0.

### Проверки

| Статус | Результат |
| --- | --- |
| OK | Ключ абонента: пустых=0, повторных строк=0. |
| OK | Числовая полнота predicted_arpu. |
| OK | Тарифы: пустых в профиле=0; строк с неизвестным тарифом=0; пустых ключей словаря=0; повторных ключей словаря=0. |
| INFO | Идентификаторы customer_profile.csv:current_tariff ↔ tariff_dictionary.csv:tariff_id: общих=21, только слева=0, только справа=0. Различие выборок само по себе не является ошибкой; назначение связи нужно сверить со словарём. |
| OK | arpu_segment по qa_segment_dictionary.csv:arpu_segment: пустых=0; строк вне словаря=0. |
| OK | data_segment по qa_segment_dictionary.csv:data_segment: пустых=0; строк вне словаря=0. |
| OK | call_segment по qa_segment_dictionary.csv:call_segment: пустых=0; строк вне словаря=0. |

### Значение для алгоритма

- Пропуски и повторные ключи нужно учесть до объединения таблиц: дубли могут увеличивать оценку аудитории.
- Размеры категорий помогают проверить представимость сегментов; размер конкретной кампании нужно считать по пересечению её фильтров.
- Распределение ARPU не определяет эффект смены тарифа; для выбора кампаний необходимы пилоты.
- NOT_CHECKED означает отсутствие данных или сопоставления, а не успешную проверку.


## Предыдущая внешняя проверка

Ранее для общей проверки чтения CSV использованы первые 500 строк
[IBM Telco Customer Churn](https://github.com/IBM/telco-customer-churn-on-icp4d/blob/d5371f5d83a446ad5673cbcca3b814b926491f8a/data/Telco-Customer-Churn.csv):
21 колонка, 500 уникальных customerID, 0 полных дублей, 1 пропуск TotalCharges.
SHA-256 выборки: `774cf85c875e87abdce60c6f2e8f574f415e5e8aa3f1588c3f53a6e21ca722b8`.
Этот внешний тест не использовался для запуска агента: в нём нет нужных полей
тарифного кейса. Поле MonthlyCharges не подменяет predicted_arpu.
