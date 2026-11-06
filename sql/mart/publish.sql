-- DELETE + INSERT, а не RENAME: сохраняются права, индексы и зависимые представления.
-- Читатели до коммита видят прошлую версию целиком.
delete from mart.mart_route_card;
insert into mart.mart_route_card select * from stg_route_card;
