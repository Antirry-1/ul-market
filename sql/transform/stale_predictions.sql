-- Прогноз сделки, которую модель больше не оценивает, удаляется вместе с новой версией окна:
-- сделку удалили в источнике, она ушла в карантин, стала ставкой торгов или составным маршрутом.
-- Иначе до следующего ml.score прогноз оставался бы в таблице, хотя самой сделки там уже нет.
-- Прогнозы остальных сделок окна не трогаем, их обновит ml.score.
delete from mart.ml_price_predictions p
where p.price_id in (select price_id from tmp_affected)
  and not exists (select 1 from core.v_ml_features v where v.price_id = p.price_id);
