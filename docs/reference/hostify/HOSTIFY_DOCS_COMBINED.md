# Hostify API Combined Docs

Нормализованная markdown-версия локально сохранённых Hostify docs для работы нейронки без поиска по десяткам HTML-файлов.

- Source HTML: `Docs _ Hostify API.html`
- Unique endpoints extracted: `109`
- Top-level sections: `20`

## Быстрый обзор

- Base URL: `https://api-rms.hostify.com/`
- Format: `application/json`
- Auth header: `x-api-key: <KEY>`
- Global params: `include_related_objects`, `page`, `per_page`
- Filter object: `field`, `operator`, `value`

## Секции

- `Introduction` (`#introduction`)
- `Authentication` (`#authentication`)
- `Accounting` (`#accounting`)
- `Calendar` (`#calendar`)
- `Custom stay` (`#custom-stay`)
- `CTA/CTD Restrictions` (`#cta-ctd-restrictions`)
- `Guests` (`#guests`)
- `Inbox` (`#inbox`)
- `Integrations` (`#integrations`)
- `Listings` (`#listings`)
- `Create Listing` (`#create-listing`)
- `Reservations` (`#reservations`)
- `Push Notifications using Amazon SNS` (`#push-notifications-using-amazon-sns`)
- `Custom Fields` (`#custom-fields`)
- `Seasonal Promotions (In BETA not available)` (`#seasonal-promotions-(in-beta-not-available)`)
- `Reviews` (`#reviews`)
- `Search` (`#search`)
- `Transactions` (`#transactions`)
- `Users` (`#users`)
- `Errors` (`#errors`)

## Introduction

Section slug: `introduction`

Welcome to Hostify API. Default base url The default base URL for Hostify API is https://api-rms.hostify.com/ All data should be sent in JSON format and with a Content-Type: application/json header. Note: for security reasons, all Hostify APIs are served over HTTPS only. Global params Parameter Type Values Default include_related_objects int 0-1 0 page int positive 1 per_page int positive 20 Filter object Parameter Description Values field Subject of the filter operator Comparison operator = <> < > <= >= in not_in between not_between value Value to filter by Operators in , not_in , between and not_between expects at least 2 comma-separated values.

## Authentication

Section slug: `authentication`

Hostify uses API keys to allow access to the API. You can register a new API key at our developer portal. Hostify expects for the API key to be included in all API requests to the server in a header that looks like the following: x-api-key: '<KEY>'

## Accounting

Section slug: `accounting`

### Endpoints And Subsections

- `Get invoice`: `GET https://api-rms.hostify.com/invoices/<ID>`
- `List invoices`: `GET https://api-rms.hostify.com/invoices`
- `Change invoice external fields`: `POST https://api-rms.hostify.com/invoices/set_external_data`
- `Get company`: `GET https://api-rms.hostify.com/companies/<ID>`
- `List companies`: `GET https://api-rms.hostify.com/companies`
- `Get counterparty`: `GET https://api-rms.hostify.com/counterparties/<ID>`
- `List counterparties`: `GET https://api-rms.hostify.com/counterparties`

### Get invoice

Slug: `invoices-get`

- Method: `GET`
- URL: `https://api-rms.hostify.com/invoices/<ID>`
- Summary: information

### List invoices

Slug: `invoices-list`

- Method: `GET`
- URL: `https://api-rms.hostify.com/invoices`
- Summary: Get all invoices

#### Fields

| Parameter | Type | Default | Example |
|---|---|---|---|
| start_date (optional) | date | 2026-01-01 | 2018-01-01 |
| end_date (optional) | date | 2026-12-31 | 2018-01-31 |

### Change invoice external fields

Slug: `set-external-data`

- Method: `POST`
- URL: `https://api-rms.hostify.com/invoices/set_external_data`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| id | int | invoice id, readonly | 21 |
| external_id (optional) | int |  | abc |
| external_status (optional) | string |  | blue |
| external_details (optional) | string |  | abc |

### Get company

Slug: `companies-get`

- Method: `GET`
- URL: `https://api-rms.hostify.com/companies/<ID>`

### List companies

Slug: `companies-list`

- Method: `GET`
- URL: `https://api-rms.hostify.com/companies`

### Get counterparty

Slug: `counterparties-get`

- Method: `GET`
- URL: `https://api-rms.hostify.com/counterparties/<ID>`

### List counterparties

Slug: `counterparties-list`

- Method: `GET`
- URL: `https://api-rms.hostify.com/counterparties`

## Calendar

Section slug: `calendar`

### Endpoints And Subsections

- `Get calendar`: `GET https://api-rms.hostify.com/calendar`
- `Get single calendar`: `GET https://api-rms.hostify.com/calendar/<ID>`
- `Update calendar`: `PUT https://api-rms.hostify.com/calendar`
- `Bulk update calendar (single listing)`: `PUT https://api-rms.hostify.com/calendar/bulk_listings/<LISTING_ID>`
- `Bulk update calendar (multiple listings)`: `PUT https://api-rms.hostify.com/calendar/bulk_listings`
- `Bulk add Seasons for a listing (deprecated)`: `POST https://api-rms.hostify.com/calendar/bulk_listing_seasons`
- `Bulk update Seasons for a listing (deprecated)`: `PUT https://api-rms.hostify.com/calendar/bulk_listing_seasons_update`

### Object Definitions In This Section

- `Calendar object`

### Calendar object

Slug: `calendar-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| date | date | Format: YYYY-MM-DD |
| status | string | available
unavailable
booked |
| price | float | The price that that day cost |
| currency | string | 3 letter ISO currency code |
| listing_id | int | Listing ID |
| reservation_id | int | Ref to Reservation |
| min_stay | int | Minimum nights required for this date |
| cta | int | Check-to-arrive: 1 (allowed), 0 (not allowed) |
| ctd | int | Check-to-depart: 1 (allowed), 0 (not allowed) |
| note | string | Calendar note |

### Get calendar

Slug: `get-calendar`

- Method: `GET`
- URL: `https://api-rms.hostify.com/calendar`
- Summary: Get listing calendar 📋

#### Fields

| Parameter | Type | Values | Default | Example |
|---|---|---|---|---|
| listing_id | int |  |  | 1000 |
| filters (optional) | array | status
price
currency |  | [
  {
   "field" : "status" ,
   "operator" : "<>" ,
   "value" : "booked"
  } ,
  {
   "field" : "price" ,
   "operator" : ">=" ,
   "value" : 100
  }
] |
| start_date (optional) | date |  | 2026-01-01 | 2018-01-01 |
| end_date (optional) | date |  | 2026-12-31 | 2018-01-31 |

### Get single calendar

Slug: `view-calendar`

- Method: `GET`
- URL: `https://api-rms.hostify.com/calendar/<ID>`

### Update calendar

Slug: `update-calendar`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/calendar`
- Summary: Add at least one of the optional parameters to the request body. 💡 Note: This endpoint supports basic calendar updates (price, availability, note). For advanced features, use the Bulk update calendar (single listing) endpoint below. 📝 Example: Update Price and Availability PUT /calendar Content-Type: application/json { "listing_id": 12345, "start_date": "2025-12-20", "end_date": "2025-12-31", "price": 150.00, "is_available": 1, "note": "Holiday season pricing" }

#### Fields

| Parameter | Type | Range | Example |
|---|---|---|---|
| listing_id | int |  | 1000 |
| start_date | date |  | 2018-01-01 |
| end_date | date |  | 2018-01-31 |
| is_available (optional) | int | 0-1 | 0 |
| price (optional) | float |  | 123.45 |
| note (optional) | string |  | Note |

### Bulk update calendar (single listing)

Slug: `bulk-update-single-calendar`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/calendar/bulk_listings/<LISTING_ID>`
- Summary: Update multiple date ranges for a single listing in one request. This endpoint supports all calendar fields. 📝 Example 1: Basic Update PUT /calendar/bulk_listings/12345 Content-Type: application/json { "calendar": [ { "start_date": "2025-12-20", "end_date": "2025-12-26", "price": 150.00, "is_available": 1 }, { "start_date": "2025-12-27", "end_date": "2025-12-31", "price": 175.00, "is_available": 1 } ] } 💡 Tip: You can update multiple date ranges in one request. Each date range can have different pricing.

#### Fields

| Parameter | Type | Example |
|---|---|---|
| calendar | array | [
  {
   "start_date" : "YYYY-MM-DD" ,
   "end_date" : "YYYY-MM-DD" ,
   "is_available" : "int" ,
   "price" : "float" ,
   "note" : "string" ,
   "bookingValue" : "float|null (Minimum Booking Value)" ,
   "los" : " [ { \ "los\ " : int , \ "adjustment\ " : float } ] (Length of Stay Pricing)" ,
   "min_stay" : "int (optional)" ,
   "cta" : "int (optional)" ,
   "ctd" : "int (optional)"
  } ,
  {
   "start_date" : "YYYY-MM-DD" ,
   "end_date" : "YYYY-MM-DD" ,
   "is_available" : "int" ,
   "price" : "float" ,
   "note" : "string" ,
   "bookingValue" : "float|null"
  }
] |

### Bulk update calendar (multiple listings)

Slug: `bulk-update-multiple-calendar`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/calendar/bulk_listings`
- Summary: Update calendar for multiple listings in one request. Each listing can have different date ranges and settings. Supported Fields start_date - Start date (YYYY-MM-DD) end_date - End date (YYYY-MM-DD) price - Price per night is_available - Availability (1 = available, 0 = unavailable, -1 = default) note - Calendar note min_stay - Minimum nights required cta - Check-to-arrive (1 = allowed, 0 = not allowed, -1 = default) ctd - Check-to-depart (1 = allowed, 0 = not allowed, -1 = default) 📝 Example: Update Multiple Listings PUT /calendar/bulk_listings Content-Type: application/json [ { "listing_id": 12345, "calendar": [ { "start_date": "2025-12-20", "end_date": "2025-12-31", "price": 150.00, "is_available": 1, "min_stay": 3 } ] }, { "listing_id": 12346, "calendar": [ { "start_date": "2025-12-20", "end_date": "2025-12-31", "price": 200.00, "is_available": 1, "min_stay": 2 } ] } ] This updates calendar for two different listings with different prices and minimum stay requirements.

#### Fields

| Parameter | Type | Example |
|---|---|---|
|  | array | [
  {
   "listing_id" : "int" ,
   "calendar" : [
    {
     "start_date" : "YYYY-MM-DD" ,
     "end_date" : "YYYY-MM-DD" ,
     "is_available" : "int" ,
     "price" : "float" ,
     "note" : "string" ,
     "bookingValue" : "float|null (optional - Minimum Booking Value)" ,
     "los" : " [ { \ "los\ " : int , \ "adjustment\ " : float } ] (optional - Length of Stay Pricing)" ,
     "min_stay" : "int (optional)" ,
     "cta" : "int (optional)" ,
     "ctd" : "int (optional)"
    } ,
    {
     "start_date" : "YYYY-MM-DD" ,
     "end_date" : "YYYY-MM-DD" ,
     "is_available" : "int" ,
     "price" : "float" ,
     "note" : "string"
    }
   ]
  }
] |

### Bulk add Seasons for a listing (deprecated)

Slug: `bulk-listing-seasons`

- Method: `POST`
- URL: `https://api-rms.hostify.com/calendar/bulk_listing_seasons`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| listing_id | int |  | 1000 |
| seasons | array | start_date
end_date
color
cta
ctd
min_stay
price
name | {
  "start_date" : "2000-01-01" ,
  "end_date" : "2000-12-30" ,
  "color" : "#EDC9D4" ,
  "cta" : "1 , 2 , 3 , 4 , 5 , 6 , 7" ,
  "ctd" : "1 , 2 , 3 , 4 , 5 , 6 , 7" ,
  "min_stay" : 1,
  "price" : 123.45,
  "name" : "Season December"
} |

### Bulk update Seasons for a listing (deprecated)

Slug: `bulk-listing-seasons-update`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/calendar/bulk_listing_seasons_update`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| listing_id | int |  | 1000 |
| seasons | array | start_date
end_date
color
cta
ctd
min_stay
price
name | {
  "start_date" : "2000-01-01" ,
  "end_date" : "2000-12-30" ,
  "color" : "#EDC9D4" ,
  "cta" : " [ 1 , 2 , 3 , 4 , 5 , 6 , 7 ] " ,
  "ctd" : " [ 1 , 2 , 3 , 4 , 5 , 6 , 7 ] " ,
  "min_stay" : 1,
  "price" : 123.45,
  "name" : "Season December"
} |

## Custom stay

Section slug: `custom-stay`

### Endpoints And Subsections

- `Get custom stay`: `GET https://api-rms.hostify.com/custom_stay`
- `Set custom stay`: `POST https://api-rms.hostify.com/custom_stay`

### Object Definitions In This Section

- `Custom stay object`

### Custom stay object

Slug: `custom-stay-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| name | string |  |
| date_start | date | Format: YYYY-MM-DD |
| date_end | date | Format: YYYY-MM-DD |
| min_stay | int | Minimum number of nights a guest can book |
| checkin_weekday | int | Numeric representation of the day of the week: 0 (for Sunday) through 6 (for Saturday) |

### Get custom stay

Slug: `get-custom-stay`

- Method: `GET`
- URL: `https://api-rms.hostify.com/custom_stay`
- Summary: Get listing custom stay rules

#### Fields

| Parameter | Type | Example |
|---|---|---|
| listing_id | int | 1000 |

### Set custom stay

Slug: `set-custom-stay`

- Method: `POST`
- URL: `https://api-rms.hostify.com/custom_stay`
- Summary: Create or change custom stay rule

#### Fields

| Parameter | Type | Example |
|---|---|---|
| listing_id | int | 1000 |
| default_min_stay | int | 1 |
| custom_stay | array | [
  {
   "min_stay" : 3,
   "date_start" : "YYYY-MM-DD" ,
   "date_end" : "YYYY-MM-DD" ,
   "checkin_weekday" : null
  } ,
  {
   "min_stay" : 2,
   "date_start" : null,
   "date_end" : null,
   "checkin_weekday" : 6
  }
] |

## CTA/CTD Restrictions

Section slug: `cta-ctd-restrictions`

### Endpoints And Subsections

- `Get CTA/CTD restrictions`: `GET https://api-rms.hostify.com/cta_ctd`
- `Set CTA/CTD restrictions`: `POST https://api-rms.hostify.com/cta_ctd`

### Object Definitions In This Section

- `CTA/CTD restriction object`

### CTA/CTD restriction object

Slug: `cta-ctd-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| listing_id | int |  |
| date_start | date | Format: YYYY-MM-DD |
| date_end | date | Format: YYYY-MM-DD |
| cta | int | Closed to arrival |
| ctd | int | Closed to departure |

### Get CTA/CTD restrictions

Slug: `get-cta-ctd`

- Method: `GET`
- URL: `https://api-rms.hostify.com/cta_ctd`
- Summary: Get listing CTA/CTD restrictions

#### Fields

| Parameter | Type | Example |
|---|---|---|
| listing_id | int | 1000 |

### Set CTA/CTD restrictions

Slug: `set-cta-ctd`

- Method: `POST`
- URL: `https://api-rms.hostify.com/cta_ctd`
- Summary: Create or change CTA/CTD restrictions. Important - The endpoint expects all the CTA/CTD restrictions in a single call, existing records will be overwritten!

#### Fields

| Parameter | Type | Example |
|---|---|---|
| listing_id | int | 1000 |
| restrictions | array | [
  {
   "date_start" : "YYYY-MM-DD" ,
   "date_end" : "YYYY-MM-DD" ,
   "cta" : 1,
   "ctd" : 1
  } ,
  {
   "date_start" : "YYYY-MM-DD" ,
   "date_end" : "YYYY-MM-DD" ,
   "cta" : 1,
   "ctd" : 0
  }
] |

## Guests

Section slug: `guests`

### Endpoints And Subsections

- `Get guest`: `GET https://api-rms.hostify.com/guests/<ID>`
- `List guests`: `GET https://api-rms.hostify.com/guests`

### Object Definitions In This Section

- `Guest object`

### Guest object

Slug: `guest-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| channel_guest_id | int | Channel unique id |
| name | string |  |
| email | string |  |
| phone | string |  |
| location | string |  |
| is_verified | int | 0-1 |
| has_facebook | int | 0-1 |
| has_governmentid | int | 0-1 |
| has_email | int | 0-1 |
| has_phone | int | 0-1 |
| reviews | int | # of reviews |
| about | string |  |
| work | string |  |
| languages | [string] |  |
| original_file | string | Guest picture URL |
| notes | string |  |
| integration_id | int | Ref to Integration |

### Get guest

Slug: `get-guest`

- Method: `GET`
- URL: `https://api-rms.hostify.com/guests/<ID>`

### List guests

Slug: `list-guests`

- Method: `GET`
- URL: `https://api-rms.hostify.com/guests`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| listing_id | int |  | 1000 |
| filters (optional) | array | channel_guest_id
phone
email | [
  {
   "field" : "channel_guest_id" ,
   "operator" : "=" ,
   "value" : "12345"
  }
] |

## Inbox

Section slug: `inbox`

### Endpoints And Subsections

- `Get thread`: `GET https://api-rms.hostify.com/inbox/<ID>`
- `List threads`: `GET https://api-rms.hostify.com/inbox`
- `Assign thread`: `POST https://api-rms.hostify.com/inbox/assignee`
- `Post a reply`: `POST https://api-rms.hostify.com/inbox/reply`
- `Post an image reply`: `POST https://api-rms.hostify.com/inbox/reply_image`
- `Receive a reply`: `POST https://api-rms.hostify.com/inbox/receive_reply`
- `Receive an image reply`: `POST https://api-rms.hostify.com/inbox/receive_reply_image`
- `Accept reservation`: `POST https://api-rms.hostify.com/reservations/accept`
- `Decline reservation`: `POST https://api-rms.hostify.com/reservations/decline`
- `Pre-approve`: `POST https://api-rms.hostify.com/reservations/pre_approve`
- `Special offer`: `POST https://api-rms.hostify.com/reservations/special_offer`

### Object Definitions In This Section

- `Thread object`
- `Message object`

### Thread object

Slug: `thread-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| channel_thread_id | int | Channel unique id |
| integration_id | int | Ref to Integration |
| listing_id | int | Ref to Listings |
| reservation_id | int | Ref to Reservation |
| guest_id | int | Ref to Guest |
| answered | int | 0-1 |
| channel_unread | int | 0-1 |
| preview | string | Last message text |
| last_message | datetime | The time of last message YYYY-MM-DD HH:mm:ss |
| nights | int |  |
| guests | int |  |
| start_date | date | Inquiry start date YYYY-MM-DD |
| is_archived | int | 0-1 |
| assignee_id | int | ID of the user assigned to this thread (null if unassigned) |
| assignee | object | Assignee user object with fields: id , first_name , last_name , name , email , avatar

(null if unassigned) |

### Message object

Slug: `message-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| message | string |  |
| notes | string |  |
| guest_id | int | Ref to Guest |
| guest_name | string |  |
| guest_thumb | string | Guest picture URL |
| created | datetime | The time of last message YYYY-MM-DD HH:mm:ss |
| is_automatic | int | 0-1 |

### Get thread

Slug: `get-thread`

- Method: `GET`
- URL: `https://api-rms.hostify.com/inbox/<ID>`

### List threads

Slug: `list-threads`

- Method: `GET`
- URL: `https://api-rms.hostify.com/inbox`

### Assign thread

Slug: `assign-thread`

- Method: `POST`
- URL: `https://api-rms.hostify.com/inbox/assignee`
- Summary: Assign or unassign a thread to a user. This endpoint updates the thread assignee in the core system. Request Body The request body should be sent as JSON with the following parameters: thread_id (required): The ID of the thread to assign assignee_id (optional): The user ID to assign the thread to. Pass null to unassign the thread.

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| thread_id | int | The thread ID to assign | 12345 |
| assignee_id (optional) | int | User ID to assign the thread to. Pass null to unassign. | 29 |

### Post a reply

Slug: `reply`

- Method: `POST`
- URL: `https://api-rms.hostify.com/inbox/reply`

#### Fields

| Parameter | Type | Values | Default | Example |
|---|---|---|---|---|
| thread_id | int |  |  | 12345 |
| message | string |  |  | Thank you for your interest! |
| send_by (optional) | string | channel
email
sms
whatsapp | channel | email |

### Post an image reply

Slug: `reply-image`

- Method: `POST`
- URL: `https://api-rms.hostify.com/inbox/reply_image`

#### Fields

| Parameter | Type | Description | Values | Example |
|---|---|---|---|---|
| thread_id | int |  |  | 12345 |
| image | {object} | Supported files: jpg|jpeg|png | filename
content_base64 | {
  "filename" : "image.jpg" ,
  "content_base64" : "\/9j\/4QAyRX.......P+Bq44\/\/Z"
} |

### Receive a reply

Slug: `receive-reply`

- Method: `POST`
- URL: `https://api-rms.hostify.com/inbox/receive_reply`

#### Fields

| Parameter | Type | Description | Values | Example |
|---|---|---|---|---|
| thread_id | int |  |  | 12345 |
| sent_by | string |  | host
guest | host |
| channel_message_id | string | Unique message ID from the channel or your system |  | 7bd81640-568a-11f0-91ed-97bac5cf6391 |
| message | string |  |  | Thank you for your interest! |

### Receive an image reply

Slug: `receive-reply-image`

- Method: `POST`
- URL: `https://api-rms.hostify.com/inbox/receive_reply_image`

#### Fields

| Parameter | Type | Description | Values | Example |
|---|---|---|---|---|
| thread_id | int |  |  | 12345 |
| sent_by | string |  | host
guest | host |
| channel_message_id | string | Unique message ID from the channel or your system |  | 7bd81640-568a-11f0-91ed-97bac5cf6391 |
| image | {object} | Supported files: jpg|jpeg|png | filename
content_base64 | {
  "filename" : "image.jpg" ,
  "content_base64" : "\/9j\/4QAyRX.......P+Bq44\/\/Z"
} |

### Accept reservation

Slug: `accept-reservation`

- Method: `POST`
- URL: `https://api-rms.hostify.com/reservations/accept`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| reservation_id | int | 1000 |

### Decline reservation

Slug: `decline-reservation`

- Method: `POST`
- URL: `https://api-rms.hostify.com/reservations/decline`

#### Fields

| Parameter | Type | Description | Values | Example |
|---|---|---|---|---|
| reservation_id | int |  |  | 1000 |
| reason_code (optional) | int | Required for Airbnb reservations. | dates_not_available
not_a_good_fit
waiting_for_better_reservation
not_comfortable | dates_not_available |
| message (optional) | string | Required for Airbnb reservations. |  | Sorry, the dates are not available. |

### Pre-approve

Slug: `pre-approve-reservation`

- Method: `POST`
- URL: `https://api-rms.hostify.com/reservations/pre_approve`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| reservation_id | int | 1000 |

### Special offer

Slug: `special-offer-reservation`

- Method: `POST`
- URL: `https://api-rms.hostify.com/reservations/special_offer`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| reservation_id | int | 1000 |
| start_date | date | 2020-01-01 |
| end_date | date | 2020-01-31 |
| guests | int | 2 |
| price (optional) | float | 123.45 |

## Integrations

Section slug: `integrations`

### Endpoints And Subsections

- `Get integration`: `GET https://api-rms.hostify.com/integrations/<ID>`
- `List integration`: `GET https://api-rms.hostify.com/integrations`

### Object Definitions In This Section

- `Integration object`

### Integration object

Slug: `integration-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| nickname | string |  |
| picture | string | URL |
| user | string |  |
| first_name | string |  |
| last_name | string |  |
| full_name | string |  |

### Get integration

Slug: `get-integration`

- Method: `GET`
- URL: `https://api-rms.hostify.com/integrations/<ID>`

### List integration

Slug: `list-integration`

- Method: `GET`
- URL: `https://api-rms.hostify.com/integrations`

## Listings

Section slug: `listings`

### Endpoints And Subsections

- `Get listing`: `GET https://api-rms.hostify.com/listings/<ID>`
- `Update listing`: `POST https://api-rms.hostify.com/listings/update`
- `List listings`: `GET https://api-rms.hostify.com/listings`
- `List children`: `GET https://api-rms.hostify.com/listings/children/<ID>`
- `Get available listings`: `GET https://api-rms.hostify.com/listings/available`
- `Listing price`: `GET https://api-rms.hostify.com/listings/price`
- `Clone listing`: `POST https://api-rms.hostify.com/listings/clone`
- `Clone state`: `GET https://api-rms.hostify.com/listings/clone/<JOB_ID>`
- `Listing list/unlist`: `POST https://api-rms.hostify.com/listings/channel_list`
- `Get listing fees`: `GET https://api-rms.hostify.com/listings/listing_fees/<LISTING_ID>`
- `Update listing fees`: `PUT https://api-rms.hostify.com/listings/listing_fees_update/<LISTING_ID>`
- `Get listing photos`: `GET https://api-rms.hostify.com/listings/photos/<LISTING_ID>`
- `Upload listing photos`: `POST https://api-rms.hostify.com/listings/photos_upload/<LISTING_ID>`
- `Upload listing photos async`: `POST https://api-rms.hostify.com/listings/photos_upload_async/<LISTING_ID>`
- `Delete listing photos`: `DELETE https://api-rms.hostify.com/listings/photos/<LISTING_ID>`
- `Reorder listing photos`: `POST https://api-rms.hostify.com/listings/photos_sort/<LISTING_ID>`
- `Get listing translations`: `GET https://api-rms.hostify.com/listings/translations/<LISTING_ID>`
- `Create translations`: `POST https://api-rms.hostify.com/listings/translations/<LISTING_ID>`
- `Update translations`: `PUT https://api-rms.hostify.com/listings/translations/<LISTING_ID>`
- `Delete translations`: `DELETE https://api-rms.hostify.com/listings/translations/<LISTING_ID>`
- `Get listing booking restrictions`: `GET https://api-rms.hostify.com/listings/booking_restriction/<LISTING_ID>`
- `Delete listing with its children`: `DELETE https://api-rms.hostify.com/listings/delete_with_children/<LISTING_ID>`
- `Get access codes`: `GET https://api-rms.hostify.com/listings/access_codes/<LISTING_ID>`
- `Update access codes`: `PUT https://api-rms.hostify.com/listings/access_codes/<LISTING_ID>`
- `Get guest guide`: `GET https://api-rms.hostify.com/listings/guest_guide/<LISTING_ID>`
- `Update guest guide`: `PUT https://api-rms.hostify.com/listings/guest_guide/<LISTING_ID>`
- `Get listing status`: `GET https://api-rms.hostify.com/listings/listing_status/<LISTING_ID>`
- `Update listing status`: `PUT https://api-rms.hostify.com/listings/listing_status/<LISTING_ID>`

### Object Definitions In This Section

- `Listing object`

### Listing object

Slug: `listing-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| currency | string | 3 letter ISO currency code |
| channel_listing_id | int | Channel unique id |
| listing_type | int |  |
| room_type | int |  |
| instant_booking | string | off
everyone
experienced
government_id
experienced_guest_with_government_id |
| name | string |  |
| nickname | string |  |
| security_deposit | float |  |
| cleaning_fee | float |  |
| pets_fee | float |  |
| extra_person | float | Extra person price per night |
| guests_included | int |  |
| default_daily_price | float |  |
| weekend_price | float |  |
| weekly_price_factor | float |  |
| monthly_price_factor | float |  |
| min_nights | int |  |
| max_nights | int |  |
| checkin_start | time | Format: HH:mm |
| checkin_end | time | Format: HH:mm |
| checkout | time | Format: HH:mm |
| cancel_policy* | int | 1 - Strict
2 - Moderate
3 - Flexible
5 - Strict |
| price_tip | float |  |
| weekly_tip | float |  |
| monthly_tip | float |  |
| max_notice_days | int |  |
| min_notice_hours | int |  |
| tags | [string] |  |
| thumbnail_file | string | URL |
| country | string |  |
| countrycode | string | 3 letter iso country code |
| state | string |  |
| city | string |  |
| city_id | int |  |
| zipcode | string |  |
| street | string |  |
| lat | float |  |
| lng | float |  |
| timezone_offset | string | Format: HH:mm |
| timezone | string |  |
| price_markup | float |  |
| master_calendar | int | 0-1 |
| service_pms | int | 0-1 |
| is_listed | int | 0-1 |
| service_communication | int | 0-1 |
| revenue_target | float |  |
| high_season_rate | float |  |
| low_season_rate | float |  |
| lowest_rate | float |  |
| back_to_back | int |  |
| guests_wo_reviews | int |  |
| guests_unverified | int |  |
| guest_explaination | int |  |
| guest_house_rules | int |  |
| guests_young | int |  |
| booking_last_minute | int |  |
| booking_future_period | int |  |
| hesitate | int |  |
| booking_additional | int |  |
| communication_sla | int |  |
| communication_whitelabel | int |  |
| communication_phone | int |  |
| checkin_type | int | 0-1 |
| lockbox_code | int |  |
| post_cleaning | int |  |
| post_maintenance | int |  |
| main_contact | string |  |
| emergency_contact | string |  |
| checkin_contact | string |  |
| cleaning_contact | string |  |
| maintenance_contact | string |  |
| integration_id | int | Ref to Integration |
| parent_id | int | Ref to Parent listing |
| revenue_target_1 | int |  |
| revenue_target_2 | int |  |
| revenue_target_3 | int |  |
| revenue_target_4 | int |  |
| revenue_target_5 | int |  |
| revenue_target_6 | int |  |
| revenue_target_7 | int |  |
| revenue_target_8 | int |  |
| revenue_target_9 | int |  |
| revenue_target_10 | int |  |
| revenue_target_11 | int |  |
| revenue_target_12 | int |  |
| listed_channel_listing_id | int |  |
| ical | string |  |
| custom_fields | [string] |  |
| users | [string] |  |
| host | {object} |  |
| photos | [array] |  |
| rooms | [array] |  |
| amenities | [array] |  |
| description | {object} |  |
| details | {object} |  |
| reviews | [array] |  |
| rating | {object} |  |
| calendar | [array] |  |

### Get listing

Slug: `get-listing`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/<ID>`
- Summary: information

#### Fields

| Parameter | Type | Description | Values | Range | Default | Example |
|---|---|---|---|---|---|---|
| guests (optional) | int |  |  |  |  | 2 |
| min_rating (optional) | int |  |  | 1-5 | 4 |  |
| include_related_objects (optional) | int | include_related_objects = 1 returns full listing data, like Room layout, Amenities and more. |  |  |  |  |
| service_pms (optional) | int |  | 0-1 |  |  |  |
| include_fees (optional) | int | Include advanced fees array in the response |  |  |  | 1 |
| guest_app (optional) | int | Include guest guide data (check-in instructions, house manual, directions, emergency info, checkout tasks, area guide) in the response. Requires include_related_objects=1 |  |  |  | 1 |
| lang (optional) | string | Language code for guest guide content (default: en) |  |  |  | en |
| start_date (optional) | date |  |  |  | 2026-01-01 | 2018-01-01 |
| end_date (optional) | date |  |  |  | 2026-12-31 | 2018-01-31 |

### Update listing

Slug: `update-listing`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/update`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| listing_id | int |  | 123 |
| nickname (optional) | string |  | Cozy house |
| price_markup (optional) | int |  | 10 |
| cleaning_fee (optional) | int |  | 30 |
| pets_fee (optional) | float |  | 3.1 |
| extra_person (optional) | int |  | 20 |
| guests_included (optional) | int |  | 2 |
| security_deposit (optional) | int |  | 145 |
| default_daily_price (optional) | int |  | 1000 |
| cancel_policy (optional) | string | strict
moderate
flexible
firm
super_strict_30
super_strict_60
strict_or_non_refundable
moderate_or_non_refundable
flexible_or_non_refundable | strict |
| instant_booking (optional) | string | everyone
experienced
off
off | everyone |
| checkin_start (optional) | string | 08:00 ... 25:00
flexible | 09:00 |
| checkin_end (optional) | string | 09:00 ... 26:00
flexible | 11:00 |
| checkout (optional) | string | 00:00 ... 23:00
flexible | 12:00 |
| min_notice_hours (optional) | int | 0
1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
48
72
168 | 24 |
| max_notice_days (optional) | int | -1
0
30
60
90
120
150
180
210
240
270
300
330
365 | 30 |
| children_allowed (optional) | int | 0
1 |  |
| children_not_allowed_details (optional) | string |  |  |
| infants_allowed (optional) | int | 0
1 |  |
| pets_allowed (optional) | int | 0
1 |  |
| smoking_allowed (optional) | int | 0
1 |  |
| bathroom_shared (optional) | bool | true
false |  |
| tags (optional) | [array] |  | [
  "tag1" ,
  "tag2"
] |
| description (optional) | {object} |  | {
  "name" : "Listing name" ,
  "summary" : "Listing summary" ,
  "space" : "Listing space" ,
  "interaction" : "Listing interaction" ,
  "notes" : "Listing notes" ,
  "neighborhood_overview" : "Neighborhood overview" ,
  "house_rules" : "Listing house rules" ,
  "house_manual" : "Listing house manual" ,
  "checkin_place" : "Listing checkin place" ,
  "arrival_info" : "Listing arrival info" ,
  "transit" : "Asana transit" ,
  "access" : "Guest access"
} |
| weekly_price_discount (optional) | int | 0-99 | 10 |
| monthly_price_discount (optional) | int | 0-99 | 10 |
| non_refundable_discount (optional) | int | 0-99 | 10 |
| early_bird_discount (optional) | {object} |  | {
  "percent" : 10,
  "days" : 10
} |
| last_minute_discount (optional) | {object} |  | {
  "percent" : 10,
  "days" : 10
} |
| listing_type (optional) | string | entire home
private room
shared room | entire home |
| property_type_group (optional) | string | apartments
bnb
boutique hotels and more
houses
secondary units
unique homes | apartments |
| property_type (optional) | string | apartment
bungalow
cabin
condominium
guesthouse
house
guest suite
townhouse
vacation home
boutique hotel
nature lodge
hostel
chalet
dorm
villa
other
bed and breakfast
studio
hotel
resort
castle
aparthotel
boat
cottage
camping
serviced apartment
loft
hut
barn
cave
dome house
earthhouse
farm stay
holiday park
houseboat
igloo
island
kezhan
lighthouse
plane
ranch
religious building
riad
rv
serviced apartment
shipping container
tent
tiny house
tipi
tower
train
treehouse
windmill
yurt | apartment |
| amenities (optional) | array | Essentials
Kitchen
Air conditioning
Heating
Hair dryer
Hangers
Iron
Washer
Dryer
Hot water
TV
Cable TV
Indoor fireplace
Private entrance
Private living room
Lock on bedroom door
Bed linens
Extra pillows and blankets
Wireless Internet
Ethernet connection
Pocket wifi
Laptop friendly workspace
Coffee maker
Refrigerator
Carbon monoxide detector
Cooking basics
Dishes and silverware
Stove
Smoke detector
Shampoo
Microwave
Dishwasher
Oven
Free parking on premises
EV charger
Gym
Pool
Hot tub
Single level home
BBQ grill
Patio or balcony
Garden or backyard
Breakfast
Beach essentials
Baby bath
Baby monitor
Babysitter recommendations
Bathtub
Changing table
Children’s books and toys
Children’s dinnerware
Crib
Fireplace guards
Game console
High chair
Outlet covers
Pack ’n Play/travel crib
Room-darkening shades
Stair gates
Table corner guards
Window guards
Luggage dropoff allowed
Long term stays allowed
Pets live on this property
Cleaning before checkout
Wide hallway clearance
Step-free access
Elevator in building
Wide doorway
Flat smooth pathway to front door
Path to entrance lit at night
Disabled parking spot
Step-free access
Wide doorway
Wide clearance to bed
Accessible-height bed
Firm mattress
Step-free access
Wide doorway
Grab-rails for shower
Tub with shower bench
Roll-in shower with shower bench or chair
Accessible-height toilet
Wide clearance to shower and toilet
Step-free access
Wide doorway
Fire extinguisher
First aid kit
Beachfront
Lake access
Ski in/Ski out
Waterfront
Air purifier
Handheld shower head
Crockery & Cutlery
Drying Rack
Bed Linen & Towels
Toiletries
Central Heating
Kettle
Alarm Clock
Stereo
DVD
CD player
bidet
cupboard
vanity cupboard
toilet
double bed
built-in wardrobes
night table
night tables
reading lamps
desk
chest of drawers
Bathroom
Balcony
Small Balcony
Lounge
Terrace
cooking hob
cooker
Electric kettle
toaster
plates
pans
fridge / freezer
fridge
washing machine
coffee maker
dish rack
vacuum cleaner
gas/electric hob
breakfast bar and stools
freezer
kitchenette
armchairs
coffee table
TV (local channels only)
satellite TV
internet connection
sofa
lamp
table and chairs
shelves
radio
double sofa bed
wardrobe
double sofa
Help Desk
Extra Bed
Mattress
Airport Pick-up Service
Maid Service
swimming pool
Dry Cleaning & Laundry
Dry Cleaning
Laundry
sofabed
shower
washbasin
dining table
Jacuzzi
Fan
Wheelchair access possible
A Gym is in the building for guests to use
On street parking
Underground parking
Guarded parking
Fax Machine
single bed
king size bed
DVD player
city maps
FREE internet access
Wood burning fireplace
Upon weekly stays: maidservice including personal laundry / ironing
armchair
Sauna
Free Wireless Internet
Home Theatre
Cell Phone Rentals
Complimentary Tea & Coffee
Espresso-Machine
Towels
Health Club
en suite bathroom
Ice Maker
Video game system
Game room
Computer rental
blender
High speed Internet access
dining room
Pair of twin beds
Bunk Bed
Heated towel bar
Free weekly cleaning
street
Courtyard
Annex Room
chairs
Business centre
Downtown
Queen size bed
Fan(s) on request
Iron/Ironing board on request
Luggage Storage Facilities
mirror
Private parking
cupboards
Table
Chair
Sea
En suite shower
Ironing Board
Telephone
Computer
Canal view
Pets are welcome
Free cot in the apartment
Free cot on request
Concierge
Washer dryer
Safe
Ski Storage
Mountain View
Seaview
Fitness Room
Spa
Steam room
Room Service
Slope View
Hot Tub (Private)
Hot Tub (Common)
Laundry (Private)
Laundry (Common)
Minibar
Doorman
Breakfast Room
Meeting Room
Restaurant
Bar
Beauty Salon
Children Area
Pull-Out Bed
Garden (Private)
Reception
Laundry Service
Adjoining Rooms
Outlet Adapters
Airline Desk
Meal Plan - American
ATM/Cash Machine
Audio Visual Equipment
Babysitting/Child Services
Barber Shop
On The Bay
Bay View
Meal Plan - Bed and Breakfast
Baby Listening Device
Beach View
Beach
Barber/Beauty Shop
Porters
Bicycle Rentals
Blackboard
Billiards / Pool Tables
Boating
Boutiques
Bowling
Meal Plan - Bermuda
Braille Elevator
Breakfast Buffet
Bathroom Telephone
Canopy / Poster Bed
Car Rental Desk
Casino
Castle Room
Meal Plan - Caribbean
CD Player
Ceiling Fan
City View
Conference Facilities
Conference Suite
Continental Breakfast
Coffee Shop
Coffee Maker in Room
Computer in Room
Concierge Desk
Connecting Rooms
Meal Plan - Continental
Copy Service
Cordless Phone
Cribs Available
Courtesy Car
City Center
Currency Exchange
Data port Available
24 Hour Front Desk
Dining Guide
Dinner
Handicapped Rooms/Facilities
Disco
Doctor on Call
Drugstore
Driving Range
Desk with lamp
Electronic Door Locks
Email Service
Live Entertainment
Meal Plan - European
Express Check In
Executive Desk
Express Checkout
Executive Level
Meal Plan - FAP/Full-board
Full English Breakfast
Female Executive Rooms
Fishing
Florist
Free Local Telephone Calls
Free Transportation
Garden View
Gift Shop
Game Rental
Golf
Golf Course View
Horseback Riding
Jogging Track
Kennels
Childrens Activities
Lake View
Guest Laundromat
Lunch
Meal Plan - MAP/Half-board
Massage
Miniature Golf
In Room Movies
Meeting Facilities
Meeting Suite
Multilingual
Nursery for Children
No Smoking Rooms/Facilities
Night Club
Free Newspaper
News Stand
Ocean View
Overhead Projector
Parasailing
Park View
No Pets Allowed
Phone Service
Picnic Area/Tables
Play Ground
Heated Pool
Indoor Pool
Childrens Pool
Outdoor Pool
Poolside Snackbar
Projector
Squash
River View
Ramp Access to Buildings
24 Hour Room Service
Safe Deposit
Sailing
Scuba Diving
Secretarial Service
24 Hour Security
Shopping Mall
Free Airport Shuttle
Skeet Shooting
Skiing
Cross Country Skiing
Snorkeling
Snowboarding
Fitness Center or Spa
Steam Bath
Telex
Indoor Tennis
Tennis
Outdoor Tennis
Tour Desk
Translation Service
Laundry Services
Vending Machines
VIP Rooms/Services
Volleyball
Wake-up Service
Wedding Services
Wind Surfing
Water Skiing
Heated Guest Rooms
Modem in Room
Murphy Bed
Rollaway Beds
Bathrobes
Solarium
Sprinklers In Rooms
Theater Desk
Temperature Control
Trouser Press
Ipod Dock
Hi-Fi
Duty Free Shop
Parking
Outdoor Parking
Valet Parking
Prayer Mats
Racquetball Courts
Smoking
Chef Provided
Marina View
Smoking allowed
Free parking on the street
Paid parking on the street
Free parking with garage
Paid parking with garage
Free cable internet
Paid cable internet
Paid wireless internet
Paid cot on request
Petanque
Ask for smoking
Ask for pets
Ask for accessibility
Communal pool
Private pool
Ping-pong table
Breakfast booking possible
House cleaning optional
Sports - swimming
Local hospital
Local groceries
Near ocean
Shared Kitchen
Shared Swimming Pool
garage
Family/kids friendly
Laptop workspace
Juicer
Security camera at entrance
MP3
Baby cot
Paddle
Taxi access
Rooftop access
Baby high chair
Baby cot paid
Baby chair on request
Pets paid
Pets accepted under request
Stroller
Baby cutlery
Internet connection on request
Cable TV on request
Dry cleaning on request
Free international calls
Chimney
TV 3D
Smart TV
Free car
Free bike
Credit card payment accepted
No parties
No children under 4
No children under 12
Anyone under 25 years
Anyone under 30 years
Anyone under 35 years
Anyone under 18 years
No reservation more than 30 days
Groups under 18 years
Groups under 25 years
Groups under 30 years
Groups under 35 years
Only families
Anyone under 40 years
No children under 6
Groups under 50 years
Same sex groups under 30 years
Groups under 45 years
Same sex groups under 35 years
Arrivals on Sunday
Families or couples only
Baby high chair paid
Baby chair paid
Wifi USB Adapter
Bottled water
Centrally controlled ventilation
Electrical adapters available
Hypoallergenic rooms
Internet browser TV
Power converters
Sewing kit
Printer
Slippers
Sound system
Sound proofed windows
Weighting scale
Run of the house
Window
Veranda
AC public areas
Aqua sports center
Courier service
Creche
Housekeeping service
Entertainment recreation
Diving
Hair salon
Hotel shops
Island hopping
Jet skiing
Kids eat for free
Late check-out available
Limo town car service available
Security guard
Shoe shine
Shoe polishing machine
Shuttle service
Suitable for children
Ticket service
Turndown service
Umbrella
Welcome amenities
Pool view
Rare view
Heated outdoor pool
Infants not allowed
Family friendly
Car necessary
Car recommended
Car not necessary
Romantic
Luxury
Budget
Water View
Beach chair
Host checkin
Self check-in
Lockbox
Safety card
Buzzer/wireless intercom
24 hour check-in
Smartlock
Keypad
Event friendly
Has dog
Full kitchen
Electric profiling bed
Cats live on this property
Trashcan
House cleaning included
Chauffeur
Wood Stove
Pantry Items
Books
Games
Music Library
Video Library
SG Clean (Singapore)
Safe & Clean (Malaysia)
CESCO (S.Korea)
Safety and Health Administration (Thailand)
Clean & Safe (Portugal)
Measures to reduce infection (Spain)
Safe Hospitality National Protocol (Italy)
Protected Tourist (Brazil)
Not sure
Sanitary Protocol (UNPLV - France)
SafeStay (AHLA - USA)
Reopening vacation rentals guide (DTV & DFV - Germany)
European Holiday Home Association (EHHA - Europe)
Intertek Cristal (3rd party expert - Global)
Safe Travels (WTTC - Global)
Croatian Tourism Association (HUT - Croatia)
SafeHome (VRMA & VRHP)
Not sure
Enhanced cleaning and safety measures
Cleaned with disinfectant
Common surface disinfected
Linens/towels high temperature washed
Self check in/check out
Guest gap (24 hours)
Guest gap (48 hours)
Guest gap (72 hours)
Family
Romantic
Historic
Golf course front
Lakefront
Mountain
Resort
Rural
Ski-in
Ski-out
Town
Village
Oceanfront
Deadbolt lock
Emergency exit
Outdoor lighting
Dining area
Meal delivery
Cabinet locks
Computer monitor
Desk chair
Office
Wifi speed (25 Mbps)
Wifi speed (50 Mbps)
Wifi speed (100 Mbps)
Wifi speed (250 Mbps)
Wifi speed (500 Mbps)
Books for kids
Kitchen island
Fenced pool
Fenced yard
Clothing storage
Dedicated Workspace
Cleaning products
Body soap
Conditioner
Outdoor shower
Shower gel
Mosquito net
Arcade games
Batting cage
Books and reading magazines
Bowling alley
Climbing wall
Exercise equipment
Laser tag
Life size games
Movie theater
Piano
Ping pong table
Record player
Skate ramp
Theme room
Children’s bikes
Children’s playroom
Board games
Outdoor playground
Portable fans
Smoke alarm
Baking sheet
Barbecue utensils
Bread maker
Coffee
Rice maker
Trash compactor
Wine glasses
Beach access
Laundromat nearby
Resort access
Bikes
Boat slip
Fire pit
Hammock
Kayak
Outdoor dinning area
Outdoor furniture
Outdoor kitchen
Sun loungers
Hockey rink
Paid parking off premises
Paid parking on premises
Cleaning available during stay
Air condition window
Babysitter fee
Babysitter on request
Bartender
Bartender fee
Bartender on request
Basketball community
Basketball private
Butler
Butler fee
Butler on request
Chef fee
Chef on request
Courtyard community
Courtyard private
Daily housekeeper
Daily housekeeper fee
Daily housekeeper on request
Dry clean fee
Dry clean on request
Fitness community
Fitness private
Fitness equipment
Grill charcoal
Grill gas
Grocery
Grocery fee
Grocery on request
Ground floor
Kid`s amenities
Laundry fee
Laundry on request
Meal included
Pool community
Private dock
Sauna community
Sauna private
Security system
Site staff
Site staff fee
Site staff on request
Ski rental
Tennis community
Tennis private
Theater
Toddler bed
Valet parking fee
Valet parking on request
Washer on property
Water sports
Water sports fee
Water sports on request
Dryer on property
Air hockey table
Alfresco dining
Alfresco shower
Badminton field
Beach club
Boat
Bocce ball court
Card table
Ceiling hoist
Cooking service
Driver
Foosball table
Pre-stocked fresh groceries
Gated community
Golf cart
Golf course access
Heated floors
Kids club
Media room
Outdoor fireplace
Pizza oven
Pool hoist
Property manager
Seabob
Shuffleboard
Spa access
Stand up paddle board
Step free access
Surfboard
Waitstaff
Windsurfers
Wine cellar
Grab-rails for toilet
Spa services
Rollin shower
Landmark view
Inner Courtyard view
Infinity Pool
Rooftop Pool
Speakers
Fire Alarm
Terrace
Garden Furniture
Scooter Rental
Winter sports
Waterpark
Private Beach
Bath with shower
Whirlpool bath |  |
| sync_category (optional) | string | no_sync
rates_and_availability
all | no_sync |
| service_pms (optional) | int | 0
1 | 0 |
| sync_photos (optional) | int | 0
1 | 0 |
| sync_rooms (optional) | int | 0
1 | 0 |
| sync_amenities (optional) | int | 0
1 | 0 |
| sync_description (optional) | int | 0
1 | 0 |
| sync_settings (optional) | int | 0
1 | 0 |

### List listings

Slug: `list-listings`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings`
- Summary: Get all active listings

#### Fields

| Parameter | Type | Values | Default |
|---|---|---|---|
| service_pms (optional) | int | 0-1 | 1 |

### List children

Slug: `list-children`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/children/<ID>`

### Get available listings

Slug: `available-listings`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/available`
- Summary: Search for available listings

#### Fields

| Parameter | Type | Values | Range | Default | Example |
|---|---|---|---|---|---|
| start_date | date |  |  |  | 2018-01-01 |
| end_date | date |  |  |  | 2018-01-31 |
| guests | int |  |  |  | 2 |
| min_rating (optional) | int |  | 1-5 | 4 |  |
| service_pms (optional) | int | 0-1 |  |  |  |

### Listing price

Slug: `listing-price`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/price`
- Summary: Get listing price

#### Fields

| Parameter | Type | Example |
|---|---|---|
| listing_id | int | 1000 |
| start_date | date | 2018-12-01 |
| end_date | date | 2018-12-03 |
| guests | int | 3 |
| pets | int | 2 |
| include_fees (optional) | int | 1 |

### Clone listing

Slug: `listing-clone`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/clone`
- Summary: to Airbnb

#### Fields

| Parameter | Type | Description | Values | Example |
|---|---|---|---|---|
| username | string | Your username, so we know where to send the notification email. |  | user@hostify.com |
| listing_id | int | The ID of the listing you want to clone. |  | 123 |
| integration | string | The integration nickname to which the listing will be cloned |  | Airbnb integration nickname |
| nickname | string | The nickname of the new listing |  | New listing nickname |
| sync_pricing | int |  |  | 1 |
| sync_photos | int |  |  | 1 |
| sync_rooms | int |  |  | 1 |
| sync_amenities | int |  |  | 1 |
| sync_description | int |  |  | 1 |
| sync_settings | int |  |  | 1 |
| channel_currency | string |  |  | USD |
| price_markup | int |  |  | 10 |
| security_deposit | int |  |  | 145 |
| cleaning_fee | int |  |  | 30 |
| instant_booking | string |  | everyone
experienced
government_id
experienced_guest_with_government_id
off | everyone |
| cancellation_policy | string |  | strict
moderate
flexible | strict |

### Clone state

Slug: `listing-clone-state`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/clone/<JOB_ID>`
- Summary: Get clone listing state

### Listing list/unlist

Slug: `listing-channel-list`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/channel_list`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| listing_id | int | The ID of the listing you want to list/unlist. | 123 |
| channel_listed | bool | 1(list), 0(unlist) | 1 |

### Get listing fees

Slug: `listing-fees`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/listing_fees/<LISTING_ID>`

### Update listing fees

Slug: `listing-fees-update`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/listings/listing_fees_update/<LISTING_ID>`

#### Fields

| Parameter | Type | Description | Values | Example |
|---|---|---|---|---|
| fees | array | You need to provide an array with the fees you want to update. |  | {
  "fees" : [
   {
    "property_fee_id" : 123,
    "amount" : 5,
    "fee_charge_type_id" : 10
   } ,
   {
    "property_fee_id" : 124,
    "amount" : 6,
    "taxable" : true,
    "fee_charge_type_id" : 1
   }
  ]
} |
| property_fee_id | int | The property ID of the fee you want to update. |  | 123 |
| amount (optional) | float | Fee amount |  | 36.99 |
| valid_from (optional) | int |  |  | 1 |
| valid_to (optional) | int |  |  | 30 |
| taxable (optional) | bool |  | true
false |  |
| exclusive (optional) | bool |  | true
false |  |
| fee_charge_type_id (optional) | int | Type should be the ID of the charge type. |  | {
  "Send one of the following options" : {
   "Per Stay" : 1,
   "Per Night" : 2,
   "Percent" : 3,
   "Per Guest" : 4,
   "Per Guest per Night" : 5,
   "Per Month" : 6,
   "Per Adult per Night" : 7,
   "Per Month Dynamic" : 8,
   "Per Pet" : 9,
   "Per Pet per Night" : 10
  }
} |

### Get listing photos

Slug: `listing-photos`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/photos/<LISTING_ID>`

### Upload listing photos

Slug: `listing-photo-upload`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/photos_upload/<LISTING_ID>`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| photos | array | You need to provide valid url to the image | {
  "photos" : [
   " { url } "
  ]
} |

### Upload listing photos async

Slug: `listing-photo-upload-async`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/photos_upload_async/<LISTING_ID>`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| photos | array | You need to provide valid url to the image | {
  "photos" : [
   " { url } "
  ]
} |

### Delete listing photos

Slug: `listing-photos-delete`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/listings/photos/<LISTING_ID>`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| photoId | int | The ID of the photo you want to delete. | 123 |

### Reorder listing photos

Slug: `listing-photos-sort`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/photos_sort/<LISTING_ID>`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| sortingData | array | {
  "sortingData" : [
   {
    "photoId" : 1233,
    "order" : 1
   } ,
   {
    "photoId" : 1234,
    "order" : 2
   }
  ]
} |

### Get listing translations

Slug: `listing-translations`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/translations/<LISTING_ID>`

### Create translations

Slug: `listing-translations-create`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/translations/<LISTING_ID>`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
|  | array | Array of translations. Language code (lang) is required for each translation object. | [
  {
   "lang" : "es" ,
   "name" : "ES name" ,
   "access" : "ES access" ,
   "directions" : "ES directions" ,
   "house_manual" : "ES house_manual" ,
   "house_rules" : "ES house_rules" ,
   "interaction" : "ES interaction" ,
   "neighborhood_overview" : "ES neighborhood_overview" ,
   "notes" : "ES notes" ,
   "space" : "ES space" ,
   "summary" : "ES summary" ,
   "transit" : "ES transit"
  } ,
  {
   "lang" : "de" ,
   "name" : "DE name" ,
   "access" : "DE access" ,
   "directions" : "DE directions" ,
   "house_manual" : "DE house_manual" ,
   "house_rules" : "DE house_rules" ,
   "interaction" : "DE interaction" ,
   "neighborhood_overview" : "DE neighborhood_overview" ,
   "notes" : "DE notes" ,
   "space" : "DE space" ,
   "summary" : "DE summary" ,
   "transit" : "DE transit"
  }
] |

### Update translations

Slug: `listing-translations-update`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/listings/translations/<LISTING_ID>`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
|  | array | Array of translations. Language code (lang) is required for each translation object. | [
  {
   "lang" : "es" ,
   "name" : "ES name update" ,
   "access" : "ES access update" ,
   "directions" : "ES directions update" ,
   "house_manual" : "ES house_manual update" ,
   "house_rules" : "ES house_rules update" ,
   "interaction" : "ES interaction update" ,
   "neighborhood_overview" : "ES neighborhood_overview update" ,
   "notes" : "ES notes update" ,
   "space" : "ES space update" ,
   "summary" : "ES summary update" ,
   "transit" : "ES transit update"
  } ,
  {
   "lang" : "de" ,
   "name" : "DE name update" ,
   "access" : "DE access update" ,
   "directions" : "DE directions update" ,
   "house_manual" : "DE house_manual update" ,
   "house_rules" : "DE house_rules update" ,
   "interaction" : "DE interaction update" ,
   "neighborhood_overview" : "DE neighborhood_overview update" ,
   "notes" : "DE notes update" ,
   "space" : "DE space update" ,
   "summary" : "DE summary update" ,
   "transit" : "DE transit update"
  }
] |

### Delete translations

Slug: `listing-translations-delete`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/listings/translations/<LISTING_ID>`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
|  | array | Array of language codes to be deleted | [
  "es" ,
  "de"
] |

### Get listing booking restrictions

Slug: `listing-booking-restriction`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/booking_restriction/<LISTING_ID>`

### Delete listing with its children

Slug: `delete_with_children`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/listings/delete_with_children/<LISTING_ID>`

#### Fields

| Parameter | Type | Description | Default | Example |
|---|---|---|---|---|
| deleteFromChannel | bool | Delete listings from channel - currently working only for AirBnb listings | false | true |
| continueOnError | bool | Continue with listing deletions on fail | false | true |

### Get access codes

Slug: `access_codes_get`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/access_codes/<LISTING_ID>`

### Update access codes

Slug: `access_codes_put`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/listings/access_codes/<LISTING_ID>`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| access_codes | int | 123 |

### Get guest guide

Slug: `guest_guide_get`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/guest_guide/<LISTING_ID>`

### Update guest guide

Slug: `guest_guide_put`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/listings/guest_guide/<LISTING_ID>`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| guest_guide | string | example text |

### Get listing status

Slug: `listing_status_get`

- Method: `GET`
- URL: `https://api-rms.hostify.com/listings/listing_status/<LISTING_ID>`

### Update listing status

Slug: `listing_status_put`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/listings/listing_status/<LISTING_ID>`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| listing_status | enum | Dirty/Clean |

## Create Listing

Section slug: `create-listing`

### Endpoints And Subsections

- `Location`: `POST https://api-rms.hostify.com/listings/process_location`
- `Layout`: `POST https://api-rms.hostify.com/listings/process_layout`
- `Amenities`: `POST https://api-rms.hostify.com/listings/process_amenities`
- `Translations`: `POST https://api-rms.hostify.com/listings/process_translations`
- `Booking restrictions`: `POST https://api-rms.hostify.com/listings/process_booking_restrictions`
- `Photos`: `POST https://api-rms.hostify.com/listings/process_photos`

### Location

Slug: `listing-location`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/process_location`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| listing_id (optional) | int |  | 1000 |
| name | string |  |  |
| property_type | string | apartment
bungalow
cabin
condominium
guesthouse
house
guest suite
townhouse
vacation home
boutique hotel
nature lodge
hostel
chalet
dorm
villa
other
bed and breakfast
studio
hotel
resort
castle
aparthotel
boat
cottage
camping
serviced apartment
loft
hut
barn
cave
dome house
earthhouse
farm stay
holiday park
houseboat
igloo
island
kezhan
lighthouse
plane
ranch
religious building
riad
rv
serviced apartment
shipping container
tent
tiny house
tipi
tower
train
treehouse
windmill
yurt | apartment |
| pms_services (optional) | int |  | 1 |
| listing_type | string | entire home
private room
shared room | entire home |
| lat | float |  | 16.0544563 |
| lng | float |  | 108.0717219 |
| address | string |  | Da Nang, Vietnam |
| city (optional) | string |  |  |
| zipcode (optional) | string |  | SW1A2DX, 1000 |
| country (optional) | string |  |  |
| state (optional) | string |  |  |
| tags (optional) | [array] |  | [
  "tag1" ,
  "tag2"
] |
| street (optional) | string |  |  |

### Layout

Slug: `listing-layout`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/process_layout`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| listing_id | int |  | 123456 |
| person_capacity (optional) | int |  |  |
| area (optional) | int |  |  |
| area_unit (optional) | string |  | meter |
| bathrooms (optional) | int |  |  |
| bathroom_shared (optional) | bool | true
false |  |
| rooms (optional) | array |  | [
  {
   "room_id" : "type 'new' to create new room or provide existing room_id" ,
   "name" : "Bedroom" ,
   "room_type" : "bedroom" ,
   "bed" : [
    {
     "bed_type" : "king_bed" ,
     "bed_number" : 1
    }
   ]
  }
] |
| room_type | string | bedroom
common space
wc
bathroom
kitchen in the living / dining room
kitchen
living room
livingroom / bedroom
bedroom/living room with kitchen corner
backyard
exterior
front yard
hot tub
garage
gym
kitchenette
laundry room
office
patio
pool
studio
art studio
balcony
bowling alley
casino
children's playroom
courtyard
darkroom
event room
game room
library
movie theater
music studio
photography studio
porch
rooftop
sunroom
terrace
theme room
wine cellar
woodshop
workshop
workspace
garden |  |
| bed_type | string | couch
double_bed,
small_double_bed,
floor_mattress
queen_bed
single_bed
sofa_bed
king_bed
air_mattress
bunk_bed
crib
toddler_bed
hammock
water_bed |  |

### Amenities

Slug: `listing-amenities`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/process_amenities`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| listing_id | int |  | 123456 |
| amenities (optional) | array | Essentials
Kitchen
Air conditioning
Heating
Hair dryer
Hangers
Iron
Washer
Dryer
Hot water
TV
Cable TV
Indoor fireplace
Private entrance
Private living room
Lock on bedroom door
Bed linens
Extra pillows and blankets
Wireless Internet
Ethernet connection
Pocket wifi
Laptop friendly workspace
Coffee maker
Refrigerator
Carbon monoxide detector
Cooking basics
Dishes and silverware
Stove
Smoke detector
Shampoo
Microwave
Dishwasher
Oven
Free parking on premises
EV charger
Gym
Pool
Hot tub
Single level home
BBQ grill
Patio or balcony
Garden or backyard
Breakfast
Beach essentials
Baby bath
Baby monitor
Babysitter recommendations
Bathtub
Changing table
Children’s books and toys
Children’s dinnerware
Crib
Fireplace guards
Game console
High chair
Outlet covers
Pack ’n Play/travel crib
Room-darkening shades
Stair gates
Table corner guards
Window guards
Luggage dropoff allowed
Long term stays allowed
Pets live on this property
Cleaning before checkout
Wide hallway clearance
Step-free access
Elevator in building
Wide doorway
Flat smooth pathway to front door
Path to entrance lit at night
Disabled parking spot
Step-free access
Wide doorway
Wide clearance to bed
Accessible-height bed
Firm mattress
Step-free access
Wide doorway
Grab-rails for shower
Tub with shower bench
Roll-in shower with shower bench or chair
Accessible-height toilet
Wide clearance to shower and toilet
Step-free access
Wide doorway
Fire extinguisher
First aid kit
Beachfront
Lake access
Ski in/Ski out
Waterfront
Air purifier
Handheld shower head
Crockery & Cutlery
Drying Rack
Bed Linen & Towels
Toiletries
Central Heating
Kettle
Alarm Clock
Stereo
DVD
CD player
bidet
cupboard
vanity cupboard
toilet
double bed
built-in wardrobes
night table
night tables
reading lamps
desk
chest of drawers
Bathroom
Balcony
Small Balcony
Lounge
Terrace
cooking hob
cooker
Electric kettle
toaster
plates
pans
fridge / freezer
fridge
washing machine
coffee maker
dish rack
vacuum cleaner
gas/electric hob
breakfast bar and stools
freezer
kitchenette
armchairs
coffee table
TV (local channels only)
satellite TV
internet connection
sofa
lamp
table and chairs
shelves
radio
double sofa bed
wardrobe
double sofa
Help Desk
Extra Bed
Mattress
Airport Pick-up Service
Maid Service
swimming pool
Dry Cleaning & Laundry
Dry Cleaning
Laundry
sofabed
shower
washbasin
dining table
Jacuzzi
Fan
Wheelchair access possible
A Gym is in the building for guests to use
On street parking
Underground parking
Guarded parking
Fax Machine
single bed
king size bed
DVD player
city maps
FREE internet access
Wood burning fireplace
Upon weekly stays: maidservice including personal laundry / ironing
armchair
Sauna
Free Wireless Internet
Home Theatre
Cell Phone Rentals
Complimentary Tea & Coffee
Espresso-Machine
Towels
Health Club
en suite bathroom
Ice Maker
Video game system
Game room
Computer rental
blender
High speed Internet access
dining room
Pair of twin beds
Bunk Bed
Heated towel bar
Free weekly cleaning
street
Courtyard
Annex Room
chairs
Business centre
Downtown
Queen size bed
Fan(s) on request
Iron/Ironing board on request
Luggage Storage Facilities
mirror
Private parking
cupboards
Table
Chair
Sea
En suite shower
Ironing Board
Telephone
Computer
Canal view
Pets are welcome
Free cot in the apartment
Free cot on request
Concierge
Washer dryer
Safe
Ski Storage
Mountain View
Seaview
Fitness Room
Spa
Steam room
Room Service
Slope View
Hot Tub (Private)
Hot Tub (Common)
Laundry (Private)
Laundry (Common)
Minibar
Doorman
Breakfast Room
Meeting Room
Restaurant
Bar
Beauty Salon
Children Area
Pull-Out Bed
Garden (Private)
Reception
Laundry Service
Adjoining Rooms
Outlet Adapters
Airline Desk
Meal Plan - American
ATM/Cash Machine
Audio Visual Equipment
Babysitting/Child Services
Barber Shop
On The Bay
Bay View
Meal Plan - Bed and Breakfast
Baby Listening Device
Beach View
Beach
Barber/Beauty Shop
Porters
Bicycle Rentals
Blackboard
Billiards / Pool Tables
Boating
Boutiques
Bowling
Meal Plan - Bermuda
Braille Elevator
Breakfast Buffet
Bathroom Telephone
Canopy / Poster Bed
Car Rental Desk
Casino
Castle Room
Meal Plan - Caribbean
CD Player
Ceiling Fan
City View
Conference Facilities
Conference Suite
Continental Breakfast
Coffee Shop
Coffee Maker in Room
Computer in Room
Concierge Desk
Connecting Rooms
Meal Plan - Continental
Copy Service
Cordless Phone
Cribs Available
Courtesy Car
City Center
Currency Exchange
Data port Available
24 Hour Front Desk
Dining Guide
Dinner
Handicapped Rooms/Facilities
Disco
Doctor on Call
Drugstore
Driving Range
Desk with lamp
Electronic Door Locks
Email Service
Live Entertainment
Meal Plan - European
Express Check In
Executive Desk
Express Checkout
Executive Level
Meal Plan - FAP/Full-board
Full English Breakfast
Female Executive Rooms
Fishing
Florist
Free Local Telephone Calls
Free Transportation
Garden View
Gift Shop
Game Rental
Golf
Golf Course View
Horseback Riding
Jogging Track
Kennels
Childrens Activities
Lake View
Guest Laundromat
Lunch
Meal Plan - MAP/Half-board
Massage
Miniature Golf
In Room Movies
Meeting Facilities
Meeting Suite
Multilingual
Nursery for Children
No Smoking Rooms/Facilities
Night Club
Free Newspaper
News Stand
Ocean View
Overhead Projector
Parasailing
Park View
No Pets Allowed
Phone Service
Picnic Area/Tables
Play Ground
Heated Pool
Indoor Pool
Childrens Pool
Outdoor Pool
Poolside Snackbar
Projector
Squash
River View
Ramp Access to Buildings
24 Hour Room Service
Safe Deposit
Sailing
Scuba Diving
Secretarial Service
24 Hour Security
Shopping Mall
Free Airport Shuttle
Skeet Shooting
Skiing
Cross Country Skiing
Snorkeling
Snowboarding
Fitness Center or Spa
Steam Bath
Telex
Indoor Tennis
Tennis
Outdoor Tennis
Tour Desk
Translation Service
Laundry Services
Vending Machines
VIP Rooms/Services
Volleyball
Wake-up Service
Wedding Services
Wind Surfing
Water Skiing
Heated Guest Rooms
Modem in Room
Murphy Bed
Rollaway Beds
Bathrobes
Solarium
Sprinklers In Rooms
Theater Desk
Temperature Control
Trouser Press
Ipod Dock
Hi-Fi
Duty Free Shop
Parking
Outdoor Parking
Valet Parking
Prayer Mats
Racquetball Courts
Smoking
Chef Provided
Marina View
Smoking allowed
Free parking on the street
Paid parking on the street
Free parking with garage
Paid parking with garage
Free cable internet
Paid cable internet
Paid wireless internet
Paid cot on request
Petanque
Ask for smoking
Ask for pets
Ask for accessibility
Communal pool
Private pool
Ping-pong table
Breakfast booking possible
House cleaning optional
Sports - swimming
Local hospital
Local groceries
Near ocean
Shared Kitchen
Shared Swimming Pool
garage
Family/kids friendly
Laptop workspace
Juicer
Security camera at entrance
MP3
Baby cot
Paddle
Taxi access
Rooftop access
Baby high chair
Baby cot paid
Baby chair on request
Pets paid
Pets accepted under request
Stroller
Baby cutlery
Internet connection on request
Cable TV on request
Dry cleaning on request
Free international calls
Chimney
TV 3D
Smart TV
Free car
Free bike
Credit card payment accepted
No parties
No children under 4
No children under 12
Anyone under 25 years
Anyone under 30 years
Anyone under 35 years
Anyone under 18 years
No reservation more than 30 days
Groups under 18 years
Groups under 25 years
Groups under 30 years
Groups under 35 years
Only families
Anyone under 40 years
No children under 6
Groups under 50 years
Same sex groups under 30 years
Groups under 45 years
Same sex groups under 35 years
Arrivals on Sunday
Families or couples only
Baby high chair paid
Baby chair paid
Wifi USB Adapter
Bottled water
Centrally controlled ventilation
Electrical adapters available
Hypoallergenic rooms
Internet browser TV
Power converters
Sewing kit
Printer
Slippers
Sound system
Sound proofed windows
Weighting scale
Run of the house
Window
Veranda
AC public areas
Aqua sports center
Courier service
Creche
Housekeeping service
Entertainment recreation
Diving
Hair salon
Hotel shops
Island hopping
Jet skiing
Kids eat for free
Late check-out available
Limo town car service available
Security guard
Shoe shine
Shoe polishing machine
Shuttle service
Suitable for children
Ticket service
Turndown service
Umbrella
Welcome amenities
Pool view
Rare view
Heated outdoor pool
Infants not allowed
Family friendly
Car necessary
Car recommended
Car not necessary
Romantic
Luxury
Budget
Water View
Beach chair
Host checkin
Self check-in
Lockbox
Safety card
Buzzer/wireless intercom
24 hour check-in
Smartlock
Keypad
Event friendly
Has dog
Full kitchen
Electric profiling bed
Cats live on this property
Trashcan
House cleaning included
Chauffeur
Wood Stove
Pantry Items
Books
Games
Music Library
Video Library
SG Clean (Singapore)
Safe & Clean (Malaysia)
CESCO (S.Korea)
Safety and Health Administration (Thailand)
Clean & Safe (Portugal)
Measures to reduce infection (Spain)
Safe Hospitality National Protocol (Italy)
Protected Tourist (Brazil)
Not sure
Sanitary Protocol (UNPLV - France)
SafeStay (AHLA - USA)
Reopening vacation rentals guide (DTV & DFV - Germany)
European Holiday Home Association (EHHA - Europe)
Intertek Cristal (3rd party expert - Global)
Safe Travels (WTTC - Global)
Croatian Tourism Association (HUT - Croatia)
SafeHome (VRMA & VRHP)
Not sure
Enhanced cleaning and safety measures
Cleaned with disinfectant
Common surface disinfected
Linens/towels high temperature washed
Self check in/check out
Guest gap (24 hours)
Guest gap (48 hours)
Guest gap (72 hours)
Family
Romantic
Historic
Golf course front
Lakefront
Mountain
Resort
Rural
Ski-in
Ski-out
Town
Village
Oceanfront
Deadbolt lock
Emergency exit
Outdoor lighting
Dining area
Meal delivery
Cabinet locks
Computer monitor
Desk chair
Office
Wifi speed (25 Mbps)
Wifi speed (50 Mbps)
Wifi speed (100 Mbps)
Wifi speed (250 Mbps)
Wifi speed (500 Mbps)
Books for kids
Kitchen island
Fenced pool
Fenced yard
Clothing storage
Dedicated Workspace
Cleaning products
Body soap
Conditioner
Outdoor shower
Shower gel
Mosquito net
Arcade games
Batting cage
Books and reading magazines
Bowling alley
Climbing wall
Exercise equipment
Laser tag
Life size games
Movie theater
Piano
Ping pong table
Record player
Skate ramp
Theme room
Children’s bikes
Children’s playroom
Board games
Outdoor playground
Portable fans
Smoke alarm
Baking sheet
Barbecue utensils
Bread maker
Coffee
Rice maker
Trash compactor
Wine glasses
Beach access
Laundromat nearby
Resort access
Bikes
Boat slip
Fire pit
Hammock
Kayak
Outdoor dinning area
Outdoor furniture
Outdoor kitchen
Sun loungers
Hockey rink
Paid parking off premises
Paid parking on premises
Cleaning available during stay
Air condition window
Babysitter fee
Babysitter on request
Bartender
Bartender fee
Bartender on request
Basketball community
Basketball private
Butler
Butler fee
Butler on request
Chef fee
Chef on request
Courtyard community
Courtyard private
Daily housekeeper
Daily housekeeper fee
Daily housekeeper on request
Dry clean fee
Dry clean on request
Fitness community
Fitness private
Fitness equipment
Grill charcoal
Grill gas
Grocery
Grocery fee
Grocery on request
Ground floor
Kid`s amenities
Laundry fee
Laundry on request
Meal included
Pool community
Private dock
Sauna community
Sauna private
Security system
Site staff
Site staff fee
Site staff on request
Ski rental
Tennis community
Tennis private
Theater
Toddler bed
Valet parking fee
Valet parking on request
Washer on property
Water sports
Water sports fee
Water sports on request
Dryer on property
Air hockey table
Alfresco dining
Alfresco shower
Badminton field
Beach club
Boat
Bocce ball court
Card table
Ceiling hoist
Cooking service
Driver
Foosball table
Pre-stocked fresh groceries
Gated community
Golf cart
Golf course access
Heated floors
Kids club
Media room
Outdoor fireplace
Pizza oven
Pool hoist
Property manager
Seabob
Shuffleboard
Spa access
Stand up paddle board
Step free access
Surfboard
Waitstaff
Windsurfers
Wine cellar
Grab-rails for toilet
Spa services
Rollin shower
Landmark view
Inner Courtyard view
Infinity Pool
Rooftop Pool
Speakers
Fire Alarm
Terrace
Garden Furniture
Scooter Rental
Winter sports
Waterpark
Private Beach
Bath with shower
Whirlpool bath |  |

### Translations

Slug: `create-listing-translations`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/process_translations`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| listing_id | int |  | 123456 |
| name (optional) | string | Title |  |
| summary (optional) | string |  |  |
| space (optional) | string |  |  |
| interaction (optional) | string | Interaction with guests |  |
| notes (optional) | string | Other things to note |  |
| neighborhood_overview (optional) | string |  |  |
| house_rules (optional) | string |  |  |
| house_manual (optional) | string |  |  |
| checkin_place (optional) | string |  |  |
| arrival_info (optional) | string |  |  |
| transit (optional) | string |  |  |
| access (optional) | string | Guest access |  |

### Booking restrictions

Slug: `listing-booking-restrictions`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/process_booking_restrictions`

#### Fields

| Parameter | Type | Description | Values | Example |
|---|---|---|---|---|
| listing_id | int |  |  | 12345 |
| price | float | Default price |  |  |
| occupancy | int | Guests included |  |  |
| extra_guest_price (optional) | float | Extra guest price |  |  |
| currency | string |  |  | USD, EUR, CHF |
| min_stay_default (optional) | int | Default min stay |  |  |
| max_stay_default (optional) | int | Default max stay |  |  |
| WeekdayMinstay (optional) | array |  |  | {
  "mon" : 1,
  "tue" : 4,
  "fri" : 1
} |
| CustomMinStay (optional) | array |  |  | [
  {
   "date_from" : "2020-02-10" ,
   "date_till" : "2020-02-13" ,
   "nights" : 2,
   "weekdays" : [
    "mon" ,
    "tue" ,
    "wed" ,
    "thu" ,
    "fri" ,
    "sat" ,
    "sun"
   ]
  }
] |
| no_checkin_days (optional) | string |  |  | [
  "mon" ,
  "tue" ,
  "wed" ,
  "thu" ,
  "fri" ,
  "sat" ,
  "sun"
] |
| no_checkout_days (optional) | string |  |  | [
  "mon" ,
  "tue" ,
  "wed" ,
  "thu" ,
  "fri" ,
  "sat" ,
  "sun"
] |
| min_notice_hours (optional) | int | The number of hours required for minimum notice before booking. | 0-24 (from 0 to 24)
48
72
168 |  |
| max_notice_days (optional) | int | The maximum number of days between the booking date and the check in date. | 0
30
60
90
120
150
180
210
240
270
300
330
365 |  |
| turnover_days (optional) | int | Preparation days. Block 1 or 2 nights before and after each reservation. | 1
2 |  |
| checkin_start (optional) | string |  |  | 14:00:00 |
| checkin_end (optional) | string |  |  | 18:30:00 |
| checkout (optional) | string |  |  | 11:30:00 |
| children_allowed (optional) | int |  | 0
1 |  |
| children_not_allowed_details (optional) | string |  |  |  |
| infants_allowed (optional) | int |  | 0
1 |  |
| pets_allowed (optional) | int |  | 0
1 |  |
| smoking_allowed (optional) | int |  | 0
1 |  |

### Photos

Slug: `listing-photos`

- Method: `POST`
- URL: `https://api-rms.hostify.com/listings/process_photos`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| listing_id | int |  | 123456 |
| photos | array | You need to provide valid url to the image |  |

## Reservations

Section slug: `reservations`

### Endpoints And Subsections

- `Get reservation`: `GET https://api-rms.hostify.com/reservations/<ID>`
- `List reservations`: `GET https://api-rms.hostify.com/reservations`
- `Create reservation`: `POST https://api-rms.hostify.com/reservations`
- `Update reservation`: `PUT https://api-rms.hostify.com/reservations/<ID>`
- `Custom fields`: `GET https://api-rms.hostify.com/reservations/custom_fields/<RESERVATION_ID>`
- `Custom field update`: `POST https://api-rms.hostify.com/reservations/custom_field_update`
- `Payment data`: `POST https://api-rms.hostify.com/reservations/payment_data`
- `Update RemoteLock pin`: `POST https://api-rms.hostify.com/reservations/update_remotelock_pin/<RESERVATION_ID>`
- `Payment Request`: `POST https://api-rms.hostify.com/reservations/payment_request`

### Object Definitions In This Section

- `Reservation object`

### Reservation object

Slug: `reservation-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| channel_reservation_id | int | Channel unique id |
| listing_id | int | Ref to Listing |
| parent_listing_id | int | Ref to Parent listing |
| guest_id | int | Ref to Guest |
| integration_id | int | Ref to Integration |
| inbox_id | int | Ref to Inbox thread |
| status_code | int |  |
| status | string | accepted
pending
denied
cancelled
no_show
awaiting_payment
moved
extended
edited
retracted
inquiry
declined_inq
preapproved
offer
withdrawn
expired
timedout
not_possible
new
deleted |
| status_description | string |  |
| currency | string | 3 letter ISO currency code |
| price_per_night | float |  |
| base_price | float |  |
| security_price | float |  |
| extras_price | float |  |
| cleaning_fee | float |  |
| channel_commission | float |  |
| service_charge | float |  |
| subtotal | float |  |
| payout_price | float |  |
| tax_amount | float |  |
| transaction_fee | float |  |
| sum_refunds | float |  |
| source | string |  |
| confirmation_code | string |  |
| checkIn | date | Format: YYYY-MM-DD |
| checkOut | date | Format: YYYY-MM-DD |
| planned_arrival | time | Format: HH:mm |
| planned_departure | time | Format: HH:mm |
| confirmed_at | datetime | Format: YYYY-MM-DD HH:mm |
| nights | int |  |
| guests | int |  |
| adults | int |  |
| children | int |  |
| infants | int |  |
| pets | int |  |
| advance_days | int |  |
| beds_to_be_prepared | int |  |
| notes | string |  |
| extra_info | string |  |
| cancel_penalty | string |  |
| cleaning_notes | string |  |
| updated_at | datetime |  |
| created_at | datetime |  |
| is_manual | bool |  |
| hostify_checkin_form_link | string |  |
| hostify_checkin_form_completed | int |  |
| lock_pin | string | Access code/PIN. Priority: Seam > Tedee > RemoteLock > Akiles > Keyless > Static. Requires include_related_objects=1 |
| lock_link | string | Smart lock magic link (Tedee/Akiles). Requires include_related_objects=1 |

### Get reservation

Slug: `get-reservation`

- Method: `GET`
- URL: `https://api-rms.hostify.com/reservations/<ID>`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| fees (optional) | int | 1 |

### List reservations

Slug: `list-reservations`

- Method: `GET`
- URL: `https://api-rms.hostify.com/reservations`
- Summary: Get listing reservations

#### Fields

| Parameter | Type | Values | Default | Example |
|---|---|---|---|---|
| listing_id (optional) | int |  |  | 1000 |
| filters (optional) | array | guest_id
checkIn
checkOut
source
status
payout_price
confirmation_code
nights
advance_days
guests
hostify_checkin_form_completed |  | [
  {
   "field" : "source" ,
   "operator" : "=" ,
   "value" : "Airbnb"
  } ,
  {
   "field" : "payout_price" ,
   "operator" : ">=" ,
   "value" : 1000
  } ,
  {
   "field" : "status" ,
   "operator" : "in" ,
   "value" : "accepted , pending"
  }
] |
| sort (optional) | string | checkIn
checkOut
confirmed_at |  |  |
| fees (optional) | int | 1
0 | 0 | {
  "reservation" : {
   "id" : 603313779,
   "fees" : {
    "id" : 603313779,
    "fee_id" : 59,
    "description" : null,
    "condition_type" : "online" ,
    "quantity" : 5,
    "amount_net" : 1000,
    "amount_tax" : 0,
    "amount_gross" : 1000,
    "amount_net_total" : 5000,
    "amount_tax_total" : 0,
    "amount_gross_total" : 5000,
    "amount_incl_total" : 5000,
    "start_date" : "2024-12-10" ,
    "end_date" : "2024-12-15" ,
    "fee" : {
     "name" : "Accommodation" ,
     "type" : "accommodation"
    } ,
    "feeChargeType" : {
     "name" : "Per Night"
    }
   }
  }
} |
| start_date (optional) | date |  | 2026-01-01 | 2018-01-01 |
| end_date (optional) | date |  | 2026-12-31 | 2018-01-31 |

### Create reservation

Slug: `create-reservation`

- Method: `POST`
- URL: `https://api-rms.hostify.com/reservations`
- Summary: Create new reservation

#### Fields

| Parameter | Type | Description | Values | Default | Example |
|---|---|---|---|---|---|
| listing_id | int |  |  |  | 1000 |
| start_date | date |  |  |  | 2018-12-01 |
| end_date | date |  |  |  | 2018-12-03 |
| guests | int |  |  |  | 3 |
| pets | int |  |  |  | 1 |
| name | string | Guest name |  |  | John Smith |
| email | string | Guest email |  |  | john@somebody.com |
| phone | string | Guest phone |  |  | +1-541-754-3010 |
| total_price | float |  |  |  | 560.00 |
| note | string |  |  |  | Approximate time of arrival: between 13:00 and 14:00 |
| source | string |  |  |  | Booking.com |
| status (optional) | string | The status of the reservation, if not provided the reservation will be created with status accepted. | accepted
pending |  | pending |
| base_price (optional) | float | If this amount is provided, it will be used as the base price |  |  | 300.00 |
| security_price (optional) | float | Deposit amount |  |  | 36.99 |
| tax_amount (optional) | float | Tax amount |  |  | 36.99 |
| channel_commission (optional) | float | Commission amount |  |  | 36.99 |
| payout_price (optional) | float | Expected host payout amount |  |  | 490.00 |
| skip_restrictions (optional) | bool | Skip minimum stay, person capacity and other restrictions. | true
false | false |  |
| fees (optional) | array |  |  |  | [
  {
   "fee_id" : 59,
   "total" : 157.3
  } ,
  {
   "fee_id" : 19,
   "total" : 5.73
  }
] |

### Update reservation

Slug: `update-reservation`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/reservations/<ID>`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| listing_id | int |  | 11360 |
| status | string | accepted
denied
cancelled_by_host
cancelled_by_guest
no_show | accepted |
| check_in | date |  | 2021-10-12 |
| check_out | date |  | 2021-10-20 |
| planned_arrival | time |  | 12:00:00 |
| planned_departure | time |  | 12:00:00 |
| checked_in_mark | int | 1 (checked-in), 0 (undo checked-in) | 1 |
| checked_out_mark | int | 1 (checked-out), 0 (undo checked-out) | 1 |

### Custom fields

Slug: `custom_fields`

- Method: `GET`
- URL: `https://api-rms.hostify.com/reservations/custom_fields/<RESERVATION_ID>`

### Custom field update

Slug: `custom_field_update`

- Method: `POST`
- URL: `https://api-rms.hostify.com/reservations/custom_field_update`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| reservation_id | int |  | 1000 |
| custom_field_id | int |  | 1000 |
| value | mixed | Value depends on the custom field type | 16:30 |

### Payment data

Slug: `payment_data`

- Method: `POST`
- URL: `https://api-rms.hostify.com/reservations/payment_data`
- Summary: Add the Customer object from your payment provider for subsequent payments. Stripe supported only

#### Fields

| Parameter | Type | Description | Default | Example |
|---|---|---|---|---|
| reservation_id | int |  |  | 1000 |
| customer_id | string | Customer ID provided by Stripe. |  | cus_391Hxzijk |
| payment_processor_id (optional) | int | The ID of the Stripe integration in our system. This is required if you have more than 1 connected account. |  | 1000 |
| is_3ds (optional) | bool | If you use Sources that are connected to the Stripe customer, then this should be set to 0.
If you use PaymentMethods / PaymentIntents this should be set to 1. | 0 |  |

### Update RemoteLock pin

Slug: `update_remotelock_pin`

- Method: `POST`
- URL: `https://api-rms.hostify.com/reservations/update_remotelock_pin/<RESERVATION_ID>`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| reservation_id | int | 1000 |
| pin | int | 1000 |

### Payment Request

Slug: `payment_request_post`

- Method: `POST`
- URL: `https://api-rms.hostify.com/reservations/payment_request`

#### Fields

| Parameter | Type | Description | Example |
|---|---|---|---|
| reservation_id | int | The ID of the reservation. | 700277445 |
| payment_processor_id | int | ID of the payment processor integration. | 6038 |
| amount | float | Amount to be charged or authorized. | 50 |
| action_type | string | Action type, either "charge" or "authorized". | charge |
| type | string | Charge type, either "accommodation" or "deposit". | accommodation |
| description | string | Description of the payment request. | text |

## Push Notifications using Amazon SNS

Section slug: `push-notifications-using-amazon-sns`

### Endpoints And Subsections

- `Get notification`: `GET https://api-rms.hostify.com/webhooks_v2/<ID>`
- `List notifications`: `GET https://api-rms.hostify.com/webhooks_v2`
- `Delete Notification`: `DELETE https://api-rms.hostify.com/webhooks_v2/<ID>`

### Object Definitions In This Section

- `Create notification`

### Get notification

Slug: `get-notification-v2`

- Method: `GET`
- URL: `https://api-rms.hostify.com/webhooks_v2/<ID>`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| id | int | 1 |

### List notifications

Slug: `list-notifications-v2`

- Method: `GET`
- URL: `https://api-rms.hostify.com/webhooks_v2`

### Create notification

Slug: `create-notification-v2`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| url | string |  | https://api.example.com |
| notification_type | string | message_new
move_reservation
new_reservation
update_reservation
create_listing
update_listing
create_update_listing
listing_photo_processed | {
  "notification_type" : "new_reservation"
} |
| auth (optional) | string |  | my_hash_key |

### Delete Notification

Slug: `delete-notification-v2`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/webhooks_v2/<ID>`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| id | int | 1 |

## Custom Fields

Section slug: `custom-fields`

### Endpoints And Subsections

- `Get Custom Field`: `GET https://api-rms.hostify.com/custom_fields/<ID>`
- `List Custom Fields`: `GET https://api-rms.hostify.com/custom_fields`
- `Create Custom Field`: `POST https://api-rms.hostify.com/custom_fields`
- `Update Custom Field`: `POST https://api-rms.hostify.com/custom_fields/update`
- `Delete Custom Field`: `DELETE https://api-rms.hostify.com/custom_fields`
- `Set Custom Field Values`: `POST https://api-rms.hostify.com/custom_fields/set_values`

### Get Custom Field

Slug: `get-custom-fields`

- Method: `GET`
- URL: `https://api-rms.hostify.com/custom_fields/<ID>`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| id | int | 1 |

### List Custom Fields

Slug: `list-custom-fields`

- Method: `GET`
- URL: `https://api-rms.hostify.com/custom_fields`

### Create Custom Field

Slug: `create-custom-fields`

- Method: `POST`
- URL: `https://api-rms.hostify.com/custom_fields`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| ref | string | listing
reservation | listing |
| name | string |  |  |
| type | string | text
long_text
number
date
time
bool
option | option |
| option_values | array |  | {
  "option1" : "1" ,
  "option2" : "2"
} |

### Update Custom Field

Slug: `update-custom-fields`

- Method: `POST`
- URL: `https://api-rms.hostify.com/custom_fields/update`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| custom_field_id | int |  |  |
| name | string |  |  |
| type | string | text
long_text
number
date
time
Yes/No
option | text |

### Delete Custom Field

Slug: `delete-custom-fields`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/custom_fields`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| custom_field_id | int | 1 |

### Set Custom Field Values

Slug: `set-values-custom-fields`

- Method: `POST`
- URL: `https://api-rms.hostify.com/custom_fields/set_values`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| custom_field_id | int |  |
| value | string | Custom field value |
| listing_ids | array | {
  "listing_ids" : [
  1,
  2,
  3
  ]
} |
| reservation_ids | array | {
  "reservation_ids" : [
  1,
  2,
  3
  ]
} |

## Seasonal Promotions (In BETA not available)

Section slug: `seasonal-promotions-(in-beta-not-available)`

### Endpoints And Subsections

- `Get Promotion`: `GET https://api-rms.hostify.com/seasonal_promotions/<ID>`
- `List Promotions`: `GET https://api-rms.hostify.com/seasonal_promotions`
- `Create Promotion`: `POST https://api-rms.hostify.com/seasonal_promotions`
- `Update Promotion`: `PUT https://api-rms.hostify.com/seasonal_promotions/<ID>`
- `Delete Promotion`: `DELETE https://api-rms.hostify.com/seasonal_promotions/<ID>`
- `Get Listings For Promotion`: `GET https://api-rms.hostify.com/seasonal_promotions/listings/<ID>`
- `Add Promotion To Listing`: `POST https://api-rms.hostify.com/seasonal_promotions/listings`
- `Delete Promotion From Listing`: `DELETE https://api-rms.hostify.com/seasonal_promotions/listings/<LISTING_ID>/<ID>`

### Object Definitions In This Section

- `Promotion object`

### Promotion object

Slug: `promotion-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| name | string | Name of the promotion |
| type | string | basic
early_bird
last_minute
los
new_listing |
| discount | decimal | The discount value |
| discount_type | string | percent
absolute |
| threshold_days | date | Number of day for this promotion |
| checkin_from | date | Start date of the checkin for this promotion |
| checkin_till | date | End date of the checkin for this promotion |
| is_active | int | The status of the promotion |

### Get Promotion

Slug: `get-seasonal-promotion`

- Method: `GET`
- URL: `https://api-rms.hostify.com/seasonal_promotions/<ID>`

### List Promotions

Slug: `get-seasonal-promotions`

- Method: `GET`
- URL: `https://api-rms.hostify.com/seasonal_promotions`

### Create Promotion

Slug: `post-seasonal-promotion`

- Method: `POST`
- URL: `https://api-rms.hostify.com/seasonal_promotions`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| name | string |  | Seasonal Promotion Name |
| type | string | basic
early_bird
last_minute
los
new_listing | basic |
| discount | decimal |  | 10.50 |
| discount_type | string | percent
absolute | percent |
| threshold_days | int |  | 5 |
| checkin_from | date |  | 2018-12-01 |
| checkin_till | date |  | 2018-15-01 |
| is_active | int |  | 1 |

### Update Promotion

Slug: `put-seasonal-promotion`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/seasonal_promotions/<ID>`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| name | string |  | Seasonal Promotion Name |
| type | string | basic
early_bird
last_minute
los
new_listing | basic |
| discount | decimal |  | 10.50 |
| discount_type | string | percent
absolute | percent |
| threshold_days | int |  | 5 |
| checkin_from | date |  | 2018-12-01 |
| checkin_till | date |  | 2018-15-01 |
| is_active | int |  | 1 |

### Delete Promotion

Slug: `delete-seasonal-promotion`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/seasonal_promotions/<ID>`

### Get Listings For Promotion

Slug: `get-seasonal-promotion-listings`

- Method: `GET`
- URL: `https://api-rms.hostify.com/seasonal_promotions/listings/<ID>`

### Add Promotion To Listing

Slug: `post-seasonal-promotion-listings`

- Method: `POST`
- URL: `https://api-rms.hostify.com/seasonal_promotions/listings`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| listingId | int | 4207 |
| promotionId | int | 6 |

### Delete Promotion From Listing

Slug: `delete-seasonal-promotion-listings`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/seasonal_promotions/listings/<LISTING_ID>/<ID>`

## Reviews

Section slug: `reviews`

### Endpoints And Subsections

- `Get review`: `GET https://api-rms.hostify.com/reviews/<ID>`
- `List reviews`: `GET https://api-rms.hostify.com/reviews`

### Object Definitions In This Section

- `Review object`

### Review object

Slug: `review-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| reservation_id | int | Ref to Reservation |
| listing_id | int | Ref to Listing |
| parent_listing_id | int | Ref to Parent listing |
| guest_id | int | Ref to Guest |
| integration_id | int | Ref to Integration |
| created | datetime | Format: YYYY-MM-DD HH:mm:ss |
| rating | int | 1-5 |
| accuracy_rating | int | 1-5 |
| checkin_rating | int | 1-5 |
| clean_rating | int | 1-5 |
| communication_rating | int | 1-5 |
| location_rating | int | 1-5 |
| value_rating | int | 1-5 |
| comments | string |  |
| accuracy_comments | string |  |
| checkin_comments | string |  |
| clean_comments | string |  |
| communication_comments | string |  |
| improve_comments | string |  |
| location_comments | string |  |
| value_comments | string |  |

### Get review

Slug: `get-review`

- Method: `GET`
- URL: `https://api-rms.hostify.com/reviews/<ID>`

### List reviews

Slug: `list-reviews`

- Method: `GET`
- URL: `https://api-rms.hostify.com/reviews`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| created_from (optional) | date | 2026-01-15 |
| created_to (optional) | date | 2026-01-20 |
| city (optional) | string | Toronto |

## Search

Section slug: `search`

Search for everything HTTP Request GET https://api-rms.hostify.com/search Query Parameters Parameter Type Values q string type (optional) string guests reservations listings integrations

## Transactions

Section slug: `transactions`

### Endpoints And Subsections

- `Get transaction`: `GET https://api-rms.hostify.com/transactions/<ID>`
- `List transactions`: `GET https://api-rms.hostify.com/transactions`
- `Create transaction`: `POST https://api-rms.hostify.com/transactions`
- `Update transaction`: `PUT https://api-rms.hostify.com/transactions/<ID>`
- `Get transaction tags`: `GET https://api-rms.hostify.com/transactions/tags/<TRANSACTION_ID>`
- `Create transaction  tag`: `POST https://api-rms.hostify.com/transactions/tags`
- `Update transaction tag`: `POST https://api-rms.hostify.com/transactions/tags`
- `Delete transactions tags`: `DELETE https://api-rms.hostify.com/transactions/tags/<TAG_ID>/<TRANSACTION_ID>`

### Object Definitions In This Section

- `Transaction object`

### Transaction object

Slug: `transaction-object`

#### Fields

| Attribute | Type | Description |
|---|---|---|
| id | int | Unique id |
| channel_transaction_id | int | Channel unique id |
| currency | string | 3 letter ISO currency code |
| amount | float |  |
| arrival_date | date | Format: YYYY-MM-DD |
| charge_date | date | Format: YYYY-MM-DD |
| is_competed | int | 0-1 |
| reservation_id | int | Ref to Reservation |
| code | string |  |
| details | string |  |
| notes | string |  |
| payout_type | int | 0-1 |
| source | string |  |

### Get transaction

Slug: `get-transaction`

- Method: `GET`
- URL: `https://api-rms.hostify.com/transactions/<ID>`

### List transactions

Slug: `list-transactions`

- Method: `GET`
- URL: `https://api-rms.hostify.com/transactions`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| reservation_id (optional) | int |  | 1000 |
| listing_id (optional) | int |  | 9876 |
| filters (optional) | array | release_date
arrival_date | [
  {
   "field" : "release_date" ,
   "operator" : ">=" ,
   "value" : "2020-01-01"
  } ,
  {
   "field" : "arrival_date" ,
   "operator" : "<" ,
   "value" : "2025-12-31"
  }
] |

### Create transaction

Slug: `create-transaction`

- Method: `POST`
- URL: `https://api-rms.hostify.com/transactions`

#### Fields

| Parameter | Type | Description | Range | Example |
|---|---|---|---|---|
| reservation_id | int |  |  | 9876 |
| amount | float |  |  | 260 |
| currency | string |  |  | USD |
| charge_date | date |  |  | 2018-12-01 |
| arrival_date | date |  |  | 2018-12-03 |
| is_completed | int |  | 0-1 | 0 |
| type (optional) | string | Type should be one of the following: "accommodation", "deposit", "extra", "other", "resolution adjustment" |  | deposit |
| details (optional) | string |  |  |  |
| channel_transaction_id (optional) | string | The remote ID of the transaction from an external system (Stripe) |  | ch_123qwe345dxz |
| payment_processor_id (optional) | int | The ID of the Stripe integration in our system. |  | 2730 |

### Update transaction

Slug: `update-transaction`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/transactions/<ID>`

#### Fields

| Parameter | Type | Range | Example |
|---|---|---|---|
| amount (optional) | float |  | 560 |
| currency (optional) | string |  | USD |
| charge_date (optional) | date |  | 2018-12-01 |
| arrival_date (optional) | date |  | 2012-12-03 |
| is_completed (optional) | int | 0-1 | 1 |
| details (optional) | string |  | Charged |

### Get transaction tags

Slug: `get-transaction-tags`

- Method: `GET`
- URL: `https://api-rms.hostify.com/transactions/tags/<TRANSACTION_ID>`

### Create transaction  tag

Slug: `create-transaction-tags`

- Method: `POST`
- URL: `https://api-rms.hostify.com/transactions/tags`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| tags | {object} | id
name | [
  {
   "id" : 123
  } ,
  {
   "tag" : "New tag name"
  }
] |

### Update transaction tag

Slug: `update-transaction-tags`

- Method: `POST`
- URL: `https://api-rms.hostify.com/transactions/tags`

#### Fields

| Parameter | Type | Values | Example |
|---|---|---|---|
| transactionId | int |  | 1234 |
| tags | {object} | id
name | [
  {
   "id" : 123
  } ,
  {
   "tag" : "New tag name"
  }
] |

### Delete transactions tags

Slug: `delete-transactions-tags`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/transactions/tags/<TAG_ID>/<TRANSACTION_ID>`

## Users

Section slug: `users`

### Endpoints And Subsections

- `User object`
- `Get user`: `GET https://api-rms.hostify.com/users/<ID>`
- `List users`: `GET https://api-rms.hostify.com/users`
- `Update user`: `PUT https://api-rms.hostify.com/users/<ID>`
- `Assign role`: `POST https://api-rms.hostify.com/users/assign_role`
- `Unassign role`: `DELETE https://api-rms.hostify.com/users/unassign_role`
- `Add listing`: `POST https://api-rms.hostify.com/users/add_listing`
- `Remove listing`: `DELETE https://api-rms.hostify.com/users/remove_listing`

### User object

Slug: `user-object`

### Get user

Slug: `get-user`

- Method: `GET`
- URL: `https://api-rms.hostify.com/users/<ID>`

### List users

Slug: `list-users`

- Method: `GET`
- URL: `https://api-rms.hostify.com/users`

### Update user

Slug: `update-user`

- Method: `PUT`
- URL: `https://api-rms.hostify.com/users/<ID>`

#### Fields

| Parameter | Type | Range | Example |
|---|---|---|---|
| is_active | int | 0-1 | 1 |

### Assign role

Slug: `assign-user-role`

- Method: `POST`
- URL: `https://api-rms.hostify.com/users/assign_role`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| user_id | int | 230 |
| role | string | Listing Owner |

### Unassign role

Slug: `unassign-user-role`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/users/unassign_role`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| user_id | int | 230 |
| role | string | Listing Owner |

### Add listing

Slug: `add-user-listing`

- Method: `POST`
- URL: `https://api-rms.hostify.com/users/add_listing`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| user_id | int | 230 |
| listing_id | int | 1000 |

### Remove listing

Slug: `remove-user-listing`

- Method: `DELETE`
- URL: `https://api-rms.hostify.com/users/remove_listing`

#### Fields

| Parameter | Type | Example |
|---|---|---|
| user_id | int | 230 |
| listing_id | int | 1000 |

## Errors

Section slug: `errors`

Platform API uses the following error codes: Error code Meaning 400 Bad request 401 Unauthorized - Your API key is wrong 403 Forbidden 404 Not found 405 Method Not Allowed - You tried to access with an invalid method 500 Internal Server Error - We had a problem with our server. Try again later. 503 Service Unavailable - We're temporarially offline for maintenance. Please try again later. { "success" : false, "error" : "Error message" }

