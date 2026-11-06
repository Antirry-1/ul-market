-- DELETE + INSERT вместо upsert: upsert не убрал бы сделку, которая исчезла из источника,
-- попала в карантин или, наоборот, покинула его после исправления.
delete from dq.dq_quarantine_prices  where price_id in (select price_id from tmp_affected);
delete from core.bridge_price_body_group where price_id in (select price_id from tmp_affected);
delete from core.fact_price_date     where price_id in (select price_id from tmp_affected);
delete from core.fact_price          where price_id in (select price_id from tmp_affected);
