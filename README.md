# crmbuilder

Конфигурация портала Битрикс24 как код. Портал описывается YAML-спецификацией,
а сервис приводит живой портал в соответствие с ней: импорт → план → применение →
откат. Поверх этого ядра будет работать ИИ-агент, который превращает ТЗ в правки
спецификации.

## Что уже есть

- `crmbuilder import` читает портал через входящий вебхук и пишет:
  - `portal.yaml`: спецификация (воронки и стадии сделок, пользовательские поля
    лидов, сделок, контактов и компаний, смарт-процессы с воронками и стадиями,
    структура отделов);
  - `portal.state.json`: связь ключей спецификации с ID в Битриксе
    (`C1:NEW`, `UF_CRM_1712345678`, ID воронок и отделов).

  Если state-файл уже есть, повторный импорт сохраняет прежние ключи, даже если
  объекты переименовали на портале. `--fresh` генерирует все ключи заново.
- `crmbuilder plan portal.yaml` заново читает портал и показывает, что нужно
  изменить, чтобы он совпал со спецификацией. На портале ничего не меняется.
  `--json` выводит план для программ, `--against other.yaml` сравнивает два файла
  без обращения к порталу.
- `crmbuilder validate portal.yaml` проверяет спецификацию по схеме.

Дальше по плану: `apply` со снимком перед изменениями, `rollback`, затем ИИ-слой.

## Рабочий цикл

```bash
crmbuilder import -o clinic/portal.yaml   # 1. прочитать портал
# 2. поправить clinic/portal.yaml
crmbuilder plan clinic/portal.yaml        # 3. посмотреть, что изменится
```

Пример плана:

```
Plan for clinic.bitrix24.ru

  ~ update  crm.deal_categories.obshchaya.stages.prepayment_invoice
        name: "Cчёт на предоплату" -> "Счёт на предоплату"
  + create  crm.deal_categories.obshchaya.stages.peregovory
        name: "Переговоры"
        semantic: "process"
        sort: 55
  - delete  crm.fields.deal.tip_printera
        WARNING: data stored in this field will be lost

Plan: 1 to create, 1 to update, 1 to delete.
1 destructive change(s) marked with - or ! can lose data.
```

Объекты сопоставляются по ключу: новый ключ в файле означает создание, пропавший
ключ означает удаление. Поэтому переименовывать ключи нельзя, меняйте
`name`/`label`. Тип поля и флаг множественности Битрикс менять не умеет, такие
правки план показывает как `replace` (удалить и создать заново, данные теряются).

Ссылки CRM-полей на смарт-процессы хранятся по ключу (`SMART_PROCESS:vebinary`),
а не по номеру `DYNAMIC_1066`, поэтому спецификацию можно переносить между
порталами. Настройки полей, совпадающие со значениями Битрикса по умолчанию,
в файл не пишутся.

## Пример спецификации

```yaml
version: 1
portal: clinic.bitrix24.ru
crm:
  deal_categories:
    - key: pervichnyy_priem
      name: Первичный приём
      stages:
        - {key: new, name: Новая заявка}
        - {key: zapisan, name: Записан}
        - {key: won, name: Пришёл, semantic: success}
        - {key: lose, name: Не пришёл, semantic: failure}
  fields:
    deal:
      - key: doctor
        label: Врач
        type: employee
        required: true
```

В спецификации нет ID Битрикса, только стабильные ключи. Ключи берутся из кода
объекта (`UF_CRM_DOCTOR` → `doctor`, `C1:NEW` → `new`), а для автосгенерированных
кодов (`UF_CRM_1712345678`, `UC_X7K2P1`) — из транслитерированного названия.
Сами ID хранятся в state-файле.

## Запуск в Docker

```bash
cp .env.example .env          # вписать BITRIX_WEBHOOK_URL
docker compose build
docker compose run --rm cli import -o clinic/portal.yaml
docker compose run --rm cli plan clinic/portal.yaml
```

Файлы появятся в `./workspace/clinic/`.

Вебхук создаётся в портале: «Разработчикам» → «Другое» → «Входящий вебхук».
Права: `crm` и `department` (структура компании). Импорт только читает данные.

### Windows (Git Bash + Docker Desktop)

Команды те же, что выше, с несколькими отличиями:

- Docker Desktop должен быть запущен (значок кита в трее). Проверка: `docker version`.
- `HOST_UID` и `HOST_GID` в `.env` оставьте `1000`. Не подставляйте вывод `id -u`:
  в Git Bash он выдаёт виндовый номер вроде `197609`, который здесь не нужен.
- `.env` удобно править так: `notepad .env`.
- Если `docker compose run` пишет `the input device is not a TTY`, добавьте флаг `-T`:
  `docker compose run --rm -T cli import -o test/portal.yaml`.
- Результат открывается в Проводнике командой `explorer workspace`.

## Локальная разработка

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Тесты не ходят в сеть: Битрикс эмулируется через `httpx.MockTransport`
(`tests/conftest.py`).
