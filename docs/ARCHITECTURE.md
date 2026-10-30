# Архитектура infra_validation

Репозиторий проверяет объекты городской инфраструктуры из выгрузок подрядчика
по независимым источникам и пишет в копию выгрузки «Подтверждено», «Вид?»
и «Основания». Код перенесён из архивного репозитория `infrastructure_2026`
(тег `archive-2026-10-30`); там же история методики. Визуальная схема потока —
`docs/architecture.qmd`.

## Слои

```
src/infracheck/
├── core/          общие примитивы и контракты таблиц
│   ├── names.py       сравнение вывесок (перенос 1:1: name_similarity, similar, is_generic…)
│   ├── geo.py         расстояния
│   ├── objects.py     контракт ObjectTable
│   ├── evidence.py    контракт Evidence
│   └── entities.py    строки выгрузки → объекты
├── inputs/        адаптеры входа: файл поставки → ObjectTable (+ RowMap)
│   ├── final_xlsx.py  формат финальной поставки (общепит, позже алкоголь, табак…)
│   └── v4_csv.py      четвёртая поставка: один CSV = одна поставка (алкоголь, табак до финальных XLSX)
├── registers/     адаптеры реестров: сырьё → RegisterTable (контракт licmatch)
│   └── egais.py       потоковый разбор ZIP ФСРАР + приведение
├── checks/        модули проверок: ObjectTable + Policy → Evidence; о категориях не знают
│   ├── providers.py   OSM / 2GIS из флагов выгрузки + relink
│   ├── arkh_admin.py
│   ├── yandex.py      оценка подрядчика, наша проверка по кэшу; сетевая часть — отдельная команда
│   ├── overture.py
│   ├── foursquare.py
│   ├── export_name.py описание вида в самом названии (для «Вид?»)
│   └── egais.py       Policy (режим catering | v4) + run + wide, как у остальных; licmatch.match → Evidence
├── categories/    конвейеры: какие проверки, правило решения, тексты
│   ├── base.py        общий каркас: Evidence → подтверждено / вид / основания
│   ├── catering.py    общепит
│   ├── alcohol.py     алкоголь: ЕГАИС — источник, только при совпадении имени
│   ├── tobacco.py     табак: ЕГАИС не источник, только косвенный довод после « || »
│   ├── registry.py    модули проверок, их Policy из конфига, запуск
│   └── pipeline.py    шаги для правил Snakemake и регрессии
└── deliver/
    └── xlsx.py        копия книги (CSV становится книгой): три колонки спереди, доказательства в конце, лист-легенда
packages/licmatch/  пакет сопоставления с реестрами: чистая функция match()
workflow/           Snakemake: правила вызывают тонкие скрипты, логика — в src/
config/             config.yaml (параметры по категориям), deliveries.tsv (что проверять)
```

Правила слоёв:

- `checks/*` не импортируют `categories/*`, не знают названия категорий и не пишут
  вердиктов. Всё, что меняет результат проверки, приходит в `Policy`
  (dataclass модуля), собранной из `config.yaml`.
- `licmatch` ничего не знает о ЕГАИС, о проекте и о русских текстах «Оснований»:
  только коды статусов и числа.
- Сеть нужна только командам загрузки (реестр, Overture, FSQ) и пополнению кэша
  Яндекса. Основной DAG офлайн.
- Сырые данные (`data/raw/`, `resources/`) только читаются.

## Контракты

### ObjectTable (`core/objects.py`)

Одна строка на объект, индекс `object_id`. Колонки: `delivery, city, name,
address, lat, lon, site_key, category, generic, n_rows` + колонки адаптера.
`RowMap` — DataFrame `row → object_id` для записи вердикта обратно во все строки.

### Evidence (`core/evidence.py`)

Одна строка на находку (объект × источник). Поля: `object_id, check, source,
origin, identity, record_state, kind_text, kind_scheme, matched_name,
matched_address, distance_m, name_score, ref, label, text` + колонки проверки
с её префиксом. Конструктор `evidence.make()` проверяет словарь.

### Три оси

| Ось | Поле | Вопрос |
|---|---|---|
| Принадлежность | `identity` strong / weak / none | запись источника — это наш объект? |
| Состояние записи | `record_state` current / stale / closed / inactive / unknown | что запись говорит о себе |
| Вид | `kind_text` + `kind_scheme` | как источник описывает вид объекта |

Правило категории по умолчанию: источник засчитывается в «Подтверждено», если
`identity = strong` и `record_state = current`, один раз на независимую группу.
Остальные находки с непустым `text` идут в «Основания» после « || ».
Отклонения от этого правила, нужные для паритета со старым кодом, задаются в
конфиге категории явно (см. «Известные отклонения»).

### RegisterTable / MatchResult (`packages/licmatch`)

`licmatch.match(objects, register, policy) -> MatchResult` — чистая функция:
без сети, файлов и глобальных констант.

- RegisterTable: `record_id, holder_id, holder_name, venue_name, address_line |
  street/house/block, lat, lon, coord_precision_m, in_force, valid_from,
  status_changed, kind`.
- Policy: веса и пороги (`MatchParams`), `kinds` (допустимые виды записи для
  объекта, в т.ч. по признаку объекта: на вынос → retail), `name_policy`
  (`required` | `tiebreak`), `name_fields` (`holder`, `venue`), `unit`
  (ключ точки), `brands` (`off` | `known` | `learned` + ручные решения),
  `archived` (правило архивной записи), `exclusive_scope` (по каким объектам
  точка эксклюзивна — например оба слоя v4).
- Пять шагов: кандидаты → оценка → допуск → назначение → объяснение. Допуск —
  список именованных условий; объяснение — условие, которое не прошёл лучший
  отвергнутый кандидат.
- MatchResult: одна строка на объект: `record_id, status (own | taken |
  other_holder | other_chain | inactive | wrong_kind | elsewhere | none), addr,
  name, dist, score, method, claimed_by, margin, near_miss, failed_rule` +
  то, что нужно адаптеру v4 (`name_basis`, счётчики лицензий по адресу).

### Группа сверки с ЕГАИС (`match_group`)

Правило v4 отдаёт одну точку лицензии одному магазину по **обоим** слоям сразу
(алкоголь и табак). Поэтому `deliveries.tsv` относит поставки к группе
`match_group`: правило `egais_group` склеивает их ObjectTable в порядке листа
(табак первым, как в архиве: ничьи решает нижняя строка), режет реестр по
городам объектов и один раз вызывает `checks.egais.run`; правило `check_egais`
берёт из результата строки своей поставки. Поставка общепита — группа из
самой себя. У всех поставок группы политика `egais` одна (проверяется при
загрузке Snakefile).

### Принадлежность лицензии в режиме v4

Сверка v4 (`tiebreak`) закрепляет лицензию и по одному адресу. В Evidence
лицензия объекта получает `identity = strong`, только если совпало имя, так же
строго, как в общепите (`checks.egais.name_agrees`): известная сеть (`manual`),
выученная пара «вывеска → ИНН», прошедшая проверку без собственных адресов
(`loo_accepted`, или принятая вручную), или сходство названий по
`licmatch.names_agree` (`fuzzy`). Общая вывеска (`generic`) и несовпадение
имени дают `weak`; архивная своя лицензия — `record_state = inactive`.
Алкоголь засчитывает только strong / current. Табак ЕГАИС не засчитывает
никогда: в реестре нет табачного класса, и строка показывает лишь косвенный
довод `probable` / `weak`, без суммирования с прямыми.

## Регрессия

Перенос — **один к одному**. Эталоны лежат в `resources/golden/` (не в git,
сняты со старого репозитория и проверены на детерминированность 2026-10-30):

| Эталон | Что должно совпасть |
|---|---|
| `final_egais/final_egais.csv` | все колонки `egais_*`, все 1 688 объектов |
| `final_confirm/final_confirm_objects.csv` | все колонки, все объекты трёх городов |
| `final_confirm/*_подтверждено.xlsx` | лист `data` ячейка в ячейку; лист «Подтверждение» — кроме путей к файлам в легенде |
| `v4_licences/v4_licences.csv` | все поля сверки и лицензии по всем 1 694 строкам — и в `run_v4`, и через конвейер (группа v4, parquet между шагами) |
| `v4_yandex/v4_alcohol_none_yandex.csv` | наша отметка Яндекса, название, карточка, расстояние, «рядом» и запрос по 440 строкам алкоголя |
| `v4_licences/v4_brand_aliases.csv` | выученные пары |
| `egais/egais_licences_final.tsv`, `egais_licences_arkhangelsk.tsv` | разбор реестра |

Тесты регрессии — `tests/regression/`, помечены `pytest.mark.golden` и
пропускаются, если эталонов нет. Юнит-тесты — `tests/unit/`.

Улучшения, меняющие результат (объединение двух функций сходства имён,
правило «слабая находка о виде не голосует» и т. п.), делаются только после
паритета, отдельными коммитами с таблицей «что изменилось».

## Известные отклонения от правила трёх осей (сохранены ради паритета)

- «Общепит?» в старом коде читает вид у найденных записей FSQ и Overture,
  в том числе у тех, что достались соседнему объекту (`exclusive`), то есть
  у `identity = weak`. Конфиг: `kind.vote_identity: [strong, weak]`.

### Исправлено

- 2026-10-30. Место Overture, собранное только из Foursquare
  (`Foursquare|Overture`), больше не засчитывается: решает прямая проверка
  FSQ. Архив сравнивал строку целиком и не срабатывал никогда, так что 21
  объект получал лишний источник. Конфиг: `existence.exclude` по наборам.
  Там же заполнена колонка «Общепит?», которую архив поставлял пустой.
  Подробно и с таблицей изменений:
  `docs/changes/2026-10-30-overture-fsq-and-food-column.md`.

## Данные

| Что | Где |
|---|---|
| Финальная поставка (XLSX) | `data/raw/final/` |
| Четвёртая поставка (алкоголь, табак) | `data/raw/v4/` |
| Реестр ФСРАР (ZIP, снимок 2026-07-01) | `resources/egais/` |
| Overture Places, FSQ OS Places (parquet) | `resources/overture/`, `resources/foursquare/` |
| Кэш Яндекса (jsonl, снимок, ценное состояние) | `resources/cache/yandex/` |
| Эталоны регрессии | `resources/golden/` |
| Промежуточные таблицы (parquet) | `data/temp/` |
| Выходы | `results/` |
