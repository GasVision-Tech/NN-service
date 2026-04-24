# Зоны для камер

Один JSON-файл на камеру (`CAM-167.json`, `CAM-170.json`, ...). Путь к файлу
указывается в `config/streams.yaml` в поле `zones_config_path`.

## Формат

```json
{
  "forbidden_zones": [
    { "id": "forbidden_1", "name": "Под навесом", "polygon": [[x1,y1],[x2,y2],...] }
  ],
  "column_zones": [
    { "id": "column_1", "name": "Колонка 1", "polygon": [[x1,y1],[x2,y2],...] }
  ],
  "station_zones": [
    { "id": "station", "name": "Вся территория АЗС", "polygon": [[x1,y1],[x2,y2],...] }
  ]
}
```

Поле может отсутствовать — соответствующий сценарий просто не будет срабатывать.

## Как сценарии используют зоны

| Сценарий                                  | Какие зоны нужны | Поведение без зон                         |
|-------------------------------------------|------------------|-------------------------------------------|
| `person_in_forbidden_zone`                | forbidden        | Не срабатывает                            |
| `person_without_car_at_column`            | column           | Не срабатывает                            |
| `person_too_long_at_station`              | station          | Срабатывает на весь кадр (full-frame)     |
| `car_too_long_at_column`                  | column           | Срабатывает на весь кадр (full-frame)     |

Если файл `zones_config_path` не существует — `load_zones_or_fallback`
синтезирует `station` и `column` на всё изображение, так что таймерные
сценарии продолжают работать до того, как разметка появится.

## Как размечать

Запусти `scripts/draw_zones.py --camera-code CAM-167` (нужен доступ к RTSP
с хоста, где запускаешь; удобно делать на ноутбуке, потом коммитить JSON).

LMB — добавить точку, RMB — закрыть полигон, `f/c/s` — переключить тип
(forbidden/column/station), `w` — сохранить в `config/zones/<camera>.json`.
