# Сторонние компоненты

Инвентаризация для Windows x86-64 / CPython 3.12.0. Прямые зависимости
закреплены в requirements.txt; транзитивные версии ниже — фактически
установленные при QA, они могут измениться при следующей установке.
Сверены metadata и LICENSE установленных пакетов, а также официальные источники.

| Компонент | Версия | Назначение | Лицензия | Официальный источник |
| --- | --- | --- | --- | --- |
| CPython | 3.12.0 в проверке; проект требует 3.11+ | Исполнение и стандартная библиотека | PSF-2.0, отдельные notices встроенных компонентов | [Python licenses](https://docs.python.org/3.12/license.html) |
| NumPy | 2.3.5, прямая зависимость | Массивы, случайные выборки, численные расчёты | BSD-3-Clause; бинарные включения ниже | [LICENSE v2.3.5](https://github.com/numpy/numpy/blob/v2.3.5/LICENSE.txt) |
| pandas | 3.0.1, прямая зависимость | Таблицы, группировки, CSV | BSD-3-Clause | [LICENSE v3.0.1](https://github.com/pandas-dev/pandas/blob/v3.0.1/LICENSE) |
| python-dateutil | 2.9.0.post0, через pandas | Поддержка дат | BSD-3-Clause; Apache-2.0 также применяется к переоформленным/новым частям | [LICENSE](https://github.com/dateutil/dateutil/blob/2.9.0.post0/LICENSE) |
| six | 1.17.0, через python-dateutil | Совместимость Python API зависимости | MIT | [LICENSE](https://github.com/benjaminp/six/blob/1.17.0/LICENSE) |
| tzdata | 2026.4, через pandas на Windows | Часовые пояса | Apache-2.0 для пакета; данные IANA преимущественно public domain, см. notices базы | [Проект](https://github.com/python/tzdata), [релиз](https://pypi.org/project/tzdata/2026.4/), [IANA](https://www.iana.org/time-zones) |
| pip | 23.2.1 в проверке | Установка зависимостей; не используется агентом | MIT; vendored-пакеты имеют собственные notices | [LICENSE](https://github.com/pypa/pip/blob/23.2.1/LICENSE.txt) |
| Git for Windows | 2.45.1.windows.1 в проверке | Репозиторий и список файлов preflight | GPL-2.0 для Git; дополнительные компоненты дистрибутива имеют отдельные условия | [Git COPYING](https://github.com/git/git/blob/v2.45.1/COPYING), [Git for Windows](https://gitforwindows.org/) |

## Бинарные включения NumPy

Проверенный Windows wheel NumPy сообщает OpenBLAS 0.3.30. Его установленный
`numpy-2.3.5.dist-info/LICENSE.txt` содержит также notices следующих компонентов:

| Компонент | Версия | Назначение | Лицензия | Источник |
| --- | --- | --- | --- | --- |
| OpenBLAS | 0.3.30 | BLAS/LAPACK в numpy.libs DLL | BSD-3-Clause | [OpenBLAS LICENSE](https://github.com/OpenMathLib/OpenBLAS/blob/v0.3.30/LICENSE) |
| LAPACK | В составе wheel NumPy 2.3.5; отдельная версия не установлена | Линейная алгебра | BSD-3-Clause-Open-MPI согласно notice wheel | [LAPACK](https://github.com/Reference-LAPACK/lapack), LICENSE установленного wheel |
| GCC runtime | В составе wheel NumPy 2.3.5; отдельная версия не установлена | Поддержка кода, собранного GCC | GPL-3.0-or-later WITH GCC-exception-3.1 | [Runtime exception](https://www.gnu.org/licenses/gcc-exception-3.1.html), LICENSE установленного wheel |

Это не полный SBOM бинарных дистрибутивов. Полные notices wheel и стандартной
библиотеки сохраняются в установленных пакетах; их нельзя заменять этой краткой
таблицей при распространении самих бинарников. В репозиторий библиотеки, venv
и их исходники не копируются.

## Модели, сервисы и данные

Во время работы агент не использует сторонние модели, LLM или сетевые API.
GitHub и индекс Python-пакетов используются для разработки/установки.

Официальный пакет участника предоставлен организаторами хакатона локально.
Его исходные `environment.py`, `mock_environment.py`, `scoring_core.py`,
`agent_template.py`, `local_eval.py`, `make_submission.py` и
`PARTICIPANT_GUIDE.md` сохраняются без изменения. CSV пакета, включая
`customer_profile.csv`, `tariff_dictionary.csv`, `feature_dictionary.csv`
и `data/`, восстанавливаются локально скриптом импорта и не входят в Git.
Список файлов и SHA-256 локального источника хранится в
`scripts/participant_package_manifest.json`. Организаторы описывают данные
как синтетические. Условия распространения исходных файлов пакета
уточняются у организаторов; таблица лицензий библиотек выше к этим файлам
не относится.

Прежний генератор команды и симулятор остаются только как исторические
тестовые средства, они не формируют вход официального evaluator.

Для предыдущего отдельного QA чтения CSV использовались 500 строк IBM Telco
Customer Churn из коммита `d5371f5d83a446ad5673cbcca3b814b926491f8a`;
[источник](https://github.com/IBM/telco-customer-churn-on-icp4d/tree/d5371f5d83a446ad5673cbcca3b814b926491f8a).
Набор не входит в зависимости агента, не включён в Git и не используется
в текущем воспроизведении. Лицензию кода IBM-репозитория не следует автоматически
переносить на данные; отдельное разрешение на их распространение здесь
не подтверждалось.

Лицензия собственного кода команды в отдельном LICENSE пока не задана.
