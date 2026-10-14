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
- `crmbuilder validate portal.yaml` проверяет спецификацию по схеме.

Дальше по плану: `plan` (сравнение спецификации с порталом), `apply` со снимком
перед изменениями, `rollback`, `drift`, затем ИИ-слой.

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
docker compose run --rm cli validate clinic/portal.yaml
```

Файлы появятся в `./workspace/clinic/`.

Вебхук создаётся в портале: «Разработчикам» → «Другое» → «Входящий вебхук».
Права: `crm`, `user` (для структуры отделов). Импорт только читает данные.

## Локальная разработка

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Тесты не ходят в сеть: Битрикс эмулируется через `httpx.MockTransport`
(`tests/conftest.py`).
