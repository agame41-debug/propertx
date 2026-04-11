# Hostify Docs Index
Локальный индекс сохранённой документации Hostify API в папке `docs/reference/hostify`.

## Что здесь лежит
В папке находятся сохранённые HTML-снимки страницы docs Hostify.

Главный файл:
- [Docs _ Hostify API.html](Docs%20_%20Hostify%20API.html)

Это полноценный snapshot docs, внутри которого уже есть весь sidebar, разделы и endpoint’ы.

Остальные файлы вида `Docs _ Hostify API (N).html` в основном выглядят как сохранённые снимки той же страницы с активным anchor/section.

Примеры:
- [Docs _ Hostify API (45).html](Docs%20_%20Hostify%20API%20(45).html) → `#authentication`
- [Docs _ Hostify API (44).html](Docs%20_%20Hostify%20API%20(44).html) → `#accounting`
- [Docs _ Hostify API (28).html](Docs%20_%20Hostify%20API%20(28).html) → `#reservations`
- [Docs _ Hostify API (7).html](Docs%20_%20Hostify%20API%20(7).html) → `#transactions`
- [Docs _ Hostify API (47).html](Docs%20_%20Hostify%20API%20(47).html) → `#errors`

## Что уже удалось извлечь
- Base URL: `https://api-rms.hostify.com/`
- Auth header: `x-api-key: <KEY>`
- В master HTML найдено `110` уникальных endpoint’ов
- В docs есть `20` top-level sections

## Top-level sections
| Раздел | Подразделов |
|---|---:|
| `Introduction` | 0 |
| `Authentication` | 0 |
| `Accounting` | 7 |
| `Calendar` | 8 |
| `Custom stay` | 3 |
| `CTA/CTD Restrictions` | 3 |
| `Guests` | 3 |
| `Inbox` | 13 |
| `Integrations` | 3 |
| `Listings` | 29 |
| `Create Listing` | 6 |
| `Reservations` | 10 |
| `Push Notifications using Amazon SNS` | 4 |
| `Custom Fields` | 6 |
| `Seasonal Promotions (In BETA not available)` | 9 |
| `Reviews` | 3 |
| `Search` | 0 |
| `Transactions` | 9 |
| `Users` | 8 |
| `Errors` | 0 |

## Разделы, которые важны для reconcile/app-first логики
### P0 — начать отсюда
- `Authentication`
- `Accounting`
- `Integrations`
- `Listings`
- `Reservations`
- `Transactions`
- `Errors`
- `Search`

### P1 — полезно почти сразу
- `Custom Fields`
- `Push Notifications using Amazon SNS`
- `Guests`

### P2 — вторично
- `Calendar`
- `Users`

### P3 — можно пока не трогать
- `Inbox`
- `Reviews`
- `CTA/CTD Restrictions`
- `Custom stay`
- `Seasonal Promotions`
- `Create Listing`

## Быстрый sitemap по важным разделам
### Authentication
Anchor:
- `#authentication`

Что зафиксировано:
- auth по `x-api-key`

Локальный snapshot:
- [Docs _ Hostify API (45).html](Docs%20_%20Hostify%20API%20(45).html)

### Accounting
Anchor:
- `#accounting`

Подразделы:
- `Get invoice`
- `List invoices`
- `Change invoice external fields`
- `Get company`
- `List companies`
- `Get counterparty`
- `List counterparties`

Endpoint’ы:
- `GET /invoices/<ID>`
- `GET /invoices`
- `POST /invoices/set_external_data`
- `GET /companies/<ID>`
- `GET /companies`
- `GET /counterparties/<ID>`
- `GET /counterparties`

Локальный snapshot:
- [Docs _ Hostify API (44).html](Docs%20_%20Hostify%20API%20(44).html)

### Integrations
Anchor:
- `#integrations`

Подразделы:
- `Integration object`
- `Get integration`
- `List integration`

Endpoint’ы:
- `GET /integrations/<ID>`
- `GET /integrations`

Почему важно:
- это ключ к связке `Hostify <-> Airbnb/Booking`

### Listings
Anchor:
- `#listings`

Подразделы:
- `Listing object`
- `Get listing`
- `Update listing`
- `List listings`
- `List children`
- `Get available listings`
- `Listing price`
- `Clone listing`
- `Clone state`
- `Listing list/unlist`
- `Get listing fees`
- `Update listing fees`
- `Get listing photos`
- `Upload listing photos`
- `Upload listing photos async`
- `Delete listing photos`
- `Reorder listing photos`
- `Get listing translations`
- `Create translations`
- `Update translations`
- `Delete translations`
- `Get listing booking restrictions`
- `Delete listing with its children`
- `Get access codes`
- `Update access codes`
- `Get guest guide`
- `Update guest guide`
- `Get listing status`
- `Update listing status`

Ключевые endpoint’ы для нас:
- `GET /listings`
- `GET /listings/<ID>`
- `GET /listings/children/<ID>`
- `GET /listings/available`
- `GET /listings/price`
- `GET /listings/listing_fees/<LISTING_ID>`

Почему важно:
- это будущий canonical слой объектов вместо части alias-магии в `reconcile.py`

### Reservations
Anchor:
- `#reservations`

Подразделы:
- `Reservation object`
- `Get reservation`
- `List reservations`
- `Create reservation`
- `Update reservation`
- `Custom fields`
- `Custom field update`
- `Payment data`
- `Update RemoteLock pin`
- `Payment Request`

Endpoint’ы:
- `GET /reservations/<ID>`
- `GET /reservations`
- `POST /reservations`
- `PUT /reservations/<ID>`
- `GET /reservations/custom_fields/<RESERVATION_ID>`
- `POST /reservations/custom_field_update`
- `POST /reservations/payment_data`
- `POST /reservations/update_remotelock_pin/<RESERVATION_ID>`
- `POST /reservations/payment_request`

Локальные snapshots:
- [Docs _ Hostify API (28).html](Docs%20_%20Hostify%20API%20(28).html)
- [Docs _ Hostify API.html](Docs%20_%20Hostify%20API.html)

Почему важно:
- это основа source-side sync

### Search
Anchor:
- `#search`

Endpoint:
- `GET /search`

Почему важно:
- если search/filter хороший, incremental sync станет намного проще

### Transactions
Anchor:
- `#transactions`

Подразделы:
- `Transaction object`
- `Get transaction`
- `List transactions`
- `Create transaction`
- `Update transaction`
- `Get transaction tags`
- `Create transaction tag`
- `Update transaction tag`
- `Delete transactions tags`

Endpoint’ы:
- `GET /transactions/<ID>`
- `GET /transactions`
- `POST /transactions`
- `PUT /transactions/<ID>`
- `GET /transactions/tags/<TRANSACTION_ID>`
- `POST /transactions/tags`
- `DELETE /transactions/tags/<TAG_ID>/<TRANSACTION_ID>`

Локальные snapshots:
- [Docs _ Hostify API (7).html](Docs%20_%20Hostify%20API%20(7).html)
- [Docs _ Hostify API (10).html](Docs%20_%20Hostify%20API%20(10).html)
- [Docs _ Hostify API (11).html](Docs%20_%20Hostify%20API%20(11).html)

Почему важно:
- это потенциальная замена части ручной финансовой логики

### Custom Fields
Anchor:
- `#custom-fields`

Подразделы:
- `Get Custom Field`
- `List Custom Fields`
- `Create Custom Field`
- `Update Custom Field`
- `Delete Custom Field`
- `Set Custom Field Values`

Endpoint’ы:
- `GET /custom_fields/<ID>`
- `GET /custom_fields`
- `POST /custom_fields`
- `POST /custom_fields/update`
- `DELETE /custom_fields`
- `POST /custom_fields/set_values`

Почему важно:
- сюда потенциально можно положить внутренний accounting/listing mapping

### Push Notifications using Amazon SNS
Anchor:
- `#push-notifications-using-amazon-sns`

Подразделы:
- `Get notification`
- `List notifications`
- `Create notification`
- `Delete Notification`

Endpoint’ы:
- `GET /webhooks_v2/<ID>`
- `GET /webhooks_v2`
- `DELETE /webhooks_v2/<ID>`

Почему важно:
- хороший кандидат на near-real-time sync вместо постоянного polling

### Errors
Anchor:
- `#errors`

Локальный snapshot:
- [Docs _ Hostify API (47).html](Docs%20_%20Hostify%20API%20(47).html)

Почему важно:
- тут надо смотреть формат ошибок и retry semantics

## Практический вывод
Если делать приложение на основе Hostify, то минимальный “боевой” набор для изучения такой:

1. `Authentication`
2. `Integrations`
3. `Listings`
4. `Reservations`
5. `Transactions`
6. `Accounting`
7. `Custom Fields`
8. `Search`
9. `Errors`

## Следующий логичный шаг
На базе этого индекса можно сделать второй файл:
- `docs/reference/hostify/RECONCILE_FIRST_ENDPOINTS.md`

Туда уже вынести:
- какие endpoint’ы реально нужны для MVP
- какие поля из них забирать
- чем каждый endpoint заменяет текущие CSV/XLS импорты
