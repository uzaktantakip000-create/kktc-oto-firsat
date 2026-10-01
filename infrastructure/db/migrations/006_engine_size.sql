-- Motor hacmi (litre): BMW 316i ile 340i gibi farklı motorlar aynı emsal havuzunda karışmasın
ALTER TABLE listings ADD COLUMN IF NOT EXISTS engine_l NUMERIC(3,1);
