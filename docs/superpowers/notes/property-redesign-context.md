# Property page redesign — session context

## Что делаем

Редизайн страницы объекта `/property/{slug}/{year}/{month}` по эталону из `~/Downloads/315n/property/` (HTML/CSS + 5 React/JSX компонентов как референс). Параллельно потом — небольшой редизайн дашборда (отдельной задачей, не сейчас).

Аврора-фон + cosmos-частицы + сайдбар-theme-toggle сохраняются. Никакого React в продакшене — порт в Jinja + vanilla JS + HTMX (текущая архитектура).

## Где находимся в брейншторме

Чек-лист `superpowers:brainstorming` — пройдены пункты 1–6:

1. ✅ Explore project + reference design context
2. ✅ Visual companion (skipped — дизайн уже визуально готов)
3. ✅ Clarifying questions (Q1–Q7 + Q-A…Q-4)
4. ✅ Approaches (выбран phased)
5. ✅ Design sections D.1–D.6 + E (все одобрены пользователем)
6. ✅ Спека записана: [docs/superpowers/specs/2026-04-26-property-page-redesign-design.md](../specs/2026-04-26-property-page-redesign-design.md), commit `a6b5a77`
7. 🟡 **Сейчас**: ожидание review со стороны пользователя
8. ⏳ Дальше: handoff в `superpowers:writing-plans` для детального плана имплементации

## Ключевые решения (locked)

| Тема | Решение |
|---|---|
| Phasing | 3 PR: **Phase 1** визуал-only / **Phase 2** Vyloučit / **Phase 3** Přesunout (← / →). Split — out of scope. |
| KPI «Zisk» | Универсальный label для всех `client_type`. Формула: rentero = `gross − expenses − vat_balance` (вариант β); klient/z_klient = `client_payout_after_expenses_czk`. |
| DPH-summary card | Только для `client.platce_dph == 1`. |
| Action-кнопки в expanded row | 4 кнопки: `← MM/YYYY`, `MM/YYYY →`, `Vyloučit`, `Úprava`, `Otevřít panel →`. В Phase 1 — Move/Vyloučit `disabled`. |
| Úprava форма | Inline под action-row внутри expanded row (вариант i). |
| Production-only UI | Notify-strips стеком над KPI (вариант A). |
| Технология | Jinja + vanilla JS, никаких новых рантаймов (вариант A). |
| Edit-expense форма | Та же calculator-strip что и add (вариант A). Trash-иконка рядом с pencil (ii). |
| Mobile | ≤1100 tablet + ≤640 phone, KPI 2×2, резервации в card-list (вариант B). |
| Excel-export кнопка | Не нужна, Excel в проекте не используется. |
| Schema migration для expenses | Идемпотентный ALTER TABLE; legacy записи остаются с `vat_rate=NULL`, рендерятся как «—». |
| Категории | Seed 6 default'ов только если таблица пуста. |
| Status mapping | CHYBÍ_V_HOSTIFY → PROBLÉMY/err; ZRUŠENO → EXCLUDED/mute. |
| Backend `vat_output_czk` | Алиас на существующее `dph_prefakturace_klient_czk`. |
| Reason переноса | Auto-generated, без UI. |
| Категория-цвет | Mapping в Jinja-словаре. |
| DPH-валидация в форме | Read all 3 (gross/net/dph) + canonical recompute + validate с EPSILON=0.02 Kč; persist canonical. |

## Следующий шаг

Пользователь читает спеку и говорит «ок» / «правки» — после этого вызывается `superpowers:writing-plans` для детального плана Phase 1.

## Памятка по продолжению

- Спека: `docs/superpowers/specs/2026-04-26-property-page-redesign-design.md`
- Эталонный мок: `/Users/nikitashlykov/Downloads/315n/property/` (HTML, CSS, 5 JSX, data.js, app.jsx)
- Скриншоты от пользователя: 4 шт. (Header+KPI+rezervace, expanded row, breakdown+výdaje, calculator-форма add)
- Текущая прод-страница: `templates/property.html` + 7 партиалов в `templates/partials/property_*.html` + `report/routes/property_routes.py`
- Worktree: `claude/happy-boyd-d935b9` (на main = unchanged)
