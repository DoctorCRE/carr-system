create function practice_address_key(address text) returns text
language sql immutable strict parallel safe as $$
  select string_agg(case token
    when 'street' then 'st' when 'avenue' then 'ave' when 'road' then 'rd'
    when 'boulevard' then 'blvd' when 'drive' then 'dr' when 'lane' then 'ln'
    when 'court' then 'ct' when 'place' then 'pl' when 'parkway' then 'pkwy'
    when 'highway' then 'hwy' when 'suite' then 'ste' when 'unit' then 'unit'
    when 'apartment' then 'apt' when 'floor' then 'fl' else token end, ' ' order by ord)
  from regexp_split_to_table(
    regexp_replace(regexp_replace(lower(address), '\m([0-9]{5})-[0-9]{4}\M', '\1', 'g'),
      '[^a-z0-9]+', ' ', 'g'), ' +') with ordinality as parts(token,ord)
  where token <> '';
$$;
grant execute on function practice_address_key(text) to carr_reader,carr_writer;
