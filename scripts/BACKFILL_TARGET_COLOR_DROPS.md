# Backfilling `drop_*` on catalog colors

Sum-drop tiers, quota, and 7-color games require every **played** catalog row to have a **complete** recipe: all of `drop_white`, `drop_black`, `drop_red`, `drop_yellow`, `drop_blue` set (use `0` for unused channels). `sum_drop_count` is the sum of those five integers.

Until recipes exist:

- `/api/game-targets` returns **409** with an explanatory message.
- Quota for logged-in users uses only colors in the current tier band with complete recipes.

## How to backfill

1. **Lab** — Match each catalog shade in the Lab UI and POST to `/api/lab/save-target-color` (or update rows in SQL) so each `target_colors` row has non-null drop columns.

2. **SQL / script** — If you have a CSV of `catalog_order` or `id` → five drop counts, run `UPDATE target_colors SET drop_white=..., ... WHERE id=...`.

3. **Re-run migrations** — `npm run db:migrate` ensures `user_progress.max_sum_drop_unlocked` exists; it does not invent recipes.

After backfill, restart the app and open the main game; `/api/game-targets` should return 7 colors (or fewer if the eligible pool has fewer than seven rows, then all are returned).
