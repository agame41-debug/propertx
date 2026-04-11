# Hostify Docs LLM Index

Короткий индекс к объединённому файлу [HOSTIFY_DOCS_COMBINED.md](HOSTIFY_DOCS_COMBINED.md).

## Читать в таком порядке

- `Authentication`
- `Integrations`
- `Listings`
- `Reservations`
- `Transactions`
- `Accounting`
- `Custom Fields`
- `Search`
- `Errors`

## Карта разделов

- `Introduction`: standalone section
- `Authentication`: standalone section
- `Accounting`: 7 subsection(s)
  - `Get invoice`: `GET https://api-rms.hostify.com/invoices/<ID>`
  - `List invoices`: `GET https://api-rms.hostify.com/invoices`
  - `Change invoice external fields`: `POST https://api-rms.hostify.com/invoices/set_external_data`
  - `Get company`: `GET https://api-rms.hostify.com/companies/<ID>`
  - `List companies`: `GET https://api-rms.hostify.com/companies`
  ... and 2 more
- `Calendar`: 8 subsection(s)
  - `Get calendar`: `GET https://api-rms.hostify.com/calendar`
  - `Get single calendar`: `GET https://api-rms.hostify.com/calendar/<ID>`
  - `Update calendar`: `PUT https://api-rms.hostify.com/calendar`
  - `Bulk update calendar (single listing)`: `PUT https://api-rms.hostify.com/calendar/bulk_listings/<LISTING_ID>`
  - `Bulk update calendar (multiple listings)`: `PUT https://api-rms.hostify.com/calendar/bulk_listings`
  ... and 2 more
- `Custom stay`: 3 subsection(s)
  - `Get custom stay`: `GET https://api-rms.hostify.com/custom_stay`
  - `Set custom stay`: `POST https://api-rms.hostify.com/custom_stay`
- `CTA/CTD Restrictions`: 3 subsection(s)
  - `Get CTA/CTD restrictions`: `GET https://api-rms.hostify.com/cta_ctd`
  - `Set CTA/CTD restrictions`: `POST https://api-rms.hostify.com/cta_ctd`
- `Guests`: 3 subsection(s)
  - `Get guest`: `GET https://api-rms.hostify.com/guests/<ID>`
  - `List guests`: `GET https://api-rms.hostify.com/guests`
- `Inbox`: 13 subsection(s)
  - `Get thread`: `GET https://api-rms.hostify.com/inbox/<ID>`
  - `List threads`: `GET https://api-rms.hostify.com/inbox`
  - `Assign thread`: `POST https://api-rms.hostify.com/inbox/assignee`
  - `Post a reply`: `POST https://api-rms.hostify.com/inbox/reply`
  - `Post an image reply`: `POST https://api-rms.hostify.com/inbox/reply_image`
  ... and 6 more
- `Integrations`: 3 subsection(s)
  - `Get integration`: `GET https://api-rms.hostify.com/integrations/<ID>`
  - `List integration`: `GET https://api-rms.hostify.com/integrations`
- `Listings`: 29 subsection(s)
  - `Get listing`: `GET https://api-rms.hostify.com/listings/<ID>`
  - `Update listing`: `POST https://api-rms.hostify.com/listings/update`
  - `List listings`: `GET https://api-rms.hostify.com/listings`
  - `List children`: `GET https://api-rms.hostify.com/listings/children/<ID>`
  - `Get available listings`: `GET https://api-rms.hostify.com/listings/available`
  ... and 23 more
- `Create Listing`: 6 subsection(s)
  - `Location`: `POST https://api-rms.hostify.com/listings/process_location`
  - `Layout`: `POST https://api-rms.hostify.com/listings/process_layout`
  - `Amenities`: `POST https://api-rms.hostify.com/listings/process_amenities`
  - `Translations`: `POST https://api-rms.hostify.com/listings/process_translations`
  - `Booking restrictions`: `POST https://api-rms.hostify.com/listings/process_booking_restrictions`
  ... and 1 more
- `Reservations`: 10 subsection(s)
  - `Get reservation`: `GET https://api-rms.hostify.com/reservations/<ID>`
  - `List reservations`: `GET https://api-rms.hostify.com/reservations`
  - `Create reservation`: `POST https://api-rms.hostify.com/reservations`
  - `Update reservation`: `PUT https://api-rms.hostify.com/reservations/<ID>`
  - `Custom fields`: `GET https://api-rms.hostify.com/reservations/custom_fields/<RESERVATION_ID>`
  ... and 4 more
- `Push Notifications using Amazon SNS`: 4 subsection(s)
  - `Get notification`: `GET https://api-rms.hostify.com/webhooks_v2/<ID>`
  - `List notifications`: `GET https://api-rms.hostify.com/webhooks_v2`
  - `Delete Notification`: `DELETE https://api-rms.hostify.com/webhooks_v2/<ID>`
- `Custom Fields`: 6 subsection(s)
  - `Get Custom Field`: `GET https://api-rms.hostify.com/custom_fields/<ID>`
  - `List Custom Fields`: `GET https://api-rms.hostify.com/custom_fields`
  - `Create Custom Field`: `POST https://api-rms.hostify.com/custom_fields`
  - `Update Custom Field`: `POST https://api-rms.hostify.com/custom_fields/update`
  - `Delete Custom Field`: `DELETE https://api-rms.hostify.com/custom_fields`
  ... and 1 more
- `Seasonal Promotions (In BETA not available)`: 9 subsection(s)
  - `Get Promotion`: `GET https://api-rms.hostify.com/seasonal_promotions/<ID>`
  - `List Promotions`: `GET https://api-rms.hostify.com/seasonal_promotions`
  - `Create Promotion`: `POST https://api-rms.hostify.com/seasonal_promotions`
  - `Update Promotion`: `PUT https://api-rms.hostify.com/seasonal_promotions/<ID>`
  - `Delete Promotion`: `DELETE https://api-rms.hostify.com/seasonal_promotions/<ID>`
  ... and 3 more
- `Reviews`: 3 subsection(s)
  - `Get review`: `GET https://api-rms.hostify.com/reviews/<ID>`
  - `List reviews`: `GET https://api-rms.hostify.com/reviews`
- `Search`: standalone section
- `Transactions`: 9 subsection(s)
  - `Get transaction`: `GET https://api-rms.hostify.com/transactions/<ID>`
  - `List transactions`: `GET https://api-rms.hostify.com/transactions`
  - `Create transaction`: `POST https://api-rms.hostify.com/transactions`
  - `Update transaction`: `PUT https://api-rms.hostify.com/transactions/<ID>`
  - `Get transaction tags`: `GET https://api-rms.hostify.com/transactions/tags/<TRANSACTION_ID>`
  ... and 3 more
- `Users`: 8 subsection(s)
  - `User object`
  - `Get user`: `GET https://api-rms.hostify.com/users/<ID>`
  - `List users`: `GET https://api-rms.hostify.com/users`
  - `Update user`: `PUT https://api-rms.hostify.com/users/<ID>`
  - `Assign role`: `POST https://api-rms.hostify.com/users/assign_role`
  ... and 3 more
- `Errors`: standalone section

## Что получилось

- Top-level sections: `20`
- Unique endpoints extracted: `109`
- Master snapshot можно читать только при необходимости, основной вход теперь через markdown
